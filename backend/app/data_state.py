"""Tracking-data freshness states.

``LIVE`` / ``RECENT`` / ``STALE`` / ``LOST`` / ``UNKNOWN`` describe how fresh the
*tracking data* for an entity is -- they are never a safety assessment of the
aircraft or ship itself.

Freshness is derived from data age (application-clock ``now`` minus the latest
position timestamp) against configurable thresholds, so demo mode with a
``SimulationClock`` classifies correctly without touching wall-clock time.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional


class TrackingState(str, Enum):
    """Freshness of the tracking data for one entity."""

    live = "LIVE"
    recent = "RECENT"
    stale = "STALE"
    lost = "LOST"
    unknown = "UNKNOWN"


def age_seconds(now: datetime, timestamp: Optional[datetime]) -> Optional[float]:
    """Return data age in seconds, or ``None`` when unknown/invalid.

    A future-dated timestamp (another detector's territory) yields ``None``
    rather than a negative age.
    """
    if timestamp is None or timestamp.tzinfo is None:
        return None
    age = (now - timestamp).total_seconds()
    return age if age >= 0 else None


def classify(
    age_s: Optional[float],
    live_s: float,
    recent_s: float,
    stale_s: float,
) -> TrackingState:
    """Map a data age onto a freshness state (thresholds ascending).

    * ``age <= live_s`` -> LIVE (fresh reports flowing)
    * ``age <= recent_s`` -> RECENT (slightly delayed)
    * ``age <= stale_s`` -> STALE (no fresh report for a while)
    * older -> LOST (presumed out of coverage)
    * unknown age -> UNKNOWN, as does a negative age (future timestamp)
    """
    if age_s is None or age_s != age_s or age_s < 0:  # NaN/negative: unknown
        return TrackingState.unknown
    if age_s <= live_s:
        return TrackingState.live
    if age_s <= recent_s:
        return TrackingState.recent
    if age_s <= stale_s:
        return TrackingState.stale
    return TrackingState.lost
