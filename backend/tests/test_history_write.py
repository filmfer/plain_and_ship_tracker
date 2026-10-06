"""Tests for the threshold-gated historical write service (offline fakes)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest

from app.config import Settings
from app.db import NullDatabase
from app.geo import offset_position
from app.history_write import HistoryWriteService, _heading_delta
from app.models import DataQuality, Entity, EntityType, Position, Source

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
ENTITY_ID = "aircraft:DEMO001"
BASE_LAT = 38.0
BASE_LON = -28.0


class RecordingDatabase:
    """In-memory Database fake capturing every store_history batch."""

    name = "recording"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.batches: list[tuple[list, list]] = []
        self.store_count = 0

    def store_history(self, entities, positions, *, seen_at=None) -> int:
        if self.fail:
            raise ConnectionError("db down")
        self.batches.append((list(entities), list(positions)))
        self.store_count += 1
        return len(list(positions))

    def load_track(self, entity_id, *, since=None, limit=500):
        return []

    def close(self) -> None:
        return None


def make_settings(**overrides) -> Settings:
    kwargs = dict(
        _env_file=None,
        database_url="",
        min_position_change_meters=50.0,
        min_time_between_history_writes=60.0,
        min_speed_change=5.0,
        min_heading_change=10.0,
    )
    kwargs.update(overrides)
    return Settings(**kwargs)


def make_entity(entity_id: str = ENTITY_ID) -> Entity:
    return Entity(
        id=entity_id,
        type=EntityType.aircraft,
        identifier="DEMO001",
        source=Source.mock,
        callsign="DEMO001",
        icao24="abc123",
    )


def make_position(**overrides) -> Position:
    data = dict(
        entity_id=ENTITY_ID,
        timestamp=T0,
        latitude=BASE_LAT,
        longitude=BASE_LON,
        source=Source.mock,
        quality=DataQuality.good,
        altitude=10_000.0,
        speed=200.0,
        heading=90.0,
    )
    data.update(overrides)
    return Position(**data)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def moved_position(distance_m: float, seconds: float, **overrides) -> Position:
    """A position exactly ``distance_m`` east of the baseline at ``seconds``."""
    latitude, longitude = offset_position(BASE_LAT, BASE_LON, 90.0, distance_m)
    return make_position(latitude=latitude, longitude=longitude,
                         timestamp=at(seconds), **overrides)


def make_service(database=None, **settings_overrides) -> HistoryWriteService:
    return HistoryWriteService(database or RecordingDatabase(),
                               make_settings(**settings_overrides))


def test_first_position_written_immediately():
    database = RecordingDatabase()
    service = make_service(database)
    assert service.record([make_entity()], [make_position()]) == 1
    assert database.store_count == 1
    assert service.write_count == 1


def test_identical_position_never_rewritten_even_after_long_time():
    """Core rule: identical positions are never stored repeatedly."""
    database = RecordingDatabase()
    service = make_service(database)
    service.record([make_entity()], [make_position()])
    # Same coordinates/speed/heading, time gate long open -> no write, ever.
    assert service.record([make_entity()], [make_position(timestamp=at(3600))]) == 0
    assert service.record([make_entity()], [make_position(timestamp=at(7200))]) == 0
    assert database.store_count == 1


def test_time_gate_throttles_meaningful_movement():
    service = make_service()  # MIN_TIME_BETWEEN_HISTORY_WRITES = 60s
    assert service.record([make_entity()], [make_position()]) == 1
    # 500 m of movement but only 10 s later -> throttled.
    assert service.record([make_entity()], [moved_position(500.0, 10)]) == 0
    # Same movement once 60 s have passed -> written.
    assert service.record([make_entity()], [moved_position(500.0, 60)]) == 1


def test_position_change_threshold_gates_small_drift():
    service = make_service(min_time_between_history_writes=0.0)
    service.record([make_entity()], [make_position()])
    # 45 m drift < MIN_POSITION_CHANGE_METERS (50) -> skipped.
    assert service.record([make_entity()], [moved_position(45.0, 1)]) == 0
    # 60 m drift (vs the last *written* baseline, not the skipped sample).
    assert service.record([make_entity()], [moved_position(60.0, 2)]) == 1


def test_stationary_entity_with_throttled_time_never_rewrites():
    service = make_service(min_time_between_history_writes=0.0)
    service.record([make_entity()], [make_position()])
    assert service.record([make_entity()], [make_position(timestamp=at(1))]) == 0


def test_speed_change_triggers_write_without_movement():
    service = make_service(min_time_between_history_writes=0.0)
    service.record([make_entity()], [make_position(speed=200.0)])
    # Identical coordinates, speed +10 >= MIN_SPEED_CHANGE (5) -> written.
    changed = make_position(timestamp=at(1), speed=210.0)
    assert service.record([make_entity()], [changed]) == 1


def test_speed_change_below_threshold_is_skipped():
    service = make_service(min_time_between_history_writes=0.0)
    service.record([make_entity()], [make_position(speed=200.0)])
    # Speed +3 < 5 and nothing else changed -> skipped.
    changed = make_position(timestamp=at(1), speed=203.0)
    assert service.record([make_entity()], [changed]) == 0


def test_heading_change_triggers_write_without_movement():
    service = make_service(min_time_between_history_writes=0.0)
    service.record([make_entity()], [make_position(heading=90.0)])
    # Heading +15 >= MIN_HEADING_CHANGE (10), coordinates unchanged -> written.
    changed = make_position(timestamp=at(1), heading=105.0)
    assert service.record([make_entity()], [changed]) == 1


def test_heading_delta_is_circular_across_north():
    assert _heading_delta(355.0, 5.0) == pytest.approx(10.0)
    assert _heading_delta(5.0, 355.0) == pytest.approx(10.0)
    assert _heading_delta(10.0, 190.0) == pytest.approx(180.0)
    service = make_service(min_time_between_history_writes=0.0)
    service.record([make_entity()], [make_position(heading=355.0)])
    changed = make_position(timestamp=at(1), heading=5.0)
    assert service.record([make_entity()], [changed]) == 1


def test_missing_speed_and_heading_contribute_no_change():
    service = make_service(min_time_between_history_writes=0.0)
    service.record([make_entity()], [make_position(speed=None, heading=None)])
    unchanged = make_position(timestamp=at(1), speed=None, heading=None)
    assert service.record([make_entity()], [unchanged]) == 0


def test_positions_without_matching_entity_are_dropped():
    """The entities foreign key must never be violated."""
    database = RecordingDatabase()
    service = make_service(database)
    orphan = make_position(entity_id="aircraft:GHOST")
    assert service.record([make_entity()], [orphan]) == 0
    assert database.store_count == 0


def test_multiple_entities_written_in_one_batch():
    database = RecordingDatabase()
    service = make_service(database)
    entities = [make_entity("aircraft:DEMO001"), make_entity("ship:DEMO-SHIP-001")]
    positions = [
        make_position(),
        make_position(entity_id="ship:DEMO-SHIP-001"),
    ]
    assert service.record(entities, positions) == 2
    assert database.store_count == 1  # one atomic batch per call
    stored_entities, stored_positions = database.batches[0]
    assert [entity.id for entity in stored_entities] == [
        "aircraft:DEMO001",
        "ship:DEMO-SHIP-001",
    ]
    assert len(stored_positions) == 2


def test_database_failure_is_one_warning_retried_and_recovers(caplog):
    database = RecordingDatabase()
    service = make_service(database, min_time_between_history_writes=0.0)
    service.record([make_entity()], [make_position()])  # baseline, healthy

    candidate = moved_position(500.0, 60)
    with caplog.at_level(logging.WARNING, logger="app.history_write"):
        database.fail = True
        assert service.record([make_entity()], [candidate]) == 0
        assert service.record([make_entity()], [candidate]) == 0
    # Exactly one warning: repeated failures must not spam the log.
    assert caplog.text.count("historical write failed") == 1
    assert database.store_count == 1  # nothing stored while failing

    # Baseline was NOT advanced by the failure -> the batch is retried.
    with caplog.at_level(logging.INFO, logger="app.history_write"):
        database.fail = False
        assert service.record([make_entity()], [candidate]) == 1
    assert "historical write recovered" in caplog.text
    assert database.store_count == 2


def test_null_database_service_never_raises():
    service = make_service(NullDatabase())
    assert service.record([make_entity()], [make_position()]) == 1
    assert service.record([make_entity()], [make_position(timestamp=at(1))]) == 0
    assert service.write_count == 1


def test_out_of_order_timestamp_is_rejected_by_time_gate():
    service = make_service()
    service.record([make_entity()], [make_position(timestamp=at(60))])
    # Older than the last written position -> negative delta < min interval.
    assert service.record([make_entity()], [make_position(timestamp=at(10))]) == 0


def test_lifespan_installs_history_service_with_graceful_null_database():
    """Startup must wire the service even when no DATABASE_URL is configured."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app(Settings(_env_file=None, database_url=""))
    with TestClient(app):
        # lifespan replaced the placeholder with the configured services
        assert app.state.database.name == "none"  # no DB -> NullDatabase, no failure
        writer = app.state.history_writer
        assert writer.database.name == "none"
        # Decisions still happen; writes are inert no-ops (demo stays DB-free).
        entity = make_entity("aircraft:TEST-LIFECYCLE")
        position = make_position(entity_id="aircraft:TEST-LIFECYCLE")
        assert writer.record([entity], [position]) == 1
        # A second, unchanged sample is gated as usual.
        again = make_position(entity_id="aircraft:TEST-LIFECYCLE", timestamp=at(1))
        assert writer.record([entity], [again]) == 0
    # Shutdown closes the database cleanly (no exception reaching here).
    assert app.state.database.name == "none"



