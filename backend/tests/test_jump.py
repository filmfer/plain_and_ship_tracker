"""POSITION_JUMP detector tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.anomalies import AnomalyType
from app.config import Settings
from app.detectors_jump import PositionJumpDetector
from app.models import Entity, EntityType, Position, Source

TS = datetime(2026, 1, 1, tzinfo=timezone.utc)
EID = "aircraft:DEMO001"


def make_settings(**overrides) -> Settings:
    kwargs = {"_env_file": None, "database_url": ""}
    kwargs.update(overrides)
    return Settings(**kwargs)


def make_entity(ent_type: EntityType) -> Entity:
    return Entity(
        id=EID,
        type=ent_type,
        identifier="DEMO001",
        source=Source.mock,
    )


def report_at(ts: datetime, lat: float, lon: float) -> Position:
    return Position(
        entity_id=EID,
        timestamp=ts,
        latitude=lat,
        longitude=lon,
        source=Source.mock,
    )


def test_clean_when_plausible():
    detector = PositionJumpDetector(make_settings())
    # ~250m/s = 900km/h (plausible aircraft)
    p1 = report_at(TS, 0.0, 0.0)
    p2 = report_at(TS + timedelta(seconds=10), 0.02, 0.0) # ~2.2km jump, ~220m/s
    assert detector.evaluate(make_entity(EntityType.aircraft), [p1, p2], TS) == []


def test_flags_implausible_aircraft_jump():
    detector = PositionJumpDetector(make_settings())
    # ~500m/s (too fast)
    p1 = report_at(TS, 0.0, 0.0)
    p2 = report_at(TS + timedelta(seconds=10), 0.05, 0.0) # ~5.5km jump, ~550m/s
    findings = detector.evaluate(make_entity(EntityType.aircraft), [p1, p2], TS)
    assert len(findings) == 1
    assert findings[0].type is AnomalyType.position_jump
    assert findings[0].score > 0


def test_flags_implausible_ship_jump():
    detector = PositionJumpDetector(make_settings())
    # ~50m/s (too fast for ship, max 35m/s)
    p1 = report_at(TS, 0.0, 0.0)
    p2 = report_at(TS + timedelta(seconds=10), 0.005, 0.0) # ~550m jump, 55m/s
    findings = detector.evaluate(make_entity(EntityType.ship), [p1, p2], TS)
    assert len(findings) == 1
    assert findings[0].type is AnomalyType.position_jump
