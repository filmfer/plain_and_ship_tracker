"""HEADING_INCONSISTENT detector tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.anomalies import AnomalyType
from app.config import Settings
from app.detectors_heading import HeadingInconsistentDetector
from app.models import Entity, EntityType, Position, Source

TS = datetime(2026, 1, 1, tzinfo=timezone.utc)
EID = "aircraft:DEMO006"


def make_settings(**overrides) -> Settings:
    kwargs = {"_env_file": None, "database_url": ""}
    kwargs.update(overrides)
    return Settings(**kwargs)


def report_at(ts: datetime, heading: float, lat: float = 0.0, lon: float = 0.0) -> Position:
    return Position(
        entity_id=EID,
        timestamp=ts,
        latitude=lat,
        longitude=lon,
        heading=heading,
        source=Source.mock,
    )


def test_clean_when_consistent():
    detector = HeadingInconsistentDetector(make_settings())
    # 0 -> 10 deg (move 200m)
    p1 = report_at(TS, 0.0, 0.0, 0.0)
    p2 = report_at(TS + timedelta(seconds=10), 10.0, 0.002, 0.0)
    assert detector.evaluate(Entity(id=EID, type=EntityType.aircraft, identifier="D", source=Source.mock), [p1, p2], TS) == []


def test_flags_erratic_change():
    detector = HeadingInconsistentDetector(make_settings())
    # 0 -> 100 deg (move 200m) - >90 deg change
    p1 = report_at(TS, 0.0, 0.0, 0.0)
    p2 = report_at(TS + timedelta(seconds=10), 100.0, 0.002, 0.0)
    findings = detector.evaluate(Entity(id=EID, type=EntityType.aircraft, identifier="D", source=Source.mock), [p1, p2], TS)
    assert len(findings) == 1
    assert findings[0].type is AnomalyType.heading_inconsistent


def test_ignores_jitter_when_stationary():
    detector = HeadingInconsistentDetector(make_settings())
    # 0 -> 100 deg, but distance is < 100m (min move)
    p1 = report_at(TS, 0.0, 0.0, 0.0)
    p2 = report_at(TS + timedelta(seconds=10), 100.0, 0.0001, 0.0)
    assert detector.evaluate(Entity(id=EID, type=EntityType.aircraft, identifier="D", source=Source.mock), [p1, p2], TS) == []
