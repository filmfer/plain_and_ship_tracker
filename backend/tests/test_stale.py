"""POSITION_STALE detector tests (rule #24, default 15 minutes)."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.anomalies import AlertState, AnomalyEngine, AnomalyType
from app.clock import DEFAULT_SIMULATION_START, SimulationClock
from app.config import Settings
from app.detectors_stale import PositionStaleDetector, format_duration
from app.models import Entity, EntityType, Position, Source

EID = "aircraft:DEMO003"
LAT, LON = 38.0, -28.0


def make_settings(**overrides) -> Settings:
    kwargs = {"_env_file": None, "database_url": ""}
    kwargs.update(overrides)
    return Settings(**kwargs)


def make_entity() -> Entity:
    return Entity(
        id=EID, type=EntityType.aircraft, identifier="DEMO003",
        source=Source.mock,
    )


def report_at(ts: datetime, lat: float = LAT, lon: float = LON) -> Position:
    return Position(
        entity_id=EID, timestamp=ts, latitude=lat, longitude=lon,
        source=Source.mock,
    )


def track_every_minute(start: datetime, minutes: int) -> list[Position]:
    """Reports keep arriving every minute, position never moves (stale)."""
    return [report_at(start + timedelta(minutes=m)) for m in range(minutes)]


def test_clean_when_moving():
    detector = PositionStaleDetector(make_settings())
    now = DEFAULT_SIMULATION_START + timedelta(minutes=30)
    positions = [
        report_at(DEFAULT_SIMULATION_START + timedelta(minutes=m),
                  lat=LAT + m * 0.01)
        for m in range(30)
    ]
    assert detector.evaluate(make_entity(), positions, now) == []


def test_clean_before_threshold():
    detector = PositionStaleDetector(make_settings())
    start = DEFAULT_SIMULATION_START
    positions = track_every_minute(start, 14)
    now = start + timedelta(minutes=14)
    assert detector.evaluate(make_entity(), positions, now) == []


def test_flags_after_fifteen_minutes():
    detector = PositionStaleDetector(make_settings())
    start = DEFAULT_SIMULATION_START
    positions = track_every_minute(start, 20)
    now = start + timedelta(minutes=20)
    findings = detector.evaluate(make_entity(), positions, now)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.type is AnomalyType.position_stale
    assert finding.state is AlertState.watch
    assert finding.entity_id == EID
    assert finding.detected_at == now
    assert finding.message == "Position unchanged for 20m 00s."
    assert "danger" not in finding.message.lower()


def test_uses_application_clock_via_engine():
    """15 simulated minutes via SimulationClock, no real waiting."""
    settings = make_settings()
    clock = SimulationClock(multiplier=0.0)
    engine = AnomalyEngine(settings, clock=clock)
    engine.register(PositionStaleDetector(settings))
    entity = make_entity()
    findings = []
    for minute in range(16):
        clock.advance(60.0)  # one simulated minute per step
        positions = track_every_minute(DEFAULT_SIMULATION_START, minute + 1)
        findings = engine.evaluate_entity(entity, positions)
        if minute < 15:
            assert findings == [], f"flagged too early at minute {minute}"
    assert len(findings) == 1
    assert findings[0].type is AnomalyType.position_stale


def test_multiplier_60_reaches_threshold_in_15_real_seconds():
    """MOCK_TIME_MULTIPLIER=60: 15 real seconds == 15 simulated minutes."""
    from tests.test_clock import FakeTimeSource

    source = FakeTimeSource()
    clock = SimulationClock(multiplier=60.0, time_source=source)
    settings = make_settings()
    engine = AnomalyEngine(settings, clock=clock)
    engine.register(PositionStaleDetector(settings))
    start = DEFAULT_SIMULATION_START
    # 17 reports spanning start..start+16m; advance 17 real seconds so the
    # application clock (x60) covers the whole track: now = start + 17m and
    # the stationary run (17m) exceeds the 15-minute threshold.
    positions = track_every_minute(start, 17)
    source.advance(17.0)  # 17 real seconds -> 17 simulated minutes
    assert clock.now() - start == timedelta(minutes=17)
    findings = engine.evaluate_entity(make_entity(), positions)
    assert len(findings) == 1
    assert findings[0].message == "Position unchanged for 17m 00s."


def test_stale_requires_fresh_reports_not_signal_loss():
    """A single old report is not stale (no fresh data -> signal loss)."""
    detector = PositionStaleDetector(make_settings())
    now = DEFAULT_SIMULATION_START + timedelta(minutes=30)
    assert detector.evaluate(make_entity(), [], now) == []
    assert detector.evaluate(
        make_entity(), [report_at(DEFAULT_SIMULATION_START)], now
    ) == []


def test_movement_resets_anchor():
    detector = PositionStaleDetector(make_settings())
    start = DEFAULT_SIMULATION_START
    positions = track_every_minute(start, 20)
    # A genuine move at minute 18 resets the stationary run.
    positions.append(report_at(start + timedelta(minutes=18), lat=LAT + 0.05))
    now = start + timedelta(minutes=20)
    assert detector.evaluate(make_entity(), positions, now) == []


def test_threshold_configurable_in_minutes(monkeypatch):
    monkeypatch.setenv("ANOMALY_STALE_MINUTES", "2")
    settings = Settings(_env_file=None, database_url="")
    assert settings.anomaly_stale_minutes == 2.0
    assert settings.anomaly_stale_after_s == 120.0
    detector = PositionStaleDetector(settings)
    start = DEFAULT_SIMULATION_START
    positions = track_every_minute(start, 4)
    findings = detector.evaluate(
        make_entity(), positions, start + timedelta(minutes=4)
    )
    assert len(findings) == 1
    assert findings[0].message == "Position unchanged for 4m 00s."


def test_legacy_seconds_env_still_works(monkeypatch):
    monkeypatch.setenv("ANOMALY_STALE_AFTER_S", "120")
    settings = Settings(_env_file=None, database_url="")
    assert settings.anomaly_stale_minutes == 2.0
    detector = PositionStaleDetector(settings)
    start = DEFAULT_SIMULATION_START
    positions = track_every_minute(start, 4)
    findings = detector.evaluate(
        make_entity(), positions, start + timedelta(minutes=4)
    )
    assert len(findings) == 1


def test_format_duration():
    assert format_duration(0) == "0m 00s"
    assert format_duration(17 * 60 + 32) == "17m 32s"
    assert format_duration(2 * 3600 + 5 * 60) == "2h 05m"

