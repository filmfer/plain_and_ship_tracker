"""Tests for the historical store (offline fakes; real-PostGIS gated by env).

Set ``PBR_TEST_DATABASE_URL`` (a PostgreSQL URL with PostGIS available) to
enable the round-trip integration test; without it everything runs offline.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

import pytest

from app.config import Settings
from app.db import (
    INSERT_POSITION_SQL,
    LOAD_TRACK_SQL,
    UPSERT_ENTITY_SQL,
    NullDatabase,
    PostgresDatabase,
    _redact_url,
    build_database,
)
from app.migrate import MIGRATIONS_DIR, run_migrations
from app.models import DataQuality, Entity, EntityType, Position, Source

TS = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
ENTITY_ID = "aircraft:DEMO001"


class FakeCursor:
    def __init__(self, rowcount=0, rows=()):
        self.rowcount = rowcount
        self._rows = list(rows)

    def fetchall(self):
        return self._rows


class FakeConn:
    """Autocommit psycopg3 connection stand-in recording every statement."""

    def __init__(self, load_rows=(), insert_rowcounts=()):
        self.statements = []
        self.transactions = 0
        self.closed = False
        self._load_rows = list(load_rows)
        self._insert_rowcounts = list(insert_rowcounts)
        self._insert_calls = 0

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        if sql == LOAD_TRACK_SQL:
            return FakeCursor(rows=self._load_rows)
        if sql == INSERT_POSITION_SQL:
            if self._insert_rowcounts:
                rowcount = self._insert_rowcounts[
                    min(self._insert_calls, len(self._insert_rowcounts) - 1)
                ]
            else:
                rowcount = 1
            self._insert_calls += 1
            return FakeCursor(rowcount=rowcount)
        return FakeCursor()

    def transaction(self):
        conn = self

        class _Transaction:
            def __enter__(self):
                conn.transactions += 1
                return self

            def __exit__(self, *exc):
                return False

        return _Transaction()

    def close(self):
        self.closed = True


def make_entity() -> Entity:
    return Entity(
        id=ENTITY_ID,
        type=EntityType.aircraft,
        identifier="DEMO001",
        source=Source.mock,
        name="Demo 001",
        callsign="DEMO001",
        icao24="abc123",
    )


def make_position(**overrides) -> Position:
    data = dict(
        entity_id=ENTITY_ID,
        timestamp=TS,
        latitude=38.5,
        longitude=-28.0,
        source=Source.mock,
        quality=DataQuality.good,
        altitude=11000.0,
        speed=230.0,
        heading=45.0,
        course=46.0,
        vertical_rate=0.0,
        signal_age=2.0,
    )
    data.update(overrides)
    return Position(**data)


def make_settings(**overrides) -> Settings:
    kwargs = {"_env_file": None, "database_url": ""}
    kwargs.update(overrides)
    return Settings(**kwargs)


def test_null_database_is_a_noop():
    db = NullDatabase()
    assert db.name == "none"
    assert db.store_history([make_entity()], [make_position()]) == 0
    assert db.store_count == 1
    assert db.load_track(ENTITY_ID) == []
    db.close()  # must never raise


def test_build_database_empty_url_never_connects():
    def connector(url, connect_timeout):  # pragma: no cover - must not run
        raise AssertionError("connector must not be called without DATABASE_URL")

    db = asyncio.run(build_database(make_settings(database_url=""), connector=connector))
    assert isinstance(db, NullDatabase)


def test_build_database_unreachable_falls_back_with_warning(caplog):
    def connector(url, connect_timeout):
        raise ConnectionError("connection refused")

    with caplog.at_level(logging.WARNING, logger="app.db"):
        db = asyncio.run(
            build_database(
                make_settings(database_url="postgresql://u:p@127.0.0.1:1/x"),
                connector=connector,
            )
        )
    assert isinstance(db, NullDatabase)
    assert "PostgreSQL unavailable" in caplog.text
    assert "continuing without historical storage" in caplog.text
    # Credentials must never leak into logs.
    assert "u:p" not in caplog.text


def test_build_database_success_returns_postgres():
    conn = FakeConn()
    db = asyncio.run(
        build_database(make_settings(database_url="postgresql://u:p@localhost/x"),
                       connector=lambda url, **kwargs: conn)
    )
    assert isinstance(db, PostgresDatabase)
    db.close()
    assert conn.closed


def test_redact_url_masks_password():
    redacted = _redact_url("postgresql://user:secret@localhost:5432/db")
    assert "secret" not in redacted
    assert redacted == "postgresql://***@localhost:5432/db"


def test_store_history_rejects_naive_seen_at_before_touching_db():
    conn = FakeConn()
    db = PostgresDatabase(conn)
    with pytest.raises(ValueError, match="timezone-aware"):
        db.store_history([make_entity()], [], seen_at=datetime(2026, 1, 1, 12, 0, 0))
    assert conn.statements == []  # rejected before any SQL ran


def test_store_history_derives_seen_at_from_positions():
    conn = FakeConn()
    db = PostgresDatabase(conn)
    inserted = db.store_history([make_entity()], [make_position()])
    assert inserted == 1
    assert db.store_count == 1

    entity_params = next(
        params for sql, params in conn.statements if sql == UPSERT_ENTITY_SQL
    )
    # first_seen / last_seen default to the newest position timestamp.
    assert entity_params[-1] == TS
    assert entity_params[-2] == TS

    position_params = next(
        params for sql, params in conn.statements if sql == INSERT_POSITION_SQL
    )
    # ST_MakePoint(lon, lat) - longitude comes first.
    assert position_params[2] == pytest.approx(-28.0)
    assert position_params[3] == pytest.approx(38.5)
    assert position_params[0] == ENTITY_ID
    assert position_params[1] == TS
    assert conn.transactions == 1  # one atomic transaction per store call


def test_store_history_counts_only_new_rows():
    conn = FakeConn(insert_rowcounts=[1, 0])  # second row already existed
    db = PostgresDatabase(conn)
    inserted = db.store_history(
        [make_entity()], [make_position(), make_position(timestamp=TS)]
    )
    assert inserted == 1


def test_load_track_maps_rows_to_positions():
    row = (TS, 38.5, -28.0, "mock", "good", 11000.0, 230.0, 45.0, 46.0, 0.0, 2.0)
    conn = FakeConn(load_rows=[row])
    db = PostgresDatabase(conn)

    since = datetime(2025, 12, 31, tzinfo=timezone.utc)
    track = db.load_track(ENTITY_ID, since=since, limit=10)

    assert len(track) == 1
    position = track[0]
    assert position.entity_id == ENTITY_ID
    assert position.timestamp == TS
    assert position.latitude == pytest.approx(38.5)
    assert position.longitude == pytest.approx(-28.0)
    assert position.source is Source.mock
    assert position.quality is DataQuality.good
    assert position.altitude == pytest.approx(11000.0)

    # Newest-first fetch within the window (callers reverse to oldest-first).
    assert "ORDER BY ts DESC" in LOAD_TRACK_SQL
    _, params = conn.statements[0]
    assert params == (ENTITY_ID, since, since, None, None, 10)

    until = datetime(2026, 1, 2, tzinfo=timezone.utc)
    db.load_track(ENTITY_ID, until=until, limit=5)
    _, params = conn.statements[1]
    assert params == (ENTITY_ID, None, None, until, until, 5)


def test_load_track_rejects_naive_bounds():
    conn = FakeConn()
    db = PostgresDatabase(conn)
    naive = datetime(2025, 12, 31)
    with pytest.raises(ValueError, match="timezone-aware"):
        db.load_track(ENTITY_ID, since=naive)
    with pytest.raises(ValueError, match="timezone-aware"):
        db.load_track(ENTITY_ID, until=naive)
    assert conn.statements == []


def test_initial_migration_schema_is_utc_geography_and_minimal():
    """Guard the schema contract (PK-only indexes, geography, timestamptz)."""
    sql = (MIGRATIONS_DIR / "0001_initial.sql").read_text(encoding="utf-8").lower()
    assert "create extension if not exists postgis" in sql
    assert "geography(point, 4326)" in sql  # distance-accurate storage
    assert "timestamptz" in sql  # UTC instants only
    assert "primary key (entity_id, ts)" in sql  # dedupe + per-entity trail
    assert "primary key" in sql  # entities
    assert "create index" not in sql  # no premature indexes (rule #20)


TEST_DB_URL = os.environ.get("PBR_TEST_DATABASE_URL", "")


@pytest.mark.skipif(
    not TEST_DB_URL, reason="set PBR_TEST_DATABASE_URL (PostGIS) to enable"
)
def test_postgres_round_trip():
    """Full migrate -> store -> dedupe -> load cycle against a real database."""
    import psycopg

    with psycopg.connect(TEST_DB_URL, autocommit=True) as conn:
        run_migrations(conn)  # idempotent
        db = PostgresDatabase(conn)
        db.store_history([make_entity()], [make_position()])
        db.store_history([make_entity()], [make_position()])  # duplicate
        track = db.load_track(ENTITY_ID)
        assert len(track) == 1  # (entity_id, ts) primary key deduplicates
        assert track[0].latitude == pytest.approx(38.5)
        assert track[0].longitude == pytest.approx(-28.0)
        assert track[0].source is Source.mock
