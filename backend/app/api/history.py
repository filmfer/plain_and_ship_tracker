"""Historical track retrieval: ``GET /api/history/{entity_id}``.

Design notes
------------
* Source: PostgreSQL/PostGIS when configured, otherwise the bounded in-memory
  demo trail (``runtime.history``). If the database has nothing for the window
  the in-memory trail is used as a fallback; the response always reports which
  source served it (rule #29 - source transparency).
* Configurable time window via ``since``/``until`` (ISO-8601). There is
  deliberately **no** wall-clock default window: the simulation clock may run
  at a different epoch than wall time, so an implicit "now" anchor would hide
  demo data. Callers that want a window pass it explicitly.
* Size protection: ``limit`` defaults to ``HISTORY_API_DEFAULT_LIMIT`` and is
  clamped to ``HISTORY_API_MAX_POINTS`` (also the database fetch cap). When a
  track holds more points than the limit, points are downsampled evenly while
  keeping the first and last of the segment, so one request can never return
  an unbounded dataset.
* Naive datetimes are interpreted as UTC; ``since > until`` is a 400.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from app.models import Entity, Position
from app.state import CurrentStateService

router = APIRouter(prefix="/api", tags=["history"])


class HistoryTrack(BaseModel):
    """One entity's historical track for the requested window."""

    entity_id: str
    source: str  # "postgresql" | "memory" (never a provider name)
    matched: int  # points found within window/fetch cap, before downsampling
    count: int  # points actually returned
    downsampled: bool
    points: list[Position]


def _resolve(state: CurrentStateService, entity_id: str) -> Optional[Entity]:
    """Resolve a full id or a bare identifier (``DEMO001`` -> ``aircraft:...``)."""
    return (
        state.get_entity(entity_id)
        or state.get_entity(f"aircraft:{entity_id}")
        or state.get_entity(f"ship:{entity_id}")
    )


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    """Treat naive timestamps as UTC (JS clients may omit the offset)."""
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _downsample(points: list[Position], limit: int) -> list[Position]:
    """Evenly reduce ``points`` to at most ``limit``, keeping first and last."""
    if len(points) <= limit:
        return points
    stride = math.ceil(len(points) / limit)
    sampled = points[::stride]
    if sampled[-1] is not points[-1]:
        if len(sampled) >= limit:
            sampled[-1] = points[-1]  # swap, never exceed the limit
        else:
            sampled.append(points[-1])
    return sampled


@router.get("/history/{entity_id}", response_model=HistoryTrack)
def get_history(
    entity_id: str,
    request: Request,
    since: Optional[datetime] = Query(None, description="inclusive window start"),
    until: Optional[datetime] = Query(None, description="inclusive window end"),
    limit: Optional[int] = Query(None, ge=1, description="max points to return"),
) -> HistoryTrack:
    """Return a bounded, optionally windowed historical track for one entity."""
    since, until = _aware(since), _aware(until)
    if since is not None and until is not None and since > until:
        raise HTTPException(status_code=400, detail="since must not be after until")

    settings = request.app.state.settings
    max_points = settings.history_api_max_points
    page_limit = min(limit or settings.history_api_default_limit, max_points)

    state: CurrentStateService = request.app.state.current_state
    entity = _resolve(state, entity_id)
    lookup_id = entity.id if entity else entity_id

    database = request.app.state.database
    points: list[Position] = []
    source = "memory"
    if database.name != "none":
        # Blocking DB call; sync handler runs in FastAPI's worker thread.
        points = database.load_track(
            lookup_id, since=since, until=until, limit=max_points
        )
        if points:
            source = "postgresql"
    if not points:
        points = request.app.state.runtime.history.track(lookup_id)
        if since is not None:
            points = [p for p in points if p.timestamp >= since]
        if until is not None:
            points = [p for p in points if p.timestamp <= until]

    points.sort(key=lambda p: p.timestamp)
    matched = len(points)
    if matched == 0 and entity is None:
        raise HTTPException(status_code=404, detail="no history for entity")

    points = _downsample(points, page_limit)
    return HistoryTrack(
        entity_id=lookup_id,
        source=source,
        matched=matched,
        count=len(points),
        downsampled=len(points) < matched,
        points=points,
    )
