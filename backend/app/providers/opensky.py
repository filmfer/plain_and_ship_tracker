"""OpenSky Network aircraft provider (``APP_MODE=production`` only).

Implements :class:`~app.providers.base.AircraftProvider` against the documented
OpenSky REST API. Every behaviour is taken from the official documentation
(``openskynetwork.github.io/opensky-api``) and the official Python client -
nothing is invented:

* ``GET {base}/states/all`` with optional ``lamin``/``lamax``/``lomin``/
  ``lomax`` bounding-box parameters; response ``{"time": int, "states": [...]}``
  using the documented 17-field state-vector layout.
* OAuth2 **client credentials** only (basic auth was removed):
  ``POST {token_url}`` as ``x-www-form-urlencoded`` with
  ``grant_type=client_credentials``, ``client_id`` and ``client_secret``;
  tokens expire after 30 minutes and are refreshed 30 s early; a ``401``
  response means the token expired - fetch a new one and retry; requests
  carry ``Authorization: Bearer``.
* Rate limits: the official client enforces >= 5 s between ``/states/all``
  calls when authenticated and >= 10 s anonymously; credits are 400/day
  anonymous and 4,000/day standard (a global query costs 4 credits); when
  credits run out the API answers ``429`` with
  ``X-Rate-Limit-Retry-After-Seconds``.
* Field units match the internal model: velocity and vertical_rate in m/s,
  altitudes in metres, true_track in degrees clockwise from north.

Credentials stay server-side and are never logged. Demo mode never constructs
this provider (see :func:`app.runtime.build_providers`); it is fully testable
offline by injecting an ``httpx.MockTransport``.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

import httpx

from app.config import Settings
from app.models import DataQuality, Entity, EntityType, Position, Source
from app.providers.base import AircraftProvider

logger = logging.getLogger(__name__)

# Documented state-vector layout (REST docs; official client StateVector.keys).
_ICAO24, _CALLSIGN, _COUNTRY, _TIME_POSITION, _LAST_CONTACT, _LONGITUDE, \
    _LATITUDE, _BARO_ALTITUDE, _ON_GROUND, _VELOCITY, _TRUE_TRACK, \
    _VERTICAL_RATE, _SENSORS, _GEO_ALTITUDE, _SQUAWK, _SPI, \
    _POSITION_SOURCE = range(17)

TOKEN_REFRESH_MARGIN_S = 30.0        # docs: refresh 30 s before expiry
DEFAULT_TOKEN_EXPIRES_IN_S = 1800.0  # docs: "tokens expire after 30 minutes"
MIN_INTERVAL_AUTHENTICATED_S = 5.0   # official client: get_states(10, 5, ...)
MIN_INTERVAL_ANONYMOUS_S = 10.0
MAX_SIGNAL_AGE_GOOD_S = 15.0         # docs: positions older than 15 s omitted
MAX_SIGNAL_AGE_DEGRADED_S = 60.0
DEFAULT_429_WAIT_S = 60.0            # fallback when no Retry-After header


class _TokenManager:
    """OAuth2 client-credentials token holder (documented flow)."""

    def __init__(
        self,
        token_url: str,
        client_id: str,
        client_secret: str,
        *,
        timeout_s: float,
        transport: Optional[httpx.BaseTransport] = None,
        now: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._token_url = token_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._timeout_s = timeout_s
        self._transport = transport
        self._now = now or datetime.now
        self._token: Optional[str] = None
        self._expires_at: Optional[datetime] = None

    def invalidate(self) -> None:
        """Drop the current token (documented 401 handling)."""
        self._token = None
        self._expires_at = None

    def headers(self) -> dict:
        """Return a valid ``Authorization: Bearer`` header dict."""
        return {"Authorization": f"Bearer {self._token_or_refresh()}"}

    def _token_or_refresh(self) -> str:
        if (
            self._token is not None
            and self._expires_at is not None
            and self._now() < self._expires_at
        ):
            return self._token
        return self._refresh()

    def _refresh(self) -> str:
        with httpx.Client(timeout=self._timeout_s, transport=self._transport) as client:
            response = client.post(
                self._token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
            )
            response.raise_for_status()
            payload = response.json()
        self._token = payload["access_token"]
        expires_in = float(payload.get("expires_in", DEFAULT_TOKEN_EXPIRES_IN_S))
        # Official client/documented example: refresh margin before expiry.
        self._expires_at = self._now() + timedelta(
            seconds=expires_in - TOKEN_REFRESH_MARGIN_S
        )
        logger.debug("OpenSky OAuth2 token acquired (expires_in=%ss)", int(expires_in))
        return self._token


class OpenSkyProvider(AircraftProvider):
    """Normalized OpenSky adapter: TTL cache, documented throttling, retries.

    All network access happens inside :meth:`_fetch`, driven by
    :meth:`_ensure_fresh`:

    * fresh cache (age < ``opensky_cache_ttl_s``)         -> no request;
    * inside the documented throttle interval             -> no request;
    * inside a 429 cooldown (``Retry-After``)             -> no request;
    * otherwise one bounded HTTP attempt sequence, then the cache (or an
      empty result on first failure - the provider never raises).
    """

    def __init__(
        self,
        settings: Settings,
        *,
        transport: Optional[httpx.BaseTransport] = None,
        now: Optional[Callable[[], float]] = None,
        sleep: Optional[Callable[[float], None]] = None,
        token_now: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._base_url = settings.opensky_base_url.rstrip("/")
        self._timeout_s = settings.opensky_timeout_s
        self._transport = transport
        self._now = now or time.monotonic
        self._sleep = sleep or time.sleep
        self._ttl = max(settings.opensky_cache_ttl_s, 0.0)
        self._max_retries = max(settings.opensky_max_retries, 0)
        self._backoff_base = max(settings.opensky_backoff_base_s, 0.0)
        self._params = _parse_bbox(settings.opensky_bbox)
        self._client_id = settings.opensky_client_id.strip()
        self._client_secret = settings.opensky_client_secret
        # Documented floors always apply; the setting may only raise them.
        floor = (
            MIN_INTERVAL_AUTHENTICATED_S
            if self._client_id
            else MIN_INTERVAL_ANONYMOUS_S
        )
        self._min_interval = max(settings.opensky_min_request_interval_s, floor)
        self._token_manager: Optional[_TokenManager] = None
        if self._client_id:
            self._token_manager = _TokenManager(
                settings.opensky_token_url,
                self._client_id,
                self._client_secret,
                timeout_s=self._timeout_s,
                transport=transport,
                now=token_now,
            )
        self._entities: dict[str, Entity] = {}
        self._positions: dict[str, Position] = {}
        self._fetched_at: Optional[float] = None
        self._last_attempt: Optional[float] = None
        self._cooldown_until: float = 0.0

    # -- provider interface --------------------------------------------------

    def get_entities(self) -> list[Entity]:
        self._ensure_fresh()
        return list(self._entities.values())

    def get_positions(self) -> list[Position]:
        self._ensure_fresh()
        return list(self._positions.values())

    # -- freshness gates -----------------------------------------------------

    def _ensure_fresh(self) -> None:
        now = self._now()
        if self._fetched_at is not None and now - self._fetched_at < self._ttl:
            return  # cache is fresh
        if now < self._cooldown_until:
            return  # HTTP 429 cooldown: serve cache until Retry-After passes
        if (
            self._last_attempt is not None
            and now - self._last_attempt < self._min_interval
        ):
            return  # documented client-side throttle
        self._last_attempt = now
        self._fetch()

    # -- network -------------------------------------------------------------

    def _fetch(self) -> None:
        """GET /states/all with documented retry/backoff; update the cache."""
        attempts = 1 + self._max_retries
        for attempt in range(attempts):
            headers: dict = {}
            try:
                if self._token_manager is not None:
                    headers = self._token_manager.headers()  # refreshes if expired
                with httpx.Client(
                    timeout=self._timeout_s, transport=self._transport
                ) as client:
                    response = client.get(
                        f"{self._base_url}/states/all",
                        params=self._params or None,
                        headers=headers,
                    )
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                # Never log request bodies or credentials - type + message only.
                logger.warning(
                    "OpenSky request failed (%s: %s)", type(exc).__name__, exc
                )
                if attempt + 1 < attempts:
                    self._backoff(attempt)
                    continue
                return
            if (
                response.status_code == 401
                and self._token_manager is not None
                and attempt + 1 < attempts
            ):
                # Docs: 401 == token expired -> request a new one and retry.
                self._token_manager.invalidate()
                continue
            if response.status_code == 429:
                self._enter_cooldown(response)  # no sleep, no retry
                return
            if response.is_success:
                try:
                    payload = response.json()
                except ValueError:
                    logger.warning("OpenSky returned invalid JSON; keeping cache")
                    return
                self._adopt(payload)
                return
            if response.status_code >= 500 and attempt + 1 < attempts:
                self._backoff(attempt)
                continue
            logger.warning(
                "OpenSky API answered HTTP %s (not retrying)", response.status_code
            )
            return

    def _backoff(self, attempt: int) -> None:
        delay = self._backoff_base * (2 ** attempt)
        if delay > 0:
            self._sleep(delay)

    def _enter_cooldown(self, response: httpx.Response) -> None:
        """Arm a no-request cooldown from the documented 429 headers."""
        raw = response.headers.get("X-Rate-Limit-Retry-After-Seconds") or (
            response.headers.get("Retry-After")
        )
        try:
            wait = float(raw) if raw is not None else DEFAULT_429_WAIT_S
        except ValueError:
            wait = DEFAULT_429_WAIT_S
        self._cooldown_until = self._now() + max(wait, 0.0)
        logger.warning(
            "OpenSky credits exhausted (HTTP 429); pausing requests for %.0fs",
            wait,
        )

    # -- normalization -------------------------------------------------------

    def _adopt(self, payload: dict) -> None:
        """Replace the cache from a documented states/all response."""
        entities: dict[str, Entity] = {}
        positions: dict[str, Position] = {}
        response_time = payload.get("time")
        for row in payload.get("states") or []:
            if not isinstance(row, (list, tuple)) or not row:
                continue
            icao24 = row[_ICAO24] if len(row) > _ICAO24 else None
            if not isinstance(icao24, str) or not icao24:
                continue  # identity is required for a normalized entity
            raw_callsign = row[_CALLSIGN] if len(row) > _CALLSIGN else None
            callsign = (
                raw_callsign.strip()
                if isinstance(raw_callsign, str) and raw_callsign.strip()
                else None
            )
            entity = Entity(
                id=f"aircraft:{icao24}",
                type=EntityType.aircraft,
                identifier=callsign or icao24,
                source=Source.opensky,
                callsign=callsign,
                icao24=icao24,
            )
            entities[entity.id] = entity
            position = self._position_for(row, entity.id, response_time)
            if position is not None:
                positions[entity.id] = position
        self._entities = entities
        self._positions = positions
        self._fetched_at = self._now()
        logger.debug(
            "OpenSky refresh: %d aircraft, %d positions",
            len(entities),
            len(positions),
        )

    def _position_for(
        self, row: list, entity_id: str, response_time
    ) -> Optional[Position]:
        latitude = _number(row, _LATITUDE)
        longitude = _number(row, _LONGITUDE)
        if latitude is None or longitude is None:
            return None  # docs: lat/lon may be null -> no position this cycle
        if not (-90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0):
            return None  # invalid coordinates are dropped, never invented
        timestamp = _timestamp(row, response_time)
        if timestamp is None:
            return None
        last_contact = _number(row, _LAST_CONTACT)
        signal_age: Optional[float] = None
        if last_contact is not None and isinstance(response_time, (int, float)):
            signal_age = max(0.0, float(response_time) - last_contact)
        speed = _number(row, _VELOCITY)
        if speed is not None and speed < 0:
            speed = None  # "over ground in m/s" cannot be negative
        track = _number(row, _TRUE_TRACK)
        heading = (track % 360.0) if track is not None else None
        # Geometric altitude preferred, barometric as fallback (both metres).
        altitude = _number(row, _GEO_ALTITUDE)
        if altitude is None:
            altitude = _number(row, _BARO_ALTITUDE)
        if signal_age is None:
            quality = DataQuality.unknown
        elif signal_age <= MAX_SIGNAL_AGE_GOOD_S:
            quality = DataQuality.good
        elif signal_age <= MAX_SIGNAL_AGE_DEGRADED_S:
            quality = DataQuality.degraded
        else:
            quality = DataQuality.poor
        return Position(
            entity_id=entity_id,
            timestamp=timestamp,
            latitude=latitude,
            longitude=longitude,
            source=Source.opensky,
            altitude=altitude,
            speed=speed,
            heading=heading,
            course=heading,  # true_track is the course over ground
            vertical_rate=_number(row, _VERTICAL_RATE),
            signal_age=signal_age,
            quality=quality,
        )


def _number(row: list, index: int) -> Optional[float]:
    """Extract a numeric state-vector field (null-safe, bool excluded)."""
    value = row[index] if len(row) > index else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _timestamp(row: list, response_time) -> Optional[datetime]:
    """time_position, else last_contact, else the response time (documented)."""
    candidates = (
        row[_TIME_POSITION] if len(row) > _TIME_POSITION else None,
        row[_LAST_CONTACT] if len(row) > _LAST_CONTACT else None,
        response_time,
    )
    for candidate in candidates:
        if isinstance(candidate, bool) or not isinstance(candidate, (int, float)):
            continue
        return datetime.fromtimestamp(float(candidate), tz=timezone.utc)
    return None


def _parse_bbox(raw: str) -> dict:
    """Parse ``lamin,lamax,lomin,lomax`` (official client tuple order).

    Invalid values are ignored with a warning so a bad setting can never
    prevent startup (mirrors the graceful-fallback philosophy of the app).
    """
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    if not parts:
        return {}
    try:
        values = [float(part) for part in parts]
    except ValueError:
        values = []
    valid = (
        len(values) == 4
        and -90.0 <= values[0] <= 90.0
        and -90.0 <= values[1] <= 90.0
        and -180.0 <= values[2] <= 180.0
        and -180.0 <= values[3] <= 180.0
        and values[0] <= values[1]
        and values[2] <= values[3]
    )
    if not valid:
        logger.warning(
            "Ignoring invalid OPENSKY_BBOX %r (expected 'lamin,lamax,lomin,lomax')",
            raw,
        )
        return {}
    lamin, lamax, lomin, lomax = values
    return {"lamin": lamin, "lamax": lamax, "lomin": lomin, "lomax": lomax}


def build_opensky_provider(settings: Settings) -> OpenSkyProvider:
    """Construct the OpenSky adapter (production wiring; never used in demo)."""
    return OpenSkyProvider(settings)
