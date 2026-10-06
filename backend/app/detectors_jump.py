"""POSITION_JUMP detector (rule #26).

Flags an entity if the speed required to traverse the distance between
consecutive positions exceeds the plausible maximum for that entity type.
"""

from __future__ import annotations

from datetime import datetime

from app.anomalies import AlertState, Anomaly, AnomalyType, Detector
from app.config import Settings
from app.geo import distance_meters
from app.models import Entity, EntityType, Position


class PositionJumpDetector:
    """Detector flagging physically impossible geographic jumps."""

    type = AnomalyType.position_jump

    def __init__(self, settings: Settings) -> None:
        self._max_aircraft_speed = settings.anomaly_max_aircraft_speed
        self._max_ship_speed = settings.anomaly_max_ship_speed

    def evaluate(
        self,
        entity: Entity,
        positions: list[Position],
        now: datetime,
    ) -> list[Anomaly]:
        if len(positions) < 2:
            return []

        # Sort to ensure chronological order.
        ordered = sorted(positions, key=lambda p: p.timestamp)
        max_speed = (
            self._max_aircraft_speed
            if entity.type == EntityType.aircraft
            else self._max_ship_speed
        )

        findings: list[Anomaly] = []

        for i in range(1, len(ordered)):
            prev, curr = ordered[i - 1], ordered[i]

            # Skip if timestamps identical (cannot calculate speed).
            # Future-dated data is handled by other rules.
            dt = (curr.timestamp - prev.timestamp).total_seconds()
            if dt <= 0:
                continue

            dist = distance_meters(
                prev.latitude, prev.longitude,
                curr.latitude, curr.longitude
            )
            speed = dist / dt

            if speed > max_speed:
                # Score ramps: 100% over limit = 100 score.
                # (speed - limit) / limit * 100
                score = min(100, int((speed - max_speed) / max_speed * 100))
                findings.append(
                    Anomaly(
                        entity_id=entity.id,
                        type=AnomalyType.position_jump,
                        state=AlertState.warning,
                        message=(
                            f"Implausible jump: {int(dist / 1000)}km "
                            f"in {int(dt)}s ({int(speed)}m/s)."
                        ),
                        detected_at=now,
                        score=score,
                    )
                )

        return findings


def build_position_jump_detector(settings: Settings) -> Detector:
    return PositionJumpDetector(settings)
