"""Live WebSocket endpoint.

The frontend subscribes here for normalized updates; it never talks to a
provider directly. Only ``snapshot`` / ``position_updates`` / ``pong`` messages
leave this endpoint.
"""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.live import LiveHub

router = APIRouter(tags=["live"])


@router.websocket("/ws/live")
async def ws_live(websocket: WebSocket) -> None:
    """Stream live normalized positions.

    Sequence: ``snapshot`` (full current state) on connect, then a
    ``position_updates`` batch per simulation tick containing only changed
    positions. ``ping`` text frames are answered with a ``pong`` keepalive.
    """
    hub: LiveHub = websocket.app.state.live_hub
    await hub.connect(websocket)
    try:
        while True:
            text = await websocket.receive_text()
            if text.strip().lower() == "ping":
                await websocket.send_json({"type": "pong"})
    except (WebSocketDisconnect, RuntimeError):
        # Client went away (or the socket was torn down by the server).
        pass
    finally:
        await hub.disconnect(websocket)
