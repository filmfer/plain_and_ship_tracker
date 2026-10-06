"""LocalAIS tests part 1: receiver contract."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.config import Settings
from app.models import Entity, EntityType, Position, Source
from app.providers.base import AISProvider, ShipProvider
from app.providers.local_ais import LocalAISProvider

TS = datetime(2026, 1, 1, tzinfo=timezone.utc)


def make_settings(**overrides) -> Settings:
    kwargs = {"_env_file": None, "database_url": ""}
    kwargs.update(overrides)
    return Settings(**kwargs)


def vessel(source: Source = Source.mock) -> tuple[Entity, Position]:
    entity = Entity(
        id="ship:244110000",
        type=EntityType.ship,
        identifier="244110000",
        source=source,
        name="Local Test Vessel",
        mmsi="244110000",
    )
    position = Position(
        entity_id="ship:244110000",
        timestamp=TS,
        latitude=52.0,
        longitude=4.0,
        source=source,
        speed=3.5,
        course=90.0,
    )
    return entity, position


def test_satisfies_the_ais_interface():
    provider = LocalAISProvider(make_settings())
    assert isinstance(provider, AISProvider)
    assert isinstance(provider, ShipProvider)
    assert provider.has_receiver is False


def test_no_receiver_serves_empty_snapshots_without_hardware():
    provider = LocalAISProvider(make_settings())
    assert provider.get_entities() == []
    assert provider.get_positions() == []


def test_receiver_snapshots_are_stamped_local_ais():
    entity, position = vessel()  # source=mock in, local_ais out
    provider = LocalAISProvider(
        make_settings(), receiver=lambda: ([entity], [position])
    )
    assert provider.has_receiver is True
    entities = provider.get_entities()
    positions = provider.get_positions()
    assert entities[0].source is Source.local_ais
    assert entities[0].mmsi == "244110000"
    assert positions[0].source is Source.local_ais
    assert positions[0].latitude == 52.0


def test_aircraft_and_orphan_positions_are_filtered():
    entity, position = vessel()
    aircraft = Entity(
        id="aircraft:XYZ",
        type=EntityType.aircraft,
        identifier="XYZ",
        source=Source.mock,
    )
    orphan = Position(
        entity_id="ship:000000000",
        timestamp=TS,
        latitude=0.0,
        longitude=0.0,
        source=Source.mock,
    )
    provider = LocalAISProvider(
        make_settings(),
        receiver=lambda: ([aircraft, entity], [orphan, position]),
    )
    assert [e.id for e in provider.get_entities()] == ["ship:244110000"]
    got = [p.entity_id for p in provider.get_positions()]
    assert got == ["ship:244110000"]


def test_receiver_polling_is_cached():
    polls: list[float] = []
    now = [0.0]
    entity, position = vessel()

    def counting_receiver():
        polls.append(now[0])
        return [entity], [position]

    provider = LocalAISProvider(
        make_settings(local_ais_cache_ttl_s=10.0),
        receiver=counting_receiver,
        now=lambda: now[0],
    )
    provider.get_entities()
    provider.get_entities()
    assert polls == [0.0]  # second call served from the cache
    now[0] = 11.0
    provider.get_positions()
    assert polls == [0.0, 11.0]  # TTL expiry re-polls


def test_receiver_faults_never_break_ticks(caplog):
    def failing_receiver():
        raise ConnectionError("no dongle attached")

    provider = LocalAISProvider(make_settings(), receiver=failing_receiver)
    with caplog.at_level(logging.WARNING, logger="app.providers.local_ais"):
        assert provider.get_entities() == []
        assert provider.get_positions() == []
    assert "receiver poll failed" in caplog.text


# -- runtime selection (mock stays the default) -------------------------------


def test_demo_local_and_production_default_to_mock_ships():
    from app.clock import build_clock
    from app.providers.mock_ship import MockShipProvider
    from app.runtime import build_providers

    for mode in ("demo", "local", "production"):
        settings = make_settings(app_mode=mode)
        _, ships = build_providers(settings, build_clock(settings))
        inner_ships = ships._provider if hasattr(ships, '_provider') else ships
    assert isinstance(inner_ships, MockShipProvider)


def test_local_mode_with_flag_builds_local_provider():
    from app.clock import build_clock
    from app.runtime import build_providers

    settings = make_settings(app_mode="local", local_ais_enabled=True)
    _, ships = build_providers(settings, build_clock(settings))
    assert isinstance(ships._provider if hasattr(ships, "_provider") else ships, LocalAISProvider)
    assert ships.get_entities() == []  # no receiver, no hardware needed


def test_local_flag_requires_local_mode(caplog):
    from app.clock import build_clock
    from app.providers.mock_ship import MockShipProvider
    from app.runtime import build_providers

    for mode in ("demo", "production"):
        settings = make_settings(app_mode=mode, local_ais_enabled=True)
        with caplog.at_level(logging.WARNING, logger="app.runtime"):
            _, ships = build_providers(settings, build_clock(settings))
        inner_ships = ships._provider if hasattr(ships, '_provider') else ships
    assert isinstance(inner_ships, MockShipProvider)
    assert "LOCAL_AIS_ENABLED is ignored" in caplog.text


def test_local_settings_are_read_from_environment(monkeypatch):
    from app.config import Settings as EnvSettings

    monkeypatch.setenv("LOCAL_AIS_ENABLED", "true")
    monkeypatch.setenv("LOCAL_AIS_CACHE_TTL_S", "2.5")
    settings = EnvSettings(_env_file=None, database_url="")
    assert settings.local_ais_enabled is True
    assert settings.local_ais_cache_ttl_s == 2.5
