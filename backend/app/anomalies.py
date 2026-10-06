"""Anomaly engine foundation."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.clock import Clock, RealClock
from app.config import Settings
from app.models import AlertState, AnomalyType, Entity, Position


class Anomaly(BaseModel):
    """One immutable engine finding; data-quality language only."""

    model_config = ConfigDict(frozen=True)

    entity_id: str
    type: AnomalyType
    state: AlertState = AlertState.watch
    message: str
    detected_at: datetime
    score: int = Field(default=0, ge=0, le=100)


class Detector(Protocol):
    """Interface every anomaly detector implements."""

    type: AnomalyType

    def evaluate(
        self,
        entity: Entity,
        positions: list[Position],
        now: datetime,
    ) -> list[Anomaly]:
        """Evaluate one entity's track; return findings (empty = clean)."""
        ...


class AnomalyEngine:
    """Detector registry and evaluation entry point."""

    def __init__(
        self,
        settings: Settings,
        clock: Clock | None = None,
    ) -> None:
        self._settings = settings
        self._clock = clock or RealClock()
        self._detectors: dict[AnomalyType, Detector] = {}

    @property
    def detector_types(self) -> list[AnomalyType]:
        """Registered detector kinds, in registration order."""
        return list(self._detectors)

    def register(self, detector: Detector) -> None:
        """Register one detector; replaces any detector of the same type."""
        self._detectors[detector.type] = detector

    def evaluate_entity(
        self,
        entity: Entity,
        positions: list[Position],
    ) -> list[Anomaly]:
        """Run every registered detector against one entity's track."""
        now = self._clock.now()
        findings: list[Anomaly] = []
        for detector in self._detectors.values():
            findings.extend(detector.evaluate(entity, positions, now))
        return findings

    def evaluate_all(
        self,
        entities: list[Entity],
        positions_by_entity: dict[str, list[Position]],
    ) -> dict[str, list[Anomaly]]:
        """Evaluate many entities; only entities with findings are returned."""
        result: dict[str, list[Anomaly]] = {}
        for entity in entities:
            findings = self.evaluate_entity(
                entity, positions_by_entity.get(entity.id, [])
            )
            if findings:
                result[entity.id] = findings
        return result

    def aggregate_score(self, findings: list[Anomaly]) -> int:
        """Calculate aggregated, weighted anomaly score 0-100."""
        score = 0.0
        for f in findings:
            # Map AnomalyType to config weight attribute.
            weight_attr = f"anomaly_weight_{f.type.name}"
            weight = getattr(self._settings, weight_attr, 1.0)
            score += f.score * weight
        return min(100, int(score))
