"""Tests for the /ws/live WebSocket (snapshot + efficient diffed updates)."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.config import Settings
from app.live import LiveHub
from app.main import create_app
from app.models import DataQuality, Entity, EntityType, Position, Source
from app.state import CurrentStateService

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)

AIRCRAFT = Entity(
    id="aircraft:DEMO001", type=EntityType.aircraft, identifier="DEMO001", source=Source.mock
)
SHIP = Entity(
    id="ship:DEMO-SHIP-001",
    type=EntityType.ship,
    identifier="DEMO-SHIP-001",
    source=Source.mock,
)


def _position(
    entity_id: str = "aircraft:DEMO001",
    lat: float = 38.0,
    lon: float = -28.0,
    second: int = 0,
) -> Position:
    return Position(
        entity_id=entity_id,
        timestamp=T0 + timedelta(seconds=second),
        latitude=lat,
        longitude=lon,
        source=Source.mock,
        altitude=10000.0,
        quality=DataQuality.good,
    )


def _state() -> CurrentStateService:
    state = CurrentStateService()
    state.update(
        [AIRCRAFT, SHIP],
        [_position(), _position("ship:DEMO-SHIP-001", 36.0, -25.0)],
        T0,
    )
    return state


class FakeWebSocket:
    """Minimal stand-in for a Starlette WebSocket (unit tests, no I/O)."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.accepted = False
        self.sent: list[dict] = []

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, message: dict) -> None:
        if self.fail:
            raise RuntimeError("connection closed")
        self.sent.append(message)


# --- LiveHub unit tests -----------------------------------------------------


def test_connect_accepts_and_sends_full_snapshot():
    hub = LiveHub(_state())
    ws = FakeWebSocket()

    asyncio.run(hub.connect(ws))

    assert ws.accepted
    assert hub.client_count == 1
    assert len(ws.sent) == 1
    snapshot = ws.sent[0]
    assert snapshot["type"] == "snapshot"
    assert len(snapshot["entities"]) == 2
    assert len(snapshot["positions"]) == 2
    assert snapshot["timestamp"] is not None


def test_publish_sends_normalized_batch_with_entity_type():
    hub = LiveHub(_state())
    ws = FakeWebSocket()
    asyncio.run(hub.connect(ws))
    ws.sent.clear()

    asyncio.run(hub.publish([_position(lat=38.5)]))

    assert len(ws.sent) == 1
    message = ws.sent[0]
    assert message["type"] == "position_updates"
    assert len(message["updates"]) == 1
    update = message["updates"][0]
    assert update["entity_id"] == "aircraft:DEMO001"
    assert update["entity_type"] == "aircraft"
    assert update["latitude"] == 38.5
    assert update["source"] == "mock"


def test_publish_skips_unchanged_positions():
    hub = LiveHub(_state())
    ws = FakeWebSocket()
    asyncio.run(hub.connect(ws))
    ws.sent.clear()

    asyncio.run(hub.publish([_position(lat=38.5)]))  # aircraft changed
    assert len(ws.sent) == 1
    ws.sent.clear()

    asyncio.run(hub.publish([_position(lat=38.5)]))  # identical -> no message
    assert ws.sent == []

    asyncio.run(
        hub.publish([_position(lat=38.5), _position("ship:DEMO-SHIP-001", 36.1, -25.0)])
    )

    assert len(ws.sent) == 1  # only the ship changed
    assert ws.sent[0]["updates"][0]["entity_type"] == "ship"


def test_publish_without_clients_still_advances_diff():
    hub = LiveHub(_state())
    asyncio.run(hub.publish([_position(lat=39.0)]))  # nobody listening

    ws = FakeWebSocket()
    asyncio.run(hub.connect(ws))
    ws.sent.clear()
    asyncio.run(hub.publish([_position(lat=39.0)]))  # unchanged since last diff

    assert ws.sent == []  # the snapshot already carried it


