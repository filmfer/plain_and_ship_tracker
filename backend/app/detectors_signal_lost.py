"""SIGNAL_LOST detector (rule #25).

Flags an entity if no data has been received for longer than
``anomaly_signal_lost_minutes`` (default 15).

Stale vs. signal loss:
* POSITION_STALE - fresh reports arrive, coordinates do not move.
* SIGNAL_LOST   - no reports arrive at all.
"""

from __future__ import annotations

from datetime import datetime

from app.anomalies import AlertState, Anomaly, AnomalyType, Detector
from app.config import Settings
from app.models import Entity, Position


class SignalLostDetector:
    """Detector flagging entities with no recent reports."""

    type = AnomalyType.signal_lost

    def __init__(self, settings: Settings) -> None:
        self._threshold_s = settings.anomaly_signal_lost_after_s

    def evaluate(
        self,
        entity: Entity,
        positions: list[Position],
        now: datetime,
    ) -> list[Anomaly]:
        if not positions:
            return []  # New entity / no data ever -> not a signal loss.

        # Sort to find the absolute latest timestamp in the track.
        latest = max(positions, key=lambda p: p.timestamp)
        if latest.timestamp > now:
            return []  # Future data issue.

        delta_s = (now - latest.timestamp).total_seconds()
        if delta_s <= self._threshold_s:
            return []

        # Convert to a readable duration for the alert message.
        duration_m = int(delta_s / 60)
        duration_s = int(delta_s % 60)
        message = f"No new data received for {duration_m}m {duration_s:02d}s."

        return [
            Anomaly(
                entity_id=entity.id,
                type=AnomalyType.signal_lost,
                state=AlertState.warning,
                message=message,
                detected_at=now,
                score=100,
            )
        ]


def build_signal_lost_detector(settings: Settings) -> Detector:
    return SignalLostDetector(settings)
