"""SIGNAL_LOST detector tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.anomalies import AlertState, AnomalyType
from app.config import Settings
from app.detectors_signal_lost import SignalLostDetector
from app.models import Entity, EntityType, Position, Source

TS = datetime(2026, 1, 1, tzinfo=timezone.utc)
EID = "aircraft:DEMO004"


def make_settings(**overrides) -> Settings:
    kwargs = {"_env_file": None, "database_url": ""}
    kwargs.update(overrides)
    return Settings(**kwargs)


def make_entity() -> Entity:
    return Entity(
        id=EID,
        type=EntityType.aircraft,
        identifier="DEMO004",
        source=Source.mock,
    )


def report_at(ts: datetime) -> Position:
    return Position(
        entity_id=EID,
        timestamp=ts,
        latitude=38.0,
        longitude=-28.0,
        source=Source.mock,
    )


def test_clean_when_data_recent():
    detector = SignalLostDetector(make_settings())
    now = TS + timedelta(minutes=5)
    # Latest report 5 mins ago
    positions = [report_at(TS)]
    assert detector.evaluate(make_entity(), positions, now) == []


def test_flags_after_fifteen_minutes():
    detector = SignalLostDetector(make_settings())
    now = TS + timedelta(minutes=20)
    # Latest report 20 mins ago
    positions = [report_at(TS)]
    findings = detector.evaluate(make_entity(), positions, now)
    assert len(findings) == 1
    assert findings[0].type is AnomalyType.signal_lost
    assert findings[0].state is AlertState.warning
    assert "No new data received" in findings[0].message
    assert findings[0].score == 100


def test_resolves_when_data_resumes():
    detector = SignalLostDetector(make_settings())
    # 20 mins elapsed, previous data expired
    now = TS + timedelta(minutes=20)
    # But new data arrived just 1 min ago
    positions = [report_at(TS), report_at(now - timedelta(minutes=1))]
    assert detector.evaluate(make_entity(), positions, now) == []


def test_configurable_threshold():
    settings = make_settings(anomaly_signal_lost_minutes=30.0)
    detector = SignalLostDetector(settings)
    now = TS + timedelta(minutes=20)
    # 20 mins elapsed, but threshold is 30
    positions = [report_at(TS)]
    assert detector.evaluate(make_entity(), positions, now) == []
