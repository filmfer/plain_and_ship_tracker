"""Tests for the deterministic mock aircraft provider."""

from __future__ import annotations

import math

from app.clock import SimulationClock
from app.geo import EARTH_RADIUS_M
from app.models import DataQuality, EntityType, Position, Source
from app.providers.mock_aircraft import (
    ANOMALOUS_SPEED_MS,
    EVENT_AT_S,
    FREEZE_AT_S,
    HEADING_TURN_DEG,
    MockAircraftProvider,
)


def _provider(seconds: float = 0.0, seed: int = 42) -> MockAircraftProvider:
    """Build a provider whose clock is advanced by ``seconds`` simulated seconds."""
    clock = SimulationClock(multiplier=0.0)
    provider = MockAircraftProvider(clock, seed=seed)
    clock.advance(seconds)
    return provider


def _by_id(provider: MockAircraftProvider) -> dict[str, Position]:
    return {p.entity_id: p for p in provider.get_positions()}


def _distance_m(a: Position, b: Position) -> float:
    p1, p2 = math.radians(a.latitude), math.radians(b.latitude)
    dphi = p2 - p1
    dlambda = math.radians(b.longitude - a.longitude)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def test_provides_eight_synthetic_aircraft() -> None:
    provider = _provider()
    entities = provider.get_entities()

    assert len(entities) == 8
    ids = {e.identifier for e in entities}
    assert ids == {f"DEMO{n:03d}" for n in range(1, 9)}
    assert all(e.type is EntityType.aircraft for e in entities)
    assert all(e.source is Source.mock for e in entities)
    assert all(e.icao24 for e in entities)


def test_entity_ids_match_positions() -> None:
    provider = _provider()
    entity_ids = {e.id for e in provider.get_entities()}
    position_ids = {p.entity_id for p in provider.get_positions()}

    assert entity_ids == position_ids


def test_positions_are_deterministic_for_same_seed() -> None:
    a = _by_id(_provider(seconds=300, seed=42))
    b = _by_id(_provider(seconds=300, seed=42))

    assert a == b


def test_seed_changes_initial_state_deterministically() -> None:
    a = _by_id(_provider(seconds=0, seed=42))
    b = _by_id(_provider(seconds=0, seed=7))

    assert a != b
    # ...but each seed is individually reproducible.
    assert a == _by_id(_provider(seconds=0, seed=42))


def test_normal_aircraft_moves_over_time() -> None:
    start = _by_id(_provider(seconds=0))["aircraft:DEMO001"]
    later = _by_id(_provider(seconds=600))["aircraft:DEMO001"]

    assert _distance_m(start, later) > 10_000


def test_stationary_aircraft_does_not_move() -> None:
    early = _by_id(_provider(seconds=0))["aircraft:DEMO003"]
    late = _by_id(_provider(seconds=3000))["aircraft:DEMO003"]

    assert _distance_m(early, late) == 0.0
    assert late.speed == 0.0


def test_signal_loss_freezes_timestamp_and_grows_signal_age() -> None:
    provider = _provider(seconds=FREEZE_AT_S * 2)
    pos = _by_id(provider)["aircraft:DEMO004"]

    assert pos.signal_age > 0.0
    assert pos.quality is not DataQuality.good
    # Timestamp stays anchored at the freeze instant.
    anchor = _by_id(_provider(seconds=FREEZE_AT_S))["aircraft:DEMO004"]
    assert pos.timestamp == anchor.timestamp


def test_signal_loss_is_fresh_before_freeze() -> None:
    pos = _by_id(_provider(seconds=FREEZE_AT_S - 1))["aircraft:DEMO004"]

    assert pos.signal_age == 0.0
    assert pos.quality is DataQuality.good


def test_position_jump_is_a_large_discontinuity() -> None:
    before = _by_id(_provider(seconds=EVENT_AT_S - 1))["aircraft:DEMO005"]
    after = _by_id(_provider(seconds=EVENT_AT_S + 1))["aircraft:DEMO005"]

    step = _distance_m(before, after)
    assert step > 500_000  # far larger than one second of normal flight


def test_speed_anomaly_exceeds_realistic_speed() -> None:
    before = _by_id(_provider(seconds=EVENT_AT_S - 1))["aircraft:DEMO006"]
    after = _by_id(_provider(seconds=EVENT_AT_S + 1))["aircraft:DEMO006"]

    assert before.speed < 400.0
    assert after.speed == ANOMALOUS_SPEED_MS


def test_heading_anomaly_turns_sharply() -> None:
    before = _by_id(_provider(seconds=EVENT_AT_S - 1))["aircraft:DEMO007"]
    after = _by_id(_provider(seconds=EVENT_AT_S + 1))["aircraft:DEMO007"]

    delta = abs(((after.heading - before.heading + 180) % 360) - 180)
    assert math.isclose(delta, HEADING_TURN_DEG, abs_tol=1.0)


def test_all_positions_are_valid_and_tz_aware() -> None:
    for pos in _provider(seconds=123).get_positions():
        assert pos.timestamp.tzinfo is not None
        assert -90.0 <= pos.latitude <= 90.0
        assert -180.0 <= pos.longitude <= 180.0
        assert pos.source is Source.mock
