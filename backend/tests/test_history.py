"""Tests for deterministic mock history (trails)."""

from __future__ import annotations

from datetime import timedelta

from app.clock import DEFAULT_SIMULATION_START, SimulationClock
from app.config import Settings
from app.history import HistoryService, sample_track
from app.models import Position
from app.providers.mock_aircraft import MockAircraftProvider
from app.providers.mock_ship import MockShipProvider
from app.runtime import build_runtime

COUNT = 5
INTERVAL_S = 60.0


def _aircraft_provider(seconds: float = 0.0) -> MockAircraftProvider:
    clock = SimulationClock(multiplier=0.0)
    provider = MockAircraftProvider(clock, seed=42)
    clock.advance(seconds)
    return provider


def _ship_provider(seconds: float = 0.0) -> MockShipProvider:
    clock = SimulationClock(multiplier=0.0)
    provider = MockShipProvider(clock, seed=42)
    clock.advance(seconds)
    return provider


# --- sample_track -----------------------------------------------------------


def test_sample_track_returns_requested_sample_count():
    provider = _aircraft_provider()
    at = DEFAULT_SIMULATION_START
    tracks = sample_track(provider, at, COUNT, INTERVAL_S)

    assert len(tracks) == len(provider.get_positions())
    for track in tracks.values():
        assert len(track) == COUNT


def test_sample_track_is_oldest_first_and_time_ordered():
    provider = _aircraft_provider()
    at = DEFAULT_SIMULATION_START
    tracks = sample_track(provider, at, COUNT, INTERVAL_S)

    entity_id = next(iter(tracks))
    times = [p.timestamp for p in tracks[entity_id]]
    assert times == sorted(times)
    assert times[0] == at - timedelta(seconds=(COUNT - 1) * INTERVAL_S)
    assert times[-1] == at


def test_sample_track_is_reproducible():
    at = DEFAULT_SIMULATION_START
    first = sample_track(_aircraft_provider(), at, COUNT, INTERVAL_S)
    second = sample_track(_aircraft_provider(), at, COUNT, INTERVAL_S)

    assert first.keys() == second.keys()
    for entity_id in first:
        a = [(p.latitude, p.longitude, p.timestamp) for p in first[entity_id]]
        b = [(p.latitude, p.longitude, p.timestamp) for p in second[entity_id]]
        assert a == b


def test_sample_track_is_small_and_no_external_data():
    provider = _aircraft_provider()
    tracks = sample_track(provider, DEFAULT_SIMULATION_START, COUNT, INTERVAL_S)
    total = sum(len(t) for t in tracks.values())
    assert total == len(provider.get_positions()) * COUNT
    assert total < 100  # intentionally tiny


def test_sample_track_moving_entity_produces_trail():
    provider = _aircraft_provider()
    tracks = sample_track(provider, DEFAULT_SIMULATION_START, COUNT, INTERVAL_S)
    # At least one entity must have actually moved across its samples.
    moved = any(
        len({(round(p.latitude, 6), round(p.longitude, 6)) for p in track}) > 1
        for track in tracks.values()
    )
    assert moved


def test_sample_track_zero_count_is_empty():
    assert sample_track(_aircraft_provider(), DEFAULT_SIMULATION_START, 0, INTERVAL_S) == {}


# --- HistoryService ---------------------------------------------------------


def _position(entity_id: str, second: int, lat: float = 10.0) -> Position:
    return Position(
        entity_id=entity_id,
        timestamp=DEFAULT_SIMULATION_START + timedelta(seconds=second),
        latitude=lat,
        longitude=20.0,
        source="mock",
    )


def test_record_and_track_appends_in_order():
    history = HistoryService()
    history.record([_position("a", 0), _position("a", 60, lat=10.1)])

    track = history.track("a")
    assert len(track) == 2
    assert track[0].timestamp < track[1].timestamp
    assert history.track("missing") == []


def test_record_skips_duplicate_timestamp():
    history = HistoryService()
    history.record([_position("a", 0)])
    history.record([_position("a", 0)])

    assert len(history.track("a")) == 1


def test_record_is_bounded_per_entity():
    history = HistoryService(max_points_per_entity=3)
    for i in range(10):
        history.record([_position("a", i * 60)])

    track = history.track("a")
    assert len(track) == 3
    assert track[-1].timestamp == _position("a", 540).timestamp


def test_load_replaces_tracks_and_tracks_copy():
    history = HistoryService()
    history.load({"a": [_position("a", 0)]})
    assert len(history) == 1

    snapshot = history.tracks()
    snapshot["a"].append(_position("a", 60))
    assert len(history.track("a")) == 1  # copy, not live view

    history.clear()
    assert len(history) == 0


# --- runtime integration ----------------------------------------------------


def test_build_runtime_backfills_history_for_all_entities():
    settings = Settings(_env_file=None, mock_time_multiplier=0.0)
    runtime = build_runtime(settings)

    tracks = runtime.history.tracks()
    expected = (
        len(runtime.aircraft_provider.get_positions())
        + len(runtime.ship_provider.get_positions())
    )
    assert len(tracks) == expected
    for track in tracks.values():
        assert len(track) == settings.history_sample_count


def test_runtime_tick_extends_history():
    settings = Settings(_env_file=None, mock_time_multiplier=0.0)
    runtime = build_runtime(settings)
    entity_id = next(iter(runtime.history.tracks()))
    before = len(runtime.history.track(entity_id))

    runtime.tick()

    assert len(runtime.history.track(entity_id)) >= before
