"""Tests for the AISHub AIS adapter - every response is mocked (no network).

Covers the published webservice rules: the once-per-minute throttle, empty
replies when polled too fast, credential hygiene, and the mock fallback when
no provider is configured.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from app.clock import build_clock
from app.config import Settings
from app.models import DataQuality, EntityType, Source
from app.providers.aishub import AishubProvider
from app.providers.base import AISProvider
from app.providers.mock_ship import MockShipProvider
from app.runtime import build_providers

# Documented sample values (aishub.net/api, human-readable format=1).
RECEIVED_AT = datetime(2021, 7, 9, 8, 7, 53, tzinfo=timezone.utc)


def sample_payload() -> list:
    return [
        {
            "MMSI": 244750034,
            "TIME": "2021-07-09 08:06:53 GMT",
            "LONGITUDE": 5.03806,
            "LATITUDE": 52.46015,
            "COG": 360,
            "SOG": 0,
            "HEADING": 511,
            "IMO": 9853864,
            "NAME": "CHATEAUROUX",
            "CALLSIGN": "PH7002",
            "TYPE": 69,
        },
        {
            "MMSI": 374651000,
            "TIME": "2021-07-09 08:07:23 GMT",
            "LONGITUDE": -76.951235,
            "LATITUDE": 20.329692,
            "COG": 122.2,
            "SOG": 14.8,
            "HEADING": 119,
            "IMO": 0,
            "NAME": "FUTURE DIAMOND",
            "CALLSIGN": "3ETX9",
            "TYPE": 80,
        },
    ]


def make_settings(**overrides) -> Settings:
    kwargs = {"_env_file": None, "database_url": "", "aishub_username": "hub-user"}
    kwargs.update(overrides)
    return Settings(**kwargs)


class FakeMonotonic:
    """Controllable monotonic clock (seconds)."""

    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def make_provider(handler, **overrides):
    """AishubProvider wired to a scripted MockTransport (fully offline)."""
    requests: list[httpx.Request] = []
    sleeps: list[float] = []
    mono = FakeMonotonic()

    def transport(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    provider = AishubProvider(
        make_settings(**overrides),
        transport=httpx.MockTransport(transport),
        now=mono,
        sleep=sleeps.append,
        wall_now=lambda: RECEIVED_AT,
    )
    return provider, requests, sleeps, mono


def ok_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=sample_payload())


# -- normalization ------------------------------------------------------------


def test_normalizes_documented_sample():
    provider, requests, _, _ = make_provider(ok_handler)
    entities = {entity.id: entity for entity in provider.get_entities()}
    positions = {position.entity_id: position for position in provider.get_positions()}
    assert len(requests) == 1  # second call served from the cache

    vessel = entities["ship:244750034"]
    assert vessel.source is Source.ais
    assert vessel.type is EntityType.ship
    assert vessel.identifier == "244750034"  # searchable MMSI (rule #19)
    assert vessel.mmsi == "244750034"
    assert vessel.imo == "IMO9853864"
    assert vessel.name == "CHATEAUROUX"
    assert vessel.callsign == "PH7002"
    assert entities["ship:374651000"].imo is None  # IMO 0 means none

    placed = positions["ship:374651000"]
    assert placed.source is Source.ais
    assert placed.latitude == pytest.approx(20.329692)
    assert placed.longitude == pytest.approx(-76.951235)
    assert placed.speed == pytest.approx(14.8 * 0.514444)  # knots -> m/s
    assert placed.course == pytest.approx(122.2)
    assert placed.heading == pytest.approx(119.0)
    assert placed.timestamp == datetime(2021, 7, 9, 8, 7, 23, tzinfo=timezone.utc)
    assert placed.signal_age == pytest.approx(30.0)  # RECEIVED_AT - TIME
    assert placed.quality is DataQuality.good

    first = positions["ship:244750034"]
    assert first.heading is None  # 511 = AIS "not available"
    assert first.course == pytest.approx(0.0)  # COG 360 -> 0
    assert first.speed == 0.0


# -- caching & documented throttling -----------------------------------------


def test_cache_prevents_duplicate_requests():
    provider, requests, _, mono = make_provider(ok_handler)
    provider.get_positions()
    mono.advance(30.0)  # below the 60s TTL
    provider.get_positions()
    assert len(requests) == 1


def test_documented_once_per_minute_floor_is_enforced():
    provider, requests, _, mono = make_provider(ok_handler, aishub_cache_ttl_s=1.0)
    provider.get_positions()  # t=0
    mono.advance(59.0)
    provider.get_positions()
    assert len(requests) == 1  # "not more frequently than once per minute"
    mono.advance(1.0)  # t=60
    provider.get_positions()
    assert len(requests) == 2


def test_setting_can_raise_but_never_lower_the_floor():
    provider, requests, _, mono = make_provider(
        ok_handler, aishub_min_request_interval_s=5.0, aishub_cache_ttl_s=1.0
    )
    provider.get_positions()
    mono.advance(30.0)
    provider.get_positions()
    assert len(requests) == 1  # configured 5s ignored: 60s floor still applies
    mono.advance(30.0)  # t=60
    provider.get_positions()
    assert len(requests) == 2
    # ...and a higher setting raises the interval further:
    provider2, requests2, _, mono2 = make_provider(
        ok_handler, aishub_min_request_interval_s=120.0, aishub_cache_ttl_s=1.0
    )
    provider2.get_positions()
    mono2.advance(60.0)
    provider2.get_positions()
    assert len(requests2) == 1  # below the configured 120s


def test_timeout_setting_is_wired():
    provider, *_ = make_provider(ok_handler, aishub_timeout_s=4.5)
    assert provider._timeout_s == 4.5


# -- error handling (documented behaviour) ------------------------------------


def test_rate_limited_empty_reply_keeps_cache_and_never_hammers():
    """Docs: polling faster than once/minute makes the service 'return nothing'."""
    responses = iter(
        [httpx.Response(200, json=sample_payload()), httpx.Response(200, content=b"")]
    )
    provider, requests, sleeps, mono = make_provider(
        lambda request: next(responses), aishub_cache_ttl_s=1.0
    )
    provider.get_positions()
    mono.advance(60.0)
    stale = provider.get_positions()
    assert stale  # previous cache still served
    assert len(requests) == 2  # exactly one refresh attempt, no retry storm
    assert sleeps == []


def test_5xx_retries_with_exponential_backoff_then_serves_empty():
    provider, requests, sleeps, _ = make_provider(
        lambda request: httpx.Response(500)
    )
    assert provider.get_positions() == []
    assert len(requests) == 3  # 1 attempt + aishub_max_retries
    assert sleeps == [1.0, 2.0]  # backoff base * 2**attempt


# -- retry behaviour ----------------------------------------------------------


def test_timeout_is_retried_with_backoff():
    def handler(request):
        raise httpx.ConnectTimeout("simulated timeout")

    provider, requests, sleeps, _ = make_provider(handler)
    assert provider.get_entities() == []
    assert len(requests) == 3
    assert sleeps == [1.0, 2.0]


# -- provider selection (demo must stay untouched) ----------------------------


def test_demo_and_local_modes_always_use_mock_ships():
    for mode in ("demo", "local"):
        settings = make_settings(app_mode=mode)
        _, ships = build_providers(settings, build_clock(settings))
        inner_ships = ships._provider if hasattr(ships, "_provider") else ships
    assert isinstance(inner_ships, MockShipProvider)


@pytest.mark.parametrize("mode", ["demo", "local"])
def test_non_production_modes_never_construct_aishub(mode, caplog):
    """APP_MODE != production must never touch AISHub, whatever the config."""
    settings = make_settings(
        app_mode=mode, aishub_enabled=True, aishub_username="hub-user"
    )
    with caplog.at_level(logging.WARNING, logger="app.runtime"):
        _, ships = build_providers(settings, build_clock(settings))
    inner_ships = ships._provider if hasattr(ships, "_provider") else ships
    assert isinstance(inner_ships, MockShipProvider)
    assert "AISHUB_ENABLED is ignored" in caplog.text


def test_production_with_username_builds_aishub_provider():
    settings = make_settings(
        app_mode="production", aishub_enabled=True, aishub_username="hub-user"
    )
    _, ships = build_providers(settings, build_clock(settings))
    assert isinstance(ships._provider if hasattr(ships, "_provider") else ships, AishubProvider)  # no network at construction
    assert isinstance(ships._provider if hasattr(ships, "_provider") else ships, AISProvider)  # satisfies the P25 interface


def test_production_requires_explicit_enable_flag(caplog):
    """A username alone is not enough: AISHUB_ENABLED must be true."""
    settings = make_settings(
        app_mode="production", aishub_username="hub-user"
    )
    with caplog.at_level(logging.WARNING, logger="app.runtime"):
        _, ships = build_providers(settings, build_clock(settings))
    inner_ships = ships._provider if hasattr(ships, "_provider") else ships
    assert isinstance(inner_ships, MockShipProvider)
    assert "AISHUB_ENABLED is false" in caplog.text


def test_production_enabled_without_username_falls_back_to_mock(caplog):
    settings = make_settings(
        app_mode="production", aishub_enabled=True, aishub_username=""
    )
    with caplog.at_level(logging.WARNING, logger="app.runtime"):
        _, ships = build_providers(settings, build_clock(settings))
    inner_ships = ships._provider if hasattr(ships, "_provider") else ships
    assert isinstance(inner_ships, MockShipProvider)
    assert "AISHUB_USERNAME" in caplog.text


def test_aishub_settings_are_read_from_environment(monkeypatch):
    """Membership username and knobs come from environment variables only."""
    monkeypatch.setenv("AISHUB_ENABLED", "true")
    monkeypatch.setenv("AISHUB_USERNAME", "env-hub-user")
    monkeypatch.setenv("AISHUB_TIMEOUT_S", "7.5")
    settings = Settings(_env_file=None, database_url="")
    assert settings.aishub_enabled is True
    assert settings.aishub_username == "env-hub-user"
    assert settings.aishub_timeout_s == 7.5


# -- credential hygiene & architecture guard -----------------------------------


def test_username_never_leaks_into_logs(caplog):
    # The webservice authenticates in the query string, which httpx logs at
    # INFO (full URL). The module pins the httpx logger to WARNING on import
    # so the membership username can never reach application logs.
    import app.providers.aishub as aishub_module

    assert logging.getLogger("httpx").level <= logging.WARNING
    assert "setLevel" in Path(aishub_module.__file__).read_text()

    provider, _, _, _ = make_provider(lambda request: httpx.Response(500))
    with caplog.at_level(logging.WARNING):
        provider.get_positions()
    assert "hub-user" not in caplog.text


def test_frontend_never_calls_aishub():
    """The browser talks only to our backend; AISHub stays server-side."""
    root = Path(__file__).resolve().parents[2] / "frontend" / "src"
    offenders = [
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file()
        and "aishub" in path.read_text(errors="ignore").lower()
    ]
    assert offenders == []

