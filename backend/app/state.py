"""Current-state service.

Holds the latest known normalized entity and position for every tracked
aircraft and ship, and exposes them to the API layer. The rest of the system
(API, WebSocket, anomaly engine) reads from here rather than talking to
providers directly, so provider details never leak to the frontend.

This is an in-memory implementation; it is intentionally isolated behind a
small interface so it can be backed by Redis in a later phase without touching
callers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional

from app.models import Entity, EntityType, Position


@dataclass(frozen=True)
class StateSnapshot:
    """Immutable view of the current state."""

    updated_at: Optional[datetime]
    entities: tuple[Entity, ...]
    positions: tuple[Position, ...]

    @property
    def positions_by_id(self) -> dict[str, Position]:
        """Positions keyed by ``entity_id``."""
        return {position.entity_id: position for position in self.positions}


class CurrentStateService:
    """In-memory store of the latest normalized entity/position per id."""

    def __init__(self) -> None:
        self._entities: dict[str, Entity] = {}
        self._positions: dict[str, Position] = {}
        self._updated_at: Optional[datetime] = None

    # -- ingestion ----------------------------------------------------------

    def update(
        self,
        entities: Iterable[Entity],
        positions: Iterable[Position],
        updated_at: Optional[datetime] = None,
    ) -> None:
        """Merge normalized provider data into the current state.

        Later reports for the same ``id``/``entity_id`` overwrite earlier ones.
        When ``updated_at`` is omitted it is derived from the newest position
        timestamp.
        """
        for entity in entities:
            self._entities[entity.id] = entity
        for position in positions:
            self._positions[position.entity_id] = position

        if updated_at is not None:
            self._updated_at = updated_at
        elif self._positions:
            self._updated_at = max(p.timestamp for p in self._positions.values())

    def clear(self) -> None:
        """Drop all stored state."""
        self._entities.clear()
        self._positions.clear()
        self._updated_at = None

    # -- accessors ----------------------------------------------------------

    @property
    def updated_at(self) -> Optional[datetime]:
        """Timestamp of the most recent ingested data (or ``None``)."""
        return self._updated_at

    def __len__(self) -> int:
        return len(self._entities)

    def get_entity(self, entity_id: str) -> Optional[Entity]:
        """Return the entity with ``entity_id``, or ``None``."""
        return self._entities.get(entity_id)

    def get_position(self, entity_id: str) -> Optional[Position]:
        """Return the latest position for ``entity_id``, or ``None``."""
        return self._positions.get(entity_id)

    def entities(self, entity_type: Optional[EntityType] = None) -> list[Entity]:
        """Return all entities, optionally filtered by type."""
        values = self._entities.values()
        if entity_type is not None:
            return [e for e in values if e.type is entity_type]
        return list(values)

    def positions(self, entity_type: Optional[EntityType] = None) -> list[Position]:
        """Return all latest positions, optionally filtered by entity type."""
        if entity_type is None:
            return list(self._positions.values())
        return [
            position
            for entity_id, position in self._positions.items()
            if (entity := self._entities.get(entity_id)) is not None
            and entity.type is entity_type
        ]

    def snapshot(self) -> StateSnapshot:
        """Return an immutable snapshot of the current state."""
        return StateSnapshot(
            updated_at=self._updated_at,
            entities=tuple(self._entities.values()),
            positions=tuple(self._positions.values()),
        )
