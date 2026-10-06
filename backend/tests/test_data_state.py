"""Tests for the tracking-data freshness classifier."""

from datetime import datetime, timedelta, timezone

from app.data_state import TrackingState, age_seconds, classify


def test_threshold_boundaries() -> None:
    assert classify(0.0, live_s=10, recent_s=60, stale_s=900) is TrackingState.live
    assert classify(10.0, live_s=10, recent_s=60, stale_s=900) is TrackingState.live
    assert classify(10.5, live_s=10, recent_s=60, stale_s=900) is TrackingState.recent
    assert classify(60.0, live_s=10, recent_s=60, stale_s=900) is TrackingState.recent
    assert classify(61.0, live_s=10, recent_s=60, stale_s=900) is TrackingState.stale
    assert classify(900.0, live_s=10, recent_s=60, stale_s=900) is TrackingState.stale
    assert classify(901.0, live_s=10, recent_s=60, stale_s=900) is TrackingState.lost


def test_unknown_on_missing_or_future() -> None:
    assert classify(None, live_s=10, recent_s=60, stale_s=900) is TrackingState.unknown
    # Future/negative ages are never classified as live: guard with classify(-x).
    assert classify(-0.1, live_s=10, recent_s=60, stale_s=900) is TrackingState.unknown


def test_age_seconds_detects_future() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert age_seconds(now, now - timedelta(seconds=30)) == 30.0
    assert age_seconds(now, now) == 0.0
    # Future timestamps (clock skew) report None so callers show UNKNOWN.
    assert age_seconds(now, now + timedelta(seconds=5)) is None