def test_dead_client_is_dropped():
    hub = LiveHub(_state())
    dead = FakeWebSocket(fail=True)
    alive = FakeWebSocket()
    asyncio.run(hub.connect(dead))
    asyncio.run(hub.connect(alive))
    alive.sent.clear()

    asyncio.run(hub.publish([_position(lat=40.0)]))

    assert hub.client_count == 1
    assert dead not in hub._clients
    assert len(alive.sent) == 1


def test_many_changed_entities_share_one_broadcast():
    """Batching: a full 16-entity tick fans out as ONE message, not 16.

    Regression guard for PROMPT 43/44: ``publish`` must collect every changed
    position of the simulation tick into a single ``position_updates`` batch
    (one ``send_json`` per client), preserving the normalized structure.
    """
    state = _state()
    # Grow the state to the full demo fleet: 8 aircraft + 8 ships.
    entities = [AIRCRAFT, SHIP]
    positions = [_position(), _position("ship:DEMO-SHIP-001", 36.0, -25.0)]
    for i in range(2, 9):
        entities.append(
            Entity(
                id=f"aircraft:DEMO{i:03d}",
                type=EntityType.aircraft,
                identifier=f"DEMO{i:03d}",
                source=Source.mock,
            )
        )
        positions.append(_position(f"aircraft:DEMO{i:03d}", lat=38.0 + i * 0.1))
        entities.append(
            Entity(
                id=f"ship:DEMO-SHIP-{i:03d}",
                type=EntityType.ship,
                identifier=f"DEMO-SHIP-{i:03d}",
                source=Source.mock,
            )
        )
        positions.append(
            _position(f"ship:DEMO-SHIP-{i:03d}", 36.0 + i * 0.1, -25.0)
        )
    state.update(entities, positions, T0)

    hub = LiveHub(state)
    ws = FakeWebSocket()
    asyncio.run(hub.connect(ws))
    ws.sent.clear()

    moved = [
        _position(entity.id, lat=39.0, second=1)
        if entity.type is EntityType.aircraft
        else _position(entity.id, lat=37.0, second=1)
        for entity in entities
    ]
    asyncio.run(hub.publish(moved))

    assert len(ws.sent) == 1  # one broadcast, not one message per entity
    message = ws.sent[0]
    assert message["type"] == "position_updates"
    assert len(message["updates"]) == 16
    update = message["updates"][0]
    assert {"entity_id", "entity_type", "latitude", "timestamp"} <= set(update)
    assert update["source"] == "mock"


def test_update_payload_falls_back_to_id_prefix():
    hub = LiveHub(_state())
    position = _position(entity_id="aircraft:UNKNOWN", lat=1.0, lon=1.0)
    key = hub._diff_key(position, hub._type_of(position))
    payload = hub._update_payload(key, position)
    assert payload["entity_type"] == "aircraft"


# --- integration -----------------------------------------------------------


def _fast_app() -> Settings:
    return Settings(_env_file=None, mock_tick_interval=0.05)


def test_ws_live_sends_snapshot_then_live_updates():
    app = create_app(_fast_app())
    with TestClient(app) as client:
        with client.websocket_connect("/ws/live") as ws:
            snapshot = ws.receive_json()
            assert snapshot["type"] == "snapshot"
            assert len(snapshot["entities"]) == 16  # 8 aircraft + 8 ships
            assert len(snapshot["positions"]) == 16

            message = ws.receive_json()
            assert message["type"] == "position_updates"
            update = message["updates"][0]
            assert {"entity_id", "entity_type", "latitude", "longitude", "timestamp"} <= set(
                update
            )
            assert update["entity_type"] in {"aircraft", "ship"}

        # Client is cleaned up shortly after the socket closes.
        for _ in range(100):
            if app.state.live_hub.client_count == 0:
                break
            time.sleep(0.02)
        assert app.state.live_hub.client_count == 0


def test_ws_live_answers_ping_with_pong():
    app = create_app(_fast_app())
    with TestClient(app) as client:
        with client.websocket_connect("/ws/live") as ws:
            ws.receive_json()  # snapshot
            ws.send_text("ping")
            pong = False
            for _ in range(20):
                if ws.receive_json().get("type") == "pong":
                    pong = True
                    break
            assert pong
