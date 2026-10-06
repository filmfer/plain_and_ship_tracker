"""ALTITUDE_INCONSISTENT detector (rule #26).

Flags an entity if:
1. Altitude exceeds physically plausible limits for aircraft.
2. Vertical speed (calculated from altitude change) exceeds plausible limits.
3. Reported vertical rate is inconsistent with calculated vertical speed.
"""

from __future__ import annotations

from datetime import datetime

from app.anomalies import AlertState, Anomaly, AnomalyType, Detector
from app.config import Settings
from app.models import Entity, EntityType, Position


class AltitudeInconsistentDetector:
    """Detector flagging impossible or erratic altitude data."""

    type = AnomalyType.altitude_inconsistent

    def __init__(self, settings: Settings) -> None:
        self._max_alt = settings.anomaly_max_altitude_m
        self._max_vrate = settings.anomaly_max_vertical_rate_mps

    def evaluate(
        self,
        entity: Entity,
        positions: list[Position],
        now: datetime,
    ) -> list[Anomaly]:
        if entity.type != EntityType.aircraft or not positions:
            return []

        # Sort to ensure chronological order.
        ordered = sorted(positions, key=lambda p: p.timestamp)
        findings: list[Anomaly] = []

        for i in range(len(ordered)):
            curr = ordered[i]

            # 1. Sanity check: Impossible altitude (above limit).
            if curr.altitude is not None and curr.altitude > self._max_alt:
                findings.append(
                    Anomaly(
                        entity_id=entity.id,
                        type=AnomalyType.altitude_inconsistent,
                        state=AlertState.warning,
                        message=f"Impossible altitude: {int(curr.altitude)}m.",
                        detected_at=now,
                        score=80,
                    )
                )

            # 2. Inconsistency check: Vertical rate.
            if i > 0:
                prev = ordered[i - 1]
                if prev.altitude is not None and curr.altitude is not None:
                    dt = (curr.timestamp - prev.timestamp).total_seconds()
                    if dt > 0:
                        calc_vrate = (curr.altitude - prev.altitude) / dt

                        # Compare calculated vrate vs limits.
                        if abs(calc_vrate) > self._max_vrate:
                            findings.append(
                                Anomaly(
                                    entity_id=entity.id,
                                    type=AnomalyType.altitude_inconsistent,
                                    state=AlertState.watch,
                                    message=f"Impossible vertical rate: {int(calc_vrate)}m/s.",
                                    detected_at=now,
                                    score=60,
                                )
                            )

                        # Inconsistency check: Reported vs Calculated.
                        if curr.vertical_rate is not None:
                            if abs(curr.vertical_rate - calc_vrate) > self._max_vrate * 2:
                                findings.append(
                                    Anomaly(
                                        entity_id=entity.id,
                                        type=AnomalyType.altitude_inconsistent,
                                        state=AlertState.watch,
                                        message=f"Reported vertical rate ({int(curr.vertical_rate)}m/s) inconsistent with track ({int(calc_vrate)}m/s).",
                                        detected_at=now,
                                        score=40,
                                    )
                                )

        return findings


def build_altitude_inconsistent_detector(settings: Settings) -> Detector:
    return AltitudeInconsistentDetector(settings)
