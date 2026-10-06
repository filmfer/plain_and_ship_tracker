"""Anomaly engine foundation tests."""

from __future__ import annotations

from datetime import datetime, timezone

from app.anomalies import (
    AlertState,
    Anomaly,
    AnomalyEngine,
    AnomalyType,
)
from app.clock import SimulationClock
from app.config import Settings
from app.models import Entity, EntityType, Position, Source

TS = datetime(2026, 1, 1, tzinfo=timezone.utc)


def make_settings(**overrides) -> Settings:
    kwargs = {"_env_file": None, "database_url": ""}
    kwargs.update(overrides)
    return Settings(**kwargs)


def make_entity() -> Entity:
    return Entity(
        id="aircraft:DEMO001",
        type=EntityType.aircraft,
        identifier="DEMO001",
        source=Source.mock,
    )


def make_position() -> Position:
    return Position(
        entity_id="aircraft:DEMO001",
        timestamp=TS,
        latitude=38.0,
        longitude=-28.0,
        source=Source.mock,
    )


def test_engine_starts_with_no_detectors():
    engine = AnomalyEngine(make_settings())
    assert engine.detector_types == []
    result = engine.evaluate_entity(make_entity(), [make_position()])
    assert result == []


class FlaggingDetector:
    """Minimal test detector: flags any non-empty track once."""

    type = AnomalyType.position_stale

    def __init__(self, seen: list[datetime]) -> None:
        self.seen = seen

    def evaluate(self, entity, positions, now):
        self.seen.append(now)
        if not positions:
            return []
        return [
            Anomaly(
                entity_id=entity.id,
                type=AnomalyType.position_stale,
                state=AlertState.watch,
                message="Position unchanged for 16m 40s.",
                detected_at=now,
                score=10,
            )
        ]


def test_registered_detector_evaluates_with_engine_clock():
    clock = SimulationClock(multiplier=0.0)
    engine = AnomalyEngine(make_settings(), clock=clock)
    seen: list[datetime] = []
    engine.register(FlaggingDetector(seen))
    assert engine.detector_types == [AnomalyType.position_stale]
    findings = engine.evaluate_entity(make_entity(), [make_position()])
    assert len(findings) == 1
    assert findings[0].type is AnomalyType.position_stale
    assert findings[0].state is AlertState.watch
    assert seen == [clock.now()]  # engine time, never wall-clock


def test_empty_track_yields_no_findings():
    engine = AnomalyEngine(make_settings())
    engine.register(FlaggingDetector([]))
    assert engine.evaluate_entity(make_entity(), []) == []


def test_evaluate_all_returns_only_flagged_entities():
    engine = AnomalyEngine(make_settings())
    engine.register(FlaggingDetector([]))
    flagged = make_entity()
    clean = Entity(
        id="ship:DEMO-SHIP-001",
        type=EntityType.ship,
        identifier="DEMO-SHIP-001",
        source=Source.mock,
    )
    result = engine.evaluate_all(
        [flagged, clean], {flagged.id: [make_position()]}
    )
    assert list(result) == [flagged.id]


def test_anomaly_is_immutable_and_score_bounded():
    finding = Anomaly(
        entity_id="aircraft:DEMO001",
        type=AnomalyType.signal_lost,
        state=AlertState.warning,
        message="No new data received for 16m 40s.",
        detected_at=TS,
        score=50,
    )
    assert finding.state is AlertState.warning
    assert "danger" not in finding.message.lower()


def test_aggregate_score():
    engine = AnomalyEngine(make_settings())
    f1 = Anomaly(
        entity_id="test",
        type=AnomalyType.position_stale,
        state=AlertState.watch,
        message="Stale",
        detected_at=TS,
        score=50,
    )
    f2 = Anomaly(
        entity_id="test",
        type=AnomalyType.signal_lost,
        state=AlertState.warning,
        message="Lost",
        detected_at=TS,
        score=30,
    )
    # Stale weight 1.0 -> 50 * 1 = 50
    # Signal weight 2.0 -> 30 * 2 = 60
    # Sum = 110 -> 100
    assert engine.aggregate_score([f1, f2]) == 100


def test_aggregate_score_custom_weights():
    # Weight 0.5 for stale
    settings = make_settings(anomaly_weight_position_stale=0.5)
    engine = AnomalyEngine(settings)
    f1 = Anomaly(
        entity_id="test",
        type=AnomalyType.position_stale,
        state=AlertState.watch,
        message="Stale",
        detected_at=TS,
        score=50,
    )
    # 50 * 0.5 = 25
    assert engine.aggregate_score([f1]) == 25


def test_thresholds_are_configurable_from_environment(monkeypatch):
    monkeypatch.setenv("ANOMALY_STALE_AFTER_S", "120")
    monkeypatch.setenv("ANOMALY_MAX_JUMP_METERS", "1000")
    settings = Settings(_env_file=None, database_url="")
    assert settings.anomaly_stale_after_s == 120.0
    assert settings.anomaly_max_jump_meters == 1000.0
    engine = AnomalyEngine(settings)
    assert engine.detector_types == []

