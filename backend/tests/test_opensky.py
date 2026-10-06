"""Tests for the OpenSky adapter - every HTTP response is mocked (no network)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.clock import build_clock
from app.config import Settings
from app.models import DataQuality, EntityType, Source
from app.providers.base import AircraftProvider
from app.providers.mock_aircraft import MockAircraftProvider
from app.providers.mock_ship import MockShipProvider
from app.providers.opensky import OpenSkyProvider, build_opensky_provider
from app.runtime import build_providers

# Documented example values (state vector example: epoch 1458987225 =
# 2016-03-26 10:13:45 UTC, 230 m/s, true track 30 deg).
NOW = 1458987225
SECRET = "super-secret-value"


def make_settings(**overrides) -> Settings:
    kwargs = {
        "_env_file": None,
        "database_url": "",
        "opensky_max_retries": 2,
        "opensky_backoff_base_s": 1.0,
        "opensky_timeout_s": 3.0,
    }
    kwargs.update(overrides)
    return Settings(**kwargs)


def states_payload() -> dict:
    return {
        "time": NOW,
        "states": [
            # 17 documented fields; geo_altitude (index 13) set, speed/track/...
            ["c0ffee", "CONAIR  ", "Germany", 1458987224, 1458987224,
             1.28, 51.89, 11000.0, False, 230.0, 30.0, 1.5, None,
             11500.0, "1000", False, 0],
            # Null position (documented) -> entity without a position.
            ["abc123", None, "Portugal", None, 1458987200,
             None, None, None, False, None, None, None, None,
             None, None, False, 0],
        ],
    }


def token_response() -> httpx.Response:
    return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 1800})


class FakeMonotonic:
    """Controllable monotonic clock (seconds)."""

    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeTokenClock:
    """Controllable datetime clock for OAuth2 token expiry."""

    def __init__(self, start: datetime) -> None:
        self.value = start

    def __call__(self) -> datetime:
        return self.value


class Recorder:
    """MockTransport handler wrapper recording every request and sleep."""

    def __init__(self, handler) -> None:
        self.handler = handler
        self.states_requests: list[httpx.Request] = []
        self.token_requests: list[httpx.Request] = []
        self.sleeps: list[float] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            self.token_requests.append(request)
            return self.handler(request, token=True)
        self.states_requests.append(request)
        return self.handler(request, token=False)


def make_provider(handler, **overrides):
    """OpenSkyProvider wired to a scripted MockTransport (fully offline)."""
    recorder = Recorder(handler)
    mono = FakeMonotonic()
    token_clock = FakeTokenClock(datetime(2016, 3, 26, 11, 13, 44, tzinfo=timezone.utc))
    provider = OpenSkyProvider(
        make_settings(**overrides),
        transport=httpx.MockTransport(recorder),
        now=mono,
        sleep=recorder.sleeps.append,
        token_now=token_clock,
    )
    return provider, recorder, mono, token_clock


def ok_handler(request: httpx.Request, *, token: bool) -> httpx.Response:
    return token_response() if token else httpx.Response(200, json=states_payload())


# -- normalization ------------------------------------------------------------


def test_provider_implements_aircraft_provider():
    provider, *_ = make_provider(ok_handler)
    assert isinstance(provider, AircraftProvider)
    assert isinstance(build_opensky_provider(make_settings()), OpenSkyProvider)


def test_normalizes_documented_state_vectors():
    provider, recorder, _, _ = make_provider(ok_handler)
    entities = provider.get_entities()
    positions = provider.get_positions()
    assert len(recorder.states_requests) == 1  # second call served from cache

    by_id = {entity.id: entity for entity in entities}
    conair = by_id["aircraft:c0ffee"]
    assert conair.source is Source.opensky
    assert conair.type is EntityType.aircraft
    assert conair.callsign == "CONAIR"  #8-char field, trailing spaces stripped
    assert conair.identifier == "CONAIR"
    assert conair.icao24 == "c0ffee"

    placed = {p.entity_id: p for p in positions}["aircraft:c0ffee"]
    assert placed.latitude == pytest.approx(51.89)
    assert placed.longitude == pytest.approx(1.28)
    assert placed.altitude == pytest.approx(11500.0)  # geo preferred over baro
    assert placed.speed == pytest.approx(230.0)  # m/s, documented unit
    assert placed.heading == pytest.approx(30.0)
    assert placed.course == pytest.approx(30.0)  # true_track = course over ground
    assert placed.vertical_rate == pytest.approx(1.5)
    assert placed.signal_age == pytest.approx(1.0)  # time - last_contact
    assert placed.quality is DataQuality.good
    assert placed.source is Source.opensky
    assert placed.timestamp == datetime(2016, 3, 26, 10, 13, 44, tzinfo=timezone.utc)


def test_positionless_state_keeps_entity_without_position():
    provider, *_ = make_provider(ok_handler)
    entities = {entity.id: entity for entity in provider.get_entities()}
    position_ids = {position.entity_id for position in provider.get_positions()}
    orphan = entities["aircraft:abc123"]
    assert orphan.callsign is None
    assert orphan.identifier == "abc123"  # falls back to the ICAO24
    assert orphan.id not in position_ids  # null lat/lon -> no position


# -- caching & documented throttling -----------------------------------------


def test_cache_serves_repeated_calls_without_new_requests():
    provider, recorder, mono, _ = make_provider(ok_handler)
    provider.get_positions()
    mono.advance(4.9)  # below opensky_cache_ttl_s (5)
    provider.get_positions()
    provider.get_entities()
    assert len(recorder.states_requests) == 1


def test_authenticated_floor_of_5s_is_enforced():
    provider, recorder, mono, _ = make_provider(
        ok_handler,
        opensky_client_id="cid",
        opensky_client_secret=SECRET,
        opensky_cache_ttl_s=1.0,
    )
    provider.get_positions()  # t=0
    mono.advance(4.0)  # cache expired, but below the documented 5s floor
    provider.get_positions()
    assert len(recorder.states_requests) == 1
    mono.advance(1.0)  # t=5 -> floor reached
    provider.get_positions()
    assert len(recorder.states_requests) == 2


def test_anonymous_floor_of_10s_is_enforced():
    provider, recorder, mono, _ = make_provider(ok_handler, opensky_cache_ttl_s=1.0)
    provider.get_positions()  # t=0
    mono.advance(9.0)  # below the documented 10s anonymous floor
    provider.get_positions()
    assert len(recorder.states_requests) == 1
    mono.advance(1.0)  # t=10 -> floor reached
    provider.get_positions()
    assert len(recorder.states_requests) == 2


# -- authentication -----------------------------------------------------------


def test_anonymous_requests_carry_no_authorization_header():
    provider, recorder, *_ = make_provider(ok_handler)
    provider.get_positions()
    assert recorder.states_requests[0].headers.get("Authorization") is None
    assert recorder.token_requests == []  # no credentials configured -> no token


def test_client_credentials_flow_and_token_reuse():
    provider, recorder, mono, _ = make_provider(
        ok_handler, opensky_client_id="cid", opensky_client_secret=SECRET
    )
    provider.get_positions()
    body = recorder.token_requests[0].content  # form-encoded POST per docs
    assert b"grant_type=client_credentials" in body
    assert b"client_id=cid" in body
    assert b"client_secret=" + SECRET.encode() in body  # to the auth server only
    assert recorder.states_requests[0].headers["Authorization"] == "Bearer tok-1"

    mono.advance(6.0)  # past TTL/floor, well before the 30-minute expiry
    provider.get_positions()
    assert len(recorder.token_requests) == 1  # cached token reused
    assert len(recorder.states_requests) == 2


def test_token_refreshes_after_documented_expiry_margin():
    provider, recorder, mono, token_clock = make_provider(
        ok_handler,
        opensky_client_id="cid",
        opensky_client_secret=SECRET,
        opensky_cache_ttl_s=1.0,
    )
    provider.get_positions()
    assert len(recorder.token_requests) == 1
    # Tokens live 1800s; the documented refresh margin is 30s before expiry.
    token_clock.value += timedelta(seconds=1771)
    mono.advance(6.0)
    provider.get_positions()
    assert len(recorder.token_requests) == 2


# -- error handling (documented status codes) ---------------------------------


def test_401_invalidates_token_and_retries():
    """Docs: 401 == token expired -> fetch a new one and retry."""

    def handler(request, *, token):
        if token:
            return token_response()
        if not handler.first_done:
            handler.first_done = True
            return httpx.Response(401)
        return httpx.Response(200, json=states_payload())

    handler.first_done = False

    provider, recorder, *_ = make_provider(
        handler, opensky_client_id="cid", opensky_client_secret=SECRET
    )
    assert provider.get_positions()  # recovered within the retry budget
    assert len(recorder.states_requests) == 2  # 401, then success
    assert len(recorder.token_requests) == 2  # refreshed after the 401


def test_429_arms_cooldown_from_documented_header_and_serves_cache():
    def handler(request, *, token):
        if token:
            return token_response()
        handler.calls += 1
        if handler.calls == 2:
            return httpx.Response(
                429, headers={"X-Rate-Limit-Retry-After-Seconds": "120"}
            )
        return httpx.Response(200, json=states_payload())

    handler.calls = 0

    provider, recorder, mono, _ = make_provider(handler, opensky_cache_ttl_s=1.0)
    provider.get_positions()  # t=0 -> success
    mono.advance(10.0)  # anonymous floor is 10s
    stale = provider.get_positions()  # t=10 -> 429; serves stale, never raises
    assert stale  # cached data still available
    assert len(recorder.states_requests) == 2
    mono.advance(50.0)  # t=60, inside the 120s cooldown
    provider.get_positions()
    assert len(recorder.states_requests) == 2  # cooldown blocks the request
    mono.advance(71.0)  # t=131 > cooldown (130); throttle floor satisfied
    provider.get_positions()
    assert len(recorder.states_requests) == 3  # allowed again


def test_5xx_retries_with_exponential_backoff_then_serves_empty():
    def handler(request, *, token):
        return token_response() if token else httpx.Response(500)

    provider, recorder, *_ = make_provider(handler)
    assert provider.get_positions() == []  # no cache yet -> empty, no exception
    assert len(recorder.states_requests) == 3  # 1 attempt + opensky_max_retries
    assert recorder.sleeps == [1.0, 2.0]  # backoff base * 2**attempt


def test_timeout_is_retried_with_backoff():
    def handler(request, *, token):
        if token:
            return token_response()
        raise httpx.ConnectTimeout("simulated timeout")

    provider, recorder, *_ = make_provider(handler)
    assert provider.get_entities() == []
    assert len(recorder.states_requests) == 3
    assert recorder.sleeps == [1.0, 2.0]
    # Still inside the throttle floor: no further attempts.
    provider.get_positions()
    assert len(recorder.states_requests) == 3


def test_4xx_is_not_retried():
    def handler(request, *, token):
        return token_response() if token else httpx.Response(403)

    provider, recorder, *_ = make_provider(handler)
    assert provider.get_positions() == []
    assert len(recorder.states_requests) == 1  # documented client error
    assert recorder.sleeps == []


def test_credentials_never_appear_in_logs(caplog):
    def handler(request, *, token):
        return httpx.Response(500)  # failing token endpoint -> exception path

    provider, *_ = make_provider(
        handler, opensky_client_id="cid", opensky_client_secret=SECRET
    )
    with caplog.at_level(logging.DEBUG):
        provider.get_positions()
    assert SECRET not in caplog.text
    assert "cid" not in caplog.text


# -- bounding box ------------------------------------------------------------


def test_bbox_is_forwarded_as_documented_parameters():
    provider, recorder, *_ = make_provider(ok_handler, opensky_bbox="45.8,47.8,6.0,10.5")
    provider.get_positions()
    assert dict(recorder.states_requests[0].url.params) == {
        "lamin": "45.8",
        "lamax": "47.8",
        "lomin": "6.0",
        "lomax": "10.5",
    }


def test_invalid_bbox_is_ignored_with_warning(caplog):
    provider, recorder, *_ = make_provider(ok_handler, opensky_bbox="not-a-bbox")
    with caplog.at_level(logging.WARNING, logger="app.providers.opensky"):
        provider.get_positions()
    assert dict(recorder.states_requests[0].url.params) == {}  # global query
    assert "OPENSKY_BBOX" in caplog.text


# -- provider selection (demo must stay untouched) ----------------------------


def test_demo_mode_still_builds_mock_providers():
    settings = make_settings()
    aircraft, ships = build_providers(settings, build_clock(settings))
    inner_aircraft = aircraft._provider if hasattr(aircraft, '_provider') else aircraft
    assert isinstance(inner_aircraft, MockAircraftProvider)
    inner_ships = ships._provider if hasattr(ships, '_provider') else ships
    assert isinstance(inner_ships, MockShipProvider)


def test_local_mode_still_builds_mock_providers():
    settings = make_settings(app_mode="local")
    aircraft, ships = build_providers(settings, build_clock(settings))
    inner_aircraft = aircraft._provider if hasattr(aircraft, '_provider') else aircraft
    assert isinstance(inner_aircraft, MockAircraftProvider)
    inner_ships = ships._provider if hasattr(ships, '_provider') else ships
    assert isinstance(inner_ships, MockShipProvider)


def test_production_with_credentials_builds_opensky_provider():
    settings = make_settings(
        app_mode="production", opensky_enabled=True, opensky_client_id="cid"
    )
    aircraft, ships = build_providers(settings, build_clock(settings))
    assert isinstance(aircraft._provider if hasattr(aircraft, "_provider") else aircraft, OpenSkyProvider)  # no network at construction
    inner_ships = ships._provider if hasattr(ships, '_provider') else ships
    assert isinstance(inner_ships, MockShipProvider)  # AIS arrives in a later phase


def test_production_requires_explicit_enable_flag(caplog):
    """Credentials alone are not enough: OPENSKY_ENABLED must be true."""
    settings = make_settings(app_mode="production", opensky_client_id="cid")
    with caplog.at_level(logging.WARNING, logger="app.runtime"):
        aircraft, _ = build_providers(settings, build_clock(settings))
    inner_aircraft = aircraft._provider if hasattr(aircraft, '_provider') else aircraft
    assert isinstance(inner_aircraft, MockAircraftProvider)
    assert "OPENSKY_ENABLED is false" in caplog.text


def test_production_enabled_without_credentials_falls_back_to_mock(caplog):
    settings = make_settings(app_mode="production", opensky_enabled=True)
    with caplog.at_level(logging.WARNING, logger="app.runtime"):
        aircraft, ships = build_providers(settings, build_clock(settings))
    inner_aircraft = aircraft._provider if hasattr(aircraft, '_provider') else aircraft
    assert isinstance(inner_aircraft, MockAircraftProvider)
    inner_ships = ships._provider if hasattr(ships, '_provider') else ships
    assert isinstance(inner_ships, MockShipProvider)
    assert "OPENSKY_CLIENT_ID" in caplog.text


@pytest.mark.parametrize("mode", ["demo", "local"])
def test_non_production_modes_never_construct_opensky(mode, caplog):
    """APP_MODE != production must never touch OpenSky, whatever the config."""
    settings = make_settings(
        app_mode=mode,
        opensky_enabled=True,
        opensky_client_id="cid",
        opensky_client_secret=SECRET,
    )
    with caplog.at_level(logging.WARNING, logger="app.runtime"):
        aircraft, ships = build_providers(settings, build_clock(settings))
    inner_aircraft = aircraft._provider if hasattr(aircraft, '_provider') else aircraft
    assert isinstance(inner_aircraft, MockAircraftProvider)
    inner_ships = ships._provider if hasattr(ships, '_provider') else ships
    assert isinstance(inner_ships, MockShipProvider)
    assert "OPENSKY_ENABLED is ignored" in caplog.text


def test_opensky_settings_are_read_from_environment(monkeypatch):
    """Credentials and knobs come from environment variables only."""
    monkeypatch.setenv("OPENSKY_ENABLED", "true")
    monkeypatch.setenv("OPENSKY_TIMEOUT_S", "7.5")
    monkeypatch.setenv("OPENSKY_MAX_RETRIES", "4")
    monkeypatch.setenv("OPENSKY_CLIENT_ID", "env-id")
    monkeypatch.setenv("OPENSKY_CLIENT_SECRET", "env-secret")
    settings = Settings(_env_file=None)
    assert settings.opensky_enabled is True
    assert settings.opensky_timeout_s == 7.5
    assert settings.opensky_max_retries == 4
    assert settings.opensky_client_id == "env-id"
    assert settings.opensky_client_secret == "env-secret"


def test_custom_interval_retries_backoff_and_timeout_are_honoured():
    def handler(request, *, token):
        return token_response() if token else httpx.Response(500)

    provider, recorder, mono, _ = make_provider(
        handler,
        opensky_min_request_interval_s=30.0,
        opensky_max_retries=0,
        opensky_backoff_base_s=0.5,
        opensky_timeout_s=4.5,
    )
    provider.get_positions()  # t=0 -> first request
    mono.advance(10.0)  # above default floors, below the configured 30s
    provider.get_positions()
    assert len(recorder.states_requests) == 1  # configured interval wins
    assert recorder.sleeps == []  # retries disabled -> no backoff sleeps
    assert provider._timeout_s == 4.5  # configured timeout wired through
    mono.advance(20.0)  # t=30 -> configured interval reached
    provider.get_positions()
    assert len(recorder.states_requests) == 2



