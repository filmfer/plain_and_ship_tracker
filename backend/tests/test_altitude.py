"""ALTITUDE_INCONSISTENT detector tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.anomalies import AnomalyType
from app.config import Settings
from app.detectors_altitude import AltitudeInconsistentDetector
from app.models import Entity, EntityType, Position, Source

TS = datetime(2026, 1, 1, tzinfo=timezone.utc)
EID = "aircraft:DEMO007"


def make_settings(**overrides) -> Settings:
    kwargs = {"_env_file": None, "database_url": ""}
    kwargs.update(overrides)
    return Settings(**kwargs)


def make_entity(ent_type: EntityType) -> Entity:
    return Entity(
        id=EID,
        type=ent_type,
        identifier="DEMO007",
        source=Source.mock,
    )


def report_at(ts: datetime, alt: float | None, vrate: float | None = None) -> Position:
    return Position(
        entity_id=EID,
        timestamp=ts,
        latitude=0.0,
        longitude=0.0,
        altitude=alt,
        vertical_rate=vrate,
        source=Source.mock,
    )


def test_ships_are_ignored():
    detector = AltitudeInconsistentDetector(make_settings())
    # Ship with absurd altitude should be ignored
    p1 = report_at(TS, 50000.0)
    assert detector.evaluate(make_entity(EntityType.ship), [p1], TS) == []


def test_flags_impossible_altitude():
    detector = AltitudeInconsistentDetector(make_settings())
    # 50,000m exceeds 20,000m limit
    p1 = report_at(TS, 50000.0)
    findings = detector.evaluate(make_entity(EntityType.aircraft), [p1], TS)
    assert len(findings) == 1
    assert findings[0].type is AnomalyType.altitude_inconsistent


def test_flags_impossible_vertical_rate():
    detector = AltitudeInconsistentDetector(make_settings())
    # 1000m -> 5000m in 10s = 400m/s vertical rate (exceeds 50m/s limit)
    p1 = report_at(TS, 1000.0)
    p2 = report_at(TS + timedelta(seconds=10), 5000.0)
    findings = detector.evaluate(make_entity(EntityType.aircraft), [p1, p2], TS)
    assert len(findings) == 1
    assert findings[0].type is AnomalyType.altitude_inconsistent
    assert "Impossible vertical rate" in findings[0].message
