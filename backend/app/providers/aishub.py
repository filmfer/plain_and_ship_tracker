"""AISHub AIS provider (``APP_MODE=production`` only).

Implements :class:`~app.providers.base.AISProvider` against the documented
AISHub webservice. Compliance notes (from aishub.net/api and the Terms of
Use published on aishub.net/join-us):

* Endpoint: ``GET https://data.aishub.net/ws.php`` with ``username``,
  ``output=json``, ``format=1`` (human-readable degrees/decimals) and
  optional ``latmin``/``latmax``/``lonmin``/``lonmax`` bounds.
* Rate limit (quoted): *"Don't access the webservice more frequently than
  once per minute! The web service will return nothing if executed more
  frequently!"* - enforced as a hard 60 s floor (settings may only raise
  it); an empty or undecodable reply is treated as that response and is
  **never** retried in a tight loop.
* Access terms (quoted): AISHub is contributor-based; a username is issued
  to members streaming their own raw AIS feed, and synthesized, scraped or
  publicly-scraped feeds are prohibited. This client never creates access by
  other means: without a legitimately obtained username it is not
  constructed and ships fall back to mock data. We only consume - we never
  contribute or fake feeds.
* Credentials: the username travels in the query string (the documented
  protocol); this module never logs it, and httpx's URL logging is pinned to
  WARNING (see below) so it cannot leak into application logs.
* Units: LATITUDE/LONGITUDE are WGS-84 degrees; SOG is knots (AIS standard)
  converted to m/s for internal consistency with the mock providers; COG and
  HEADING are degrees (HEADING 511 = "not available" -> None); ``TIME`` is a
  GMT string parsed to UTC, with receipt time as the documented fallback.
* ``source = ais`` on every entity and position (rule #29).

Demo/local never construct this provider (see
:func:`app.runtime.build_providers`); tests use mocked responses only.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Callable, Optional

import httpx

from app.config import Settings
from app.models import DataQuality, Entity, EntityType, Position, Source
from app.providers.base import AISProvider

# The webservice authenticates via ``username`` in the query string and httpx
# logs full request URLs at INFO - keep it at WARNING so the membership
# credential can never reach application logs.
logging.getLogger("httpx").setLevel(logging.WARNING)

# The webservice authenticates via ``username`` in the query string and httpx
# logs full request URLs at INFO - keep it at WARNING so the membership
# credential can never reach application logs.
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

POLL_FLOOR_S = 60.0                 # documented: never faster than once/minute
TIME_FORMAT = "%Y-%m-%d %H:%M:%S GMT"  # documented human-readable TIME
KNOT_TO_MS = 0.514444               # AIS SOG is knots; internal unit is m/s
SOG_UNAVAILABLE_KN = 102.3          # AIS "not available" SOG encoding
MAX_SIGNAL_AGE_GOOD_S = 120.0       # 60 s downsampling + 60 s poll
MAX_SIGNAL_AGE_DEGRADED_S = 600.0
DEFAULT_429_WAIT_S = 60.0           # fallback when no Retry-After header


class AishubProvider(AISProvider):
    """Normalized AISHub adapter: TTL cache, documented 60 s throttle, retries.

    ``get_entities``/``get_positions`` serve the cache and refresh it at most
    once per configured interval (never below the documented 60 s floor);
    the provider never raises and never logs credentials.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        transport: Optional[httpx.BaseTransport] = None,
        now: Optional[Callable[[], float]] = None,
        sleep: Optional[Callable[[float], None]] = None,
        wall_now: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._base_url = settings.aishub_base_url
        self._timeout_s = settings.aishub_timeout_s
        self._transport = transport
        self._now = now or time.monotonic
        self._sleep = sleep or time.sleep
        self._wall_now = wall_now or (lambda: datetime.now(timezone.utc))
        self._ttl = max(settings.aishub_cache_ttl_s, 0.0)
        self._max_retries = max(settings.aishub_max_retries, 0)
        self._backoff_base = max(settings.aishub_backoff_base_s, 0.0)
        # The documented once-per-minute rule is a hard floor.
        self._min_interval = max(
            settings.aishub_min_request_interval_s, POLL_FLOOR_S
        )
        params: dict = {
            "username": settings.aishub_username,
            "output": "json",
            "format": 1,  # human-readable values (degrees, decimals)
            "compress": 0,
        }
        params.update(_bbox_params(settings.aishub_bbox))
        self._params = params
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
            return  # rate-limit cooldown: serve cache
        if (
            self._last_attempt is not None
            and now - self._last_attempt < self._min_interval
        ):
            return  # documented once-per-minute throttle
        self._last_attempt = now
        self._fetch()

    # -- network -------------------------------------------------------------

    def _fetch(self) -> None:
        """GET ws.php with retry/backoff; update the cache on valid JSON."""
        attempts = 1 + self._max_retries
        for attempt in range(attempts):
            try:
                with httpx.Client(
                    timeout=self._timeout_s, transport=self._transport
                ) as client:
                    response = client.get(self._base_url, params=self._params)
            except httpx.HTTPError as exc:
                # Type only: httpx messages embed the URL (and username).
                logger.warning("AISHub request failed (%s)", type(exc).__name__)
                if attempt + 1 < attempts:
                    self._backoff(attempt)
                    continue
                return
            if response.status_code == 429:
                self._enter_cooldown(response)  # no sleep, no retry
                return
            if response.is_success:
                try:
                    payload = response.json()
                except ValueError:
                    # Documented rate-limit behaviour: the service "returns
                    # nothing" when polled too fast - keep the cache, do not
                    # hammer the endpoint with retries.
                    logger.warning(
                        "AISHub returned an empty or undecodable reply; "
                        "keeping the previous cache"
                    )
                    return
                self._adopt(payload)
                return
            if response.status_code >= 500 and attempt + 1 < attempts:
                self._backoff(attempt)
                continue
            logger.warning(
                "AISHub answered HTTP %s (not retrying)", response.status_code
            )
            return

    def _backoff(self, attempt: int) -> None:
        delay = self._backoff_base * (2 ** attempt)
        if delay > 0:
            self._sleep(delay)

    def _enter_cooldown(self, response: httpx.Response) -> None:
        raw = response.headers.get("Retry-After")
        try:
            wait = float(raw) if raw is not None else DEFAULT_429_WAIT_S
        except ValueError:
            wait = DEFAULT_429_WAIT_S
        self._cooldown_until = self._now() + max(wait, 0.0)
        logger.warning(
            "AISHub throttling (HTTP 429); pausing requests for %.0fs", wait
        )

    # -- normalization -------------------------------------------------------

    def _adopt(self, payload) -> None:
        """Replace the cache from a documented ws.php JSON reply."""
        if isinstance(payload, dict):
            rows = [payload]  # the docs also show a single-vessel object
        elif isinstance(payload, list):
            rows = payload
        else:
            logger.warning("Unexpected AISHub payload type; keeping cache")
            return
        entities: dict[str, Entity] = {}
        positions: dict[str, Position] = {}
        for row in rows:
            if not isinstance(row, dict) or row.get("MMSI") is None:
                continue
            mmsi = str(row["MMSI"])
            name = str(row.get("NAME") or "").strip() or None
            callsign = str(row.get("CALLSIGN") or "").strip() or None
            try:
                imo_number = int(row.get("IMO") or 0)
            except (TypeError, ValueError):
                imo_number = 0
            entity = Entity(
                id=f"ship:{mmsi}",
                type=EntityType.ship,
                identifier=mmsi,  # rule #19: ship lookup by MMSI
                source=Source.ais,
                name=name,
                callsign=callsign,
                mmsi=mmsi,
                imo=f"IMO{imo_number}" if imo_number > 0 else None,
            )
            entities[entity.id] = entity
            position = self._position_for(row, entity.id)
            if position is not None:
                positions[entity.id] = position
        self._entities = entities
        self._positions = positions
        self._fetched_at = self._now()
        logger.debug(
            "AISHub refresh: %d vessels, %d positions",
            len(entities),
            len(positions),
        )

    def _position_for(self, row: dict, entity_id: str) -> Optional[Position]:
        latitude = _number(row.get("LATITUDE"))
        longitude = _number(row.get("LONGITUDE"))
        if latitude is None or longitude is None:
            return None  # no position in this reply
        if not (-90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0):
            return None  # invalid coordinates are dropped, never invented
        timestamp, signal_age = self._timestamp(row)
        sog = _number(row.get("SOG"))
        speed: Optional[float] = None
        if sog is not None and 0.0 <= sog < SOG_UNAVAILABLE_KN:
            speed = sog * KNOT_TO_MS  # AIS SOG is knots; internal unit is m/s
        cog = _number(row.get("COG"))
        course = (
            cog % 360.0 if cog is not None and 0.0 <= cog <= 360.0 else None
        )
        heading_raw = _number(row.get("HEADING"))
        # 511 is the AIS "not available" encoding for true heading.
        heading = (
            heading_raw % 360.0
            if heading_raw is not None and 0.0 <= heading_raw <= 360.0
            else None
        )
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
            source=Source.ais,
            speed=speed,
            heading=heading,
            course=course,
            signal_age=signal_age,
            quality=quality,
        )

    def _timestamp(self, row: dict) -> tuple[datetime, Optional[float]]:
        """Parse the documented GMT ``TIME`` string; else use receipt time."""
        now = self._wall_now()
        raw = row.get("TIME")
        if isinstance(raw, str):
            try:
                parsed = datetime.strptime(raw, TIME_FORMAT).replace(
                    tzinfo=timezone.utc
                )
                return parsed, max(0.0, (now - parsed).total_seconds())
            except ValueError:
                pass
        return now, None  # receipt time; age unknown -> quality unknown


def _number(value) -> Optional[float]:
    """Extract a numeric field (null-safe, bool excluded)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _bbox_params(raw: str) -> dict:
    """Parse ``lamin,lamax,lomin,lomax`` into the documented query names."""
    parts = [part.strip() for part in raw.split(",") if part.strip()]
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
        if raw.strip():
            logger.warning(
                "Ignoring invalid AISHUB_BBOX %r "
                "(expected 'lamin,lamax,lomin,lomax')",
                raw,
            )
        return {}
    lamin, lamax, lomin, lomax = values
    return {"latmin": lamin, "latmax": lamax, "lonmin": lomin, "lonmax": lomax}


