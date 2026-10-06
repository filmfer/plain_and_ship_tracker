"""Local AIS receiver (APP_MODE=local opt-in; no hardware required)."""

from __future__ import annotations

import logging
import time
from typing import Callable, Optional

from app.config import Settings
from app.models import Entity, EntityType, Position, Source
from app.providers.base import AISProvider

logger = logging.getLogger(__name__)

ReceiverSnapshot = tuple[list[Entity], list[Position]]
Receiver = Callable[[], ReceiverSnapshot]


class LocalAISProvider(AISProvider):
    """Receiver adapter: injectable source, source=local_ais output."""

    def __init__(
        self,
        settings: Settings,
        *,
        receiver: Optional[Receiver] = None,
        now: Optional[Callable[[], float]] = None,
    ) -> None:
        self._receiver = receiver
        self._now = now or time.monotonic
        self._ttl = max(settings.local_ais_cache_ttl_s, 0.0)
        self._fetched_at: Optional[float] = None
        self._entities: list[Entity] = []
        self._positions: list[Position] = []
        if receiver is None:
            logger.info("LocalAISProvider has no receiver: empty snapshots")

    @property
    def has_receiver(self) -> bool:
        """Whether a local feed is attached (False = empty snapshots)."""
        return self._receiver is not None

    def get_entities(self) -> list[Entity]:
        """Return locally received ships (empty when no receiver)."""
        self._ensure_fresh()
        return list(self._entities)

    def get_positions(self) -> list[Position]:
        """Return locally received positions (empty when no receiver)."""
        self._ensure_fresh()
        return list(self._positions)

    def _ensure_fresh(self) -> None:
        if self._receiver is None:
            return
        now = self._now()
        if self._fetched_at is not None and now - self._fetched_at < self._ttl:
            return
        self._poll()
        self._fetched_at = now

    def _poll(self) -> None:
        assert self._receiver is not None
        try:
            entities, positions = self._receiver()
        except Exception:  # noqa: BLE001 - faults must not break ticks
            logger.warning("Local AIS receiver poll failed", exc_info=True)
            return
        self._entities = [
            entity.model_copy(update={"source": Source.local_ais})
            for entity in entities
            if entity.type is EntityType.ship
        ]
        known = {entity.id for entity in self._entities}
        self._positions = [
            position.model_copy(update={"source": Source.local_ais})
            for position in positions
            if position.entity_id in known
        ]
