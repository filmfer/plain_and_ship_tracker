"""WebSocket live fan-out.

Design notes (efficiency):

* One shared diff, not one per client: every tick the hub serializes only the
  positions that actually changed and fans the resulting *single* message out to
  all connected clients with one ``gather``.
* A client that connects later first receives a full ``snapshot`` so it never
  needs backfill or provider access.
* The payload is normalized (``Position`` + ``entity_type``); providers, modes
  and internal state never cross this boundary.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Iterable, Optional

from fastapi import WebSocket

from app.clock import Clock, RealClock
from app.config import Settings, get_settings
from app.data_state import age_seconds, classify
from app.models import EntityType, Position
from app.state import CurrentStateService, StateSnapshot

logger = logging.getLogger(__name__)

_TYPE_VALUES = {member.value for member in EntityType}


def freshness_fields(
    position: Position, settings: Settings, clock: Clock
) -> dict[str, Any]:
    """Freshness of ``position`` against the application clock.

    Returns plain fields merged into WS payloads (``data_state`` is the enum
    value, ``data_age_s`` the age in seconds or ``None``).
    """
    age = age_seconds(clock.now(), position.timestamp)
    return {
        "data_state": classify(
            age,
            live_s=settings.data_state_live_s,
            recent_s=settings.data_state_recent_s,
            stale_s=settings.data_state_stale_s,
        ).value,
        "data_age_s": age,
    }


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


class LiveHub:
    """Registry of connected WebSocket clients plus diff-based broadcasting."""

    def __init__(
        self,
        state: CurrentStateService,
        settings: Settings | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._state = state
        self._settings = settings or get_settings()
        self._clock = clock or RealClock()
        self._clients: set[WebSocket] = set()
        # Last payload delivered per entity, used to skip unchanged positions.
        self._last_sent: dict[str, dict[str, Any]] = {}

    @property
    def client_count(self) -> int:
        """Number of currently connected clients."""
        return len(self._clients)

    # -- connection lifecycle ------------------------------------------------

    async def connect(self, websocket: WebSocket) -> None:
        """Accept ``websocket`` and send it the current full snapshot."""
        await websocket.accept()
        self._clients.add(websocket)
        await self._send(websocket, self._snapshot_message())

    async def disconnect(self, websocket: WebSocket) -> None:
        self._clients.discard(websocket)

    # -- payloads ------------------------------------------------------------

    def _snapshot_message(self) -> dict[str, Any]:
        snapshot: StateSnapshot = self._state.snapshot()
        return {
            "type": "snapshot",
            "timestamp": _iso(snapshot.updated_at),
            "entities": [entity.model_dump(mode="json") for entity in snapshot.entities],
            "positions": [
                {
                    **position.model_dump(mode="json"),
                    **freshness_fields(position, self._settings, self._clock),
                }
                for position in snapshot.positions
            ],
        }

    def _type_of(self, position: Position) -> str:
        entity = self._state.get_entity(position.entity_id)
        if entity is not None:
            return entity.type.value
        prefix = position.entity_id.split(":", 1)[0]
        return prefix if prefix in _TYPE_VALUES else "unknown"

    def _diff_key(self, position: Position, entity_type: str) -> dict[str, Any]:
        """Stable payload used for change detection (no drifting freshness).

        Freshness (``data_age_s``) is attached at send time, not as part of
        this key: it drifts with the clock, so including it in the comparison
        would make every tick look like a position change and defeat the
        diff-based fan-out.
        """
        payload = position.model_dump(mode="json")
        payload["entity_type"] = entity_type
        return payload

    def _update_payload(
        self, key: dict[str, Any], position: Position
    ) -> dict[str, Any]:
        """Send-time payload: reuse the diff ``key`` and attach freshness once."""
        payload = dict(key)
        payload.update(freshness_fields(position, self._settings, self._clock))
        return payload

    # -- publishing ----------------------------------------------------------

    async def publish(self, positions: Iterable[Position]) -> None:
        """Broadcast a batch containing only the positions that changed."""
        updates: list[dict[str, Any]] = []
        for position in positions:
            entity_type = self._type_of(position)
            key = self._diff_key(position, entity_type)
            previous = self._last_sent.get(position.entity_id)
            self._last_sent[position.entity_id] = key
            if previous == key:
                continue
            updates.append(self._update_payload(key, position))

        if not updates or not self._clients:
            # Nothing to send; the diff is still advanced so a client that
            # connects later only receives real changes after its snapshot.
            return

        await self._broadcast(
            {
                "type": "position_updates",
                "timestamp": max(item["timestamp"] for item in updates),
                "updates": updates,
            }
        )

    async def _broadcast(self, message: dict[str, Any]) -> None:
        clients = list(self._clients)
        if not clients:
            return
        results = await asyncio.gather(
            *(client.send_json(message) for client in clients),
            return_exceptions=True,
        )
        for client, result in zip(clients, results):
            if isinstance(result, BaseException):
                logger.debug("dropping dead websocket client: %s", result)
                self._clients.discard(client)

    async def _send(self, websocket: WebSocket, message: dict[str, Any]) -> None:
        try:
            await websocket.send_json(message)
        except Exception:  # client vanished mid-handshake
            self._clients.discard(websocket)
