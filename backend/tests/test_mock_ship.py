"""Tests for the deterministic mock ship provider."""

from __future__ import annotations

import math

from app.clock import SimulationClock
from app.geo import EARTH_RADIUS_M
from app.models import DataQuality, EntityType, Position, Source
from app.providers.mock_ship import (
    ANOMALOUS_SPEED_MS,
    COURSE_TURN_DEG,
    EVENT_AT_S,
    FREEZE_AT_S,
    MockShipProvider,
)


def _provider(seconds: float = 0.0, seed: int = 42) -> MockShipProvider:
    """Build a provider whose clock is advanced by ``seconds`` simulated seconds."""
    clock = SimulationClock(multiplier=0.0)
    provider = MockShipProvider(clock, seed=seed)
    clock.advance(seconds)
    return provider


def _by_id(provider: MockShipProvider) -> dict[str, Position]:
    return {p.entity_id: p for p in provider.get_positions()}


def _distance_m(a: Position, b: Position) -> float:
    p1, p2 = math.radians(a.latitude), math.radians(b.latitude)
    dphi = p2 - p1
    dlambda = math.radians(b.longitude - a.longitude)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def test_provides_eight_synthetic_ships() -> None:
    provider = _provider()
    entities = provider.get_entities()

    assert len(entities) == 8
    ids = {e.identifier for e in entities}
    assert ids == {f"DEMO-SHIP-{n:03d}" for n in range(1, 9)}
    assert all(e.type is EntityType.ship for e in entities)
    assert all(e.source is Source.mock for e in entities)


def test_ships_expose_maritime_identifiers() -> None:
    for entity in _provider().get_entities():
        assert entity.name
        assert entity.callsign
        assert entity.mmsi and entity.mmsi.isdigit() and len(entity.mmsi) == 9
        assert entity.imo and entity.imo.startswith("IMO")


def test_entity_ids_match_positions() -> None:
    provider = _provider()
    assert {e.id for e in provider.get_entities()} == {
        p.entity_id for p in provider.get_positions()
    }


def test_positions_are_deterministic_for_same_seed() -> None:
    assert _by_id(_provider(seconds=300, seed=42)) == _by_id(_provider(seconds=300, seed=42))


def test_seed_changes_initial_state_deterministically() -> None:
    a = _by_id(_provider(seconds=0, seed=42))
    b = _by_id(_provider(seconds=0, seed=7))

    assert a != b
    assert a == _by_id(_provider(seconds=0, seed=42))


def test_normal_ship_moves_at_realistic_speed() -> None:
    start = _by_id(_provider(seconds=0))["ship:DEMO-SHIP-001"]
    later = _by_id(_provider(seconds=3600))["ship:DEMO-SHIP-001"]

    assert _distance_m(start, later) > 5_000
    # 5..20 kn nominal range -> roughly 2.5..11 m/s.
    assert 2.0 < start.speed < 12.0


def test_ships_have_no_altitude() -> None:
    for pos in _provider(seconds=60).get_positions():
        assert pos.altitude is None
        assert pos.vertical_rate is None


def test_stationary_ship_does_not_move() -> None:
    early = _by_id(_provider(seconds=0))["ship:DEMO-SHIP-003"]
    late = _by_id(_provider(seconds=3000))["ship:DEMO-SHIP-003"]

    assert _distance_m(early, late) == 0.0
    assert late.speed == 0.0


def test_signal_loss_freezes_timestamp_and_grows_signal_age() -> None:
    provider = _provider(seconds=FREEZE_AT_S * 2)
    pos = _by_id(provider)["ship:DEMO-SHIP-004"]

    assert pos.signal_age > 0.0
    assert pos.quality is not DataQuality.good
    anchor = _by_id(_provider(seconds=FREEZE_AT_S))["ship:DEMO-SHIP-004"]
    assert pos.timestamp == anchor.timestamp


def test_signal_loss_is_fresh_before_freeze() -> None:
    pos = _by_id(_provider(seconds=FREEZE_AT_S - 1))["ship:DEMO-SHIP-004"]

    assert pos.signal_age == 0.0
    assert pos.quality is DataQuality.good


def test_position_jump_is_a_large_discontinuity() -> None:
    before = _by_id(_provider(seconds=EVENT_AT_S - 1))["ship:DEMO-SHIP-005"]
    after = _by_id(_provider(seconds=EVENT_AT_S + 1))["ship:DEMO-SHIP-005"]

    assert _distance_m(before, after) > 300_000


def test_speed_anomaly_exceeds_realistic_speed() -> None:
    before = _by_id(_provider(seconds=EVENT_AT_S - 1))["ship:DEMO-SHIP-006"]
    after = _by_id(_provider(seconds=EVENT_AT_S + 1))["ship:DEMO-SHIP-006"]

    assert before.speed < 15.0
    assert after.speed == ANOMALOUS_SPEED_MS


def test_course_anomaly_turns_sharply() -> None:
    before = _by_id(_provider(seconds=EVENT_AT_S - 1))["ship:DEMO-SHIP-007"]
    after = _by_id(_provider(seconds=EVENT_AT_S + 1))["ship:DEMO-SHIP-007"]

    delta = abs(((after.course - before.course + 180) % 360) - 180)
    assert math.isclose(delta, COURSE_TURN_DEG, abs_tol=1.0)


def test_all_positions_are_valid_and_tz_aware() -> None:
    for pos in _provider(seconds=123).get_positions():
        assert pos.timestamp.tzinfo is not None
        assert -90.0 <= pos.latitude <= 90.0
        assert -180.0 <= pos.longitude <= 180.0
        assert pos.source is Source.mock
