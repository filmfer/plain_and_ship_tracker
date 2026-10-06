"""POSITION_STALE detector (rule #24).

Flags an entity whose *reports keep arriving* but whose position has not
materially changed for longer than ``ANOMALY_STALE_MINUTES`` (default 15).

Stale vs. signal loss (rule #25):

* POSITION_STALE - fresh reports arrive, coordinates do not move.
* SIGNAL_LOST   - no reports arrive at all (separate detector, later prompt).

The detector is provider-independent: it consumes only normalized
:class:`~app.models.Position` objects plus the ``now`` instant supplied by
:class:`~app.anomalies.AnomalyEngine` (application clock - SimulationClock
under demo/local - never wall-clock time directly).
"""

from __future__ import annotations

from datetime import datetime

from app.anomalies import (
    AlertState,
    Anomaly,
    AnomalyType,
    Detector,
)
from app.config import Settings
from app.geo import distance_meters
from app.models import Entity, Position


def format_duration(total_seconds: float) -> str:
    """Format a non-negative duration as e.g. ``17m 32s`` or ``2h 05m``."""
    total = max(0, int(total_seconds))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m {seconds:02d}s"


class PositionStaleDetector:
    """Detector flagging positions unchanged beyond the stale threshold."""

    type = AnomalyType.position_stale

    def __init__(self, settings: Settings) -> None:
        self._stale_after_s = settings.anomaly_stale_after_s
        # Same meaningful-movement cutoff as historical writes: sub-threshold
        # jitter does not count as movement.
        self._min_move_meters = settings.min_position_change_meters

    def evaluate(
        self,
        entity: Entity,
        positions: list[Position],
        now: datetime,
    ) -> list[Anomaly]:
        """Flag ``now - anchor > threshold`` where anchor is the latest report.

        The track is walked back from the newest report while consecutive
        reports stay within ``min_position_change_meters``; the anchor is the
        newest report of the *current stationary run*. Finding requires at
        least two reports (data keeps arriving -> not signal loss) and a
        strictly positive movement threshold crossing of the time limit.
        """
        if len(positions) < 2:
            return []
        ordered = sorted(positions, key=lambda p: p.timestamp)
        latest = ordered[-1]
        if latest.timestamp > now:
            return []  # future-dated data is a quality issue for another rule
        anchor = latest
        for previous in reversed(ordered[:-1]):
            if previous.timestamp > anchor.timestamp:
                continue
            moved = distance_meters(
                previous.latitude,
                previous.longitude,
                anchor.latitude,
                anchor.longitude,
            )
            if moved >= self._min_move_meters:
                break
            anchor = previous
        stale_seconds = (now - anchor.timestamp).total_seconds()
        if stale_seconds <= self._stale_after_s:
            return []
        duration = format_duration(stale_seconds)
        # Score ramps 0-100 over the next threshold-length past the limit.
        overshoot = stale_seconds - self._stale_after_s
        score = min(100, int(overshoot / self._stale_after_s * 100))
        return [
            Anomaly(
                entity_id=entity.id,
                type=AnomalyType.position_stale,
                state=AlertState.watch,
                message=f"Position unchanged for {duration}.",
                detected_at=now,
                score=score,
            )
        ]


def build_position_stale_detector(settings: Settings) -> Detector:
    """Factory used by engine wiring (later prompt); keeps import light."""
    return PositionStaleDetector(settings)
