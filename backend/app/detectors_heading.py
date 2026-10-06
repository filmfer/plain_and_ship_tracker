"""HEADING_INCONSISTENT detector (rule #26).

Flags an entity if the change in heading/course between consecutive
reports exceeds plausible limits, provided sufficient movement has occurred.
"""

from __future__ import annotations

from datetime import datetime

from app.anomalies import AlertState, Anomaly, AnomalyType, Detector
from app.config import Settings
from app.geo import distance_meters
from app.models import Entity, Position


class HeadingInconsistentDetector:
    """Detector flagging erratic heading/course changes."""

    type = AnomalyType.heading_inconsistent

    def __init__(self, settings: Settings) -> None:
        self._max_change_deg = settings.anomaly_max_heading_change_deg
        self._min_move_m = settings.anomaly_min_move_for_heading_check_m

    def evaluate(
        self,
        entity: Entity,
        positions: list[Position],
        now: datetime,
    ) -> list[Anomaly]:
        if len(positions) < 2:
            return []

        ordered = sorted(positions, key=lambda p: p.timestamp)
        findings: list[Anomaly] = []

        for i in range(1, len(ordered)):
            prev, curr = ordered[i - 1], ordered[i]

            # Use heading if available, else course.
            h1 = prev.heading if prev.heading is not None else prev.course
            h2 = curr.heading if curr.heading is not None else curr.course

            if h1 is None or h2 is None:
                continue

            # Skip if movement is too small (noise).
            dist = distance_meters(
                prev.latitude, prev.longitude,
                curr.latitude, curr.longitude
            )
            if dist < self._min_move_m:
                continue

            # Calculate circular difference.
            diff = abs((h2 - h1 + 180) % 360 - 180)

            if diff > self._max_change_deg:
                findings.append(
                    Anomaly(
                        entity_id=entity.id,
                        type=AnomalyType.heading_inconsistent,
                        state=AlertState.watch,
                        message=f"Erratic heading change: {int(diff)}°.",
                        detected_at=now,
                        score=min(100, int((diff - self._max_change_deg) / 10)),
                    )
                )

        return findings


def build_heading_inconsistent_detector(settings: Settings) -> Detector:
    return HeadingInconsistentDetector(settings)
