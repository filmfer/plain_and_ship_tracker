"""Deterministic mock history (trails).

For demo mode we do not have a database; instead we derive a small, bounded set
of past positions from the mock providers. Because provider movement is a pure
function of simulated time, sampling at fixed past instants is fully
reproducible and needs no external data.

``HistoryService`` is an in-memory, bounded per-entity store suitable for
drawing trails. It is the seam where PostgreSQL/PostGIS storage will be plugged
in later.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
from typing import Iterable, Protocol

from app.models import Position


class _Samplable(Protocol):
    """A provider able to report positions at an arbitrary instant."""

    def position_at(self, when: datetime) -> list[Position]:
        ...


def sample_track(
    provider: _Samplable,
    at: datetime,
    count: int,
    interval_s: float,
) -> dict[str, list[Position]]:
    """Sample ``count`` past positions per entity, ending at ``at`` (inclusive).

    Returns positions oldest-first so the result can be drawn directly as a
    trail. Sampling is deterministic for a given provider state and ``at``.
    """
    if count <= 0:
        return {}
    tracks: dict[str, list[Position]] = {}
    for step in range(count - 1, -1, -1):
        moment = at - timedelta(seconds=step * interval_s)
        for position in provider.position_at(moment):
            tracks.setdefault(position.entity_id, []).append(position)
    return tracks


class HistoryService:
    """Bounded, in-memory position history keyed by entity id."""

    def __init__(self, max_points_per_entity: int = 200) -> None:
        self._max_points = max_points_per_entity
        self._tracks: dict[str, deque[Position]] = {}

    def __len__(self) -> int:
        return len(self._tracks)

    def record(self, positions: Iterable[Position]) -> None:
        """Append positions, skipping an exact timestamp repeat per entity."""
        for position in positions:
            track = self._tracks.get(position.entity_id)
            if track is None:
                track = deque(maxlen=self._max_points)
                self._tracks[position.entity_id] = track
            if track and track[-1].timestamp == position.timestamp:
                continue
            track.append(position)

    def load(self, tracks: dict[str, list[Position]]) -> None:
        """Replace stored tracks (used for the initial deterministic backfill)."""
        self._tracks = {
            entity_id: deque(positions, maxlen=self._max_points)
            for entity_id, positions in tracks.items()
        }

    def track(self, entity_id: str) -> list[Position]:
        """Return the trail for ``entity_id`` (oldest-first); empty if unknown."""
        return list(self._tracks.get(entity_id, ()))

    def tracks(self) -> dict[str, list[Position]]:
        """Return a copy of every stored trail."""
        return {entity_id: list(track) for entity_id, track in self._tracks.items()}

    def clear(self) -> None:
        self._tracks.clear()
