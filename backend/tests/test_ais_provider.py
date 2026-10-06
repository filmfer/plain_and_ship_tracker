"""Tests for the ship/AIS provider abstraction (interfaces only).

No real external AIS source is integrated yet (PROMPT 25); future providers
plug into the pipeline exactly like the fakes defined here.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.clock import SimulationClock
from app.config import Settings
from app.models import Entity, EntityType, Position, Source
from app.providers.base import AISProvider, ShipProvider
from app.providers.mock_aircraft import MockAircraftProvider
from app.providers.mock_ship import MockShipProvider
from app.runtime import build_clock, build_providers
from app.simulation import DemoSimulation
from tests.helpers.provider import unwrap

TS = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _vessel(entity_id: str, source: Source) -> tuple[Entity, Position]:
    entity = Entity(
        id=entity_id,
        type=EntityType.ship,
        identifier=entity_id.split(":", 1)[1],
        source=source,
        name="AIS Test Vessel",
        mmsi="244110000",
    )
    position = Position(
        entity_id=entity_id,
        timestamp=TS,
        latitude=52.0,
        longitude=4.0,
        source=source,
        speed=3.5,
        course=90.0,
    )
    return entity, position


class FakeExternalAIS(AISProvider):
    """Stand-in for a future external AIS collector (rule #6, ``source=ais``)."""

    def __init__(self) -> None:
        self.entity, self.position = _vessel("ship:244110000", Source.ais)

    def get_entities(self) -> list[Entity]:
        return [self.entity]

    def get_positions(self) -> list[Position]:
        return [self.position]


class FakeLocalAIS(AISProvider):
    """Stand-in for a future local AIS receiver (``source=local_ais``)."""

    def __init__(self) -> None:
        self.entity, self.position = _vessel("ship:999000001", Source.local_ais)

    def get_entities(self) -> list[Entity]:
        return [self.entity]

    def get_positions(self) -> list[Position]:
        return [self.position]


def test_mock_ship_provider_already_satisfies_the_ais_interface():
    clock = SimulationClock(multiplier=0.0)
    provider = MockShipProvider(clock, seed=42)
    assert isinstance(provider, AISProvider)
    assert isinstance(provider, ShipProvider)


def test_ais_provider_interface_is_abstract():
    with pytest.raises(TypeError):
        AISProvider()  # type: ignore[abstract]


@pytest.mark.parametrize("provider_cls", [FakeExternalAIS, FakeLocalAIS])
def test_future_ais_providers_drop_into_the_pipeline(provider_cls):
    """Both future variants (external + local) substitute seamlessly."""
    clock = SimulationClock(multiplier=0.0)
    provider = provider_cls()
    assert isinstance(provider, ShipProvider)  # interchangeable contract

    sim = DemoSimulation(
        clock=clock,
        aircraft_provider=MockAircraftProvider(clock, seed=42),
        ship_provider=provider,
        tick_interval=0.0,
    )
    snapshot = sim.step()
    entity_ids = {entity.id for entity in snapshot.entities}
    assert provider.entity.id in entity_ids

    vessel_position = next(
        position
        for position in snapshot.positions
        if position.entity_id == provider.entity.id
    )
    assert vessel_position.source is provider.entity.source  # transparency kept


def test_ships_remain_mock_ais_sources_without_any_real_integration():
    configurations = (
        {},  # demo default
        # production with OpenSky enabled still has no real AIS source
        {"app_mode": "production", "opensky_enabled": True, "opensky_client_id": "cid"},
    )
    configurations = (
        {},  # demo default
        # production with OpenSky enabled still has no real AIS source
        {"app_mode": "production", "opensky_enabled": True, "opensky_client_id": "cid"},
    )
    for overrides in configurations:
        settings = Settings(_env_file=None, database_url="", **overrides)
        _, ships = build_providers(settings, build_clock(settings))
        # Unwraps SafeShipProvider if present
        inner_ships = unwrap(ships)
        assert isinstance(inner_ships, MockShipProvider)  # nothing real integrated yet
        assert isinstance(inner_ships, AISProvider)
