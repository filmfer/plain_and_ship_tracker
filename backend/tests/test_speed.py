"""SPEED_INCONSISTENT detector tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.anomalies import AnomalyType
from app.config import Settings
from app.detectors_speed import SpeedInconsistentDetector
from app.models import Entity, EntityType, Position, Source

TS = datetime(2026, 1, 1, tzinfo=timezone.utc)
EID = "aircraft:DEMO005"


def make_settings(**overrides) -> Settings:
    kwargs = {"_env_file": None, "database_url": ""}
    kwargs.update(overrides)
    return Settings(**kwargs)


def make_entity(ent_type: EntityType) -> Entity:
    return Entity(
        id=EID,
        type=ent_type,
        identifier="DEMO005",
        source=Source.mock,
    )


def report_at(ts: datetime, speed: float | None) -> Position:
    return Position(
        entity_id=EID,
        timestamp=ts,
        latitude=0.0,
        longitude=0.0,
        speed=speed,
        source=Source.mock,
    )


def test_clean_when_consistent():
    detector = SpeedInconsistentDetector(make_settings())
    # 200m/s -> 210m/s in 10s (accel 1.0 m/s^2)
    p1 = report_at(TS, 200.0)
    p2 = report_at(TS + timedelta(seconds=10), 210.0)
    assert detector.evaluate(make_entity(EntityType.aircraft), [p1, p2], TS) == []


def test_flags_impossible_speed():
    detector = SpeedInconsistentDetector(make_settings())
    # 500m/s (exceeds 350m/s limit)
    p1 = report_at(TS, 500.0)
    findings = detector.evaluate(make_entity(EntityType.aircraft), [p1], TS)
    assert len(findings) == 1
    assert findings[0].type is AnomalyType.speed_inconsistent


def test_flags_sudden_unrealistic_change():
    detector = SpeedInconsistentDetector(make_settings())
    # 100m/s -> 300m/s in 2s (accel 100 m/s^2 -> exceeds aircraft 20m/s^2 limit)
    p1 = report_at(TS, 100.0)
    p2 = report_at(TS + timedelta(seconds=2), 300.0)
    findings = detector.evaluate(make_entity(EntityType.aircraft), [p1, p2], TS)
    assert len(findings) == 1
    assert findings[0].type is AnomalyType.speed_inconsistent
