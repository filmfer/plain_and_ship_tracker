"""SPEED_INCONSISTENT detector (rule #26).

Flags an entity if:
1. Reported speed exceeds physical plausibility limits.
2. The change in speed between consecutive reports implies acceleration
   exceeding plausible physical limits.
"""

from __future__ import annotations

from datetime import datetime

from app.anomalies import AlertState, Anomaly, AnomalyType, Detector
from app.config import Settings
from app.models import Entity, EntityType, Position


class SpeedInconsistentDetector:
    """Detector flagging impossible or erratic speeds."""

    type = AnomalyType.speed_inconsistent

    def __init__(self, settings: Settings) -> None:
        self._max_speed_ac = settings.anomaly_max_aircraft_speed
        self._max_speed_sh = settings.anomaly_max_ship_speed
        self._max_accel_ac = settings.anomaly_max_aircraft_accel
        self._max_accel_sh = settings.anomaly_max_ship_accel

    def evaluate(
        self,
        entity: Entity,
        positions: list[Position],
        now: datetime,
    ) -> list[Anomaly]:
        if not positions:
            return []

        # Sort to ensure chronological order.
        ordered = sorted(positions, key=lambda p: p.timestamp)
        max_speed = (
            self._max_speed_ac if entity.type == EntityType.aircraft else self._max_speed_sh
        )
        max_accel = (
            self._max_accel_ac if entity.type == EntityType.aircraft else self._max_accel_sh
        )

        findings: list[Anomaly] = []

        # 1. Sanity check: Impossible speed (every report).
        for p in ordered:
            if p.speed is not None and p.speed > max_speed:
                findings.append(
                    Anomaly(
                        entity_id=entity.id,
                        type=AnomalyType.speed_inconsistent,
                        state=AlertState.warning,
                        message=f"Impossible speed: {int(p.speed)}m/s.",
                        detected_at=now,
                        score=80,
                    )
                )

        # 2. Inconsistency check: Acceleration (dv / dt).
        for i in range(1, len(ordered)):
            prev, curr = ordered[i - 1], ordered[i]

            # Skip if speed data missing.
            if prev.speed is None or curr.speed is None:
                continue

            dt = (curr.timestamp - prev.timestamp).total_seconds()
            if dt > 0:
                dv = abs(curr.speed - prev.speed)
                accel = dv / dt
                if accel > max_accel:
                    findings.append(
                        Anomaly(
                            entity_id=entity.id,
                            type=AnomalyType.speed_inconsistent,
                            state=AlertState.watch,
                            message=f"Inconsistent speed change: {int(accel)}m/s².",
                            detected_at=now,
                            score=50,
                        )
                    )

        return findings


def build_speed_inconsistent_detector(settings: Settings) -> Detector:
    return SpeedInconsistentDetector(settings)
