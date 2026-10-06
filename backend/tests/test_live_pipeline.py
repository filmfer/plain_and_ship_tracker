"""End-to-end live aircraft pipeline tests with **mocked** OpenSky responses.

Covers: OpenSky (mocked) -> collector loop -> normalization -> current state ->
state cache (Redis/memory) -> WebSocket. Real requests stay disabled until
``OPENSKY_ENABLED`` + ``APP_MODE=production`` + credentials are configured, and
the frontend must never talk to OpenSky itself.
"""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app import runtime as runtime_module
from app.clock import SimulationClock
from app.config import Settings
from app.main import create_app
from app.models import Entity, EntityType, Position, Source
from app.providers.base import AircraftProvider
from app.providers.mock_ship import MockShipProvider
from app.providers.opensky import OpenSkyProvider
from app.simulation import DemoSimulation
from tests.test_opensky import Recorder, ok_handler

TS = datetime(2026, 1, 1, tzinfo=timezone.utc)


def make_settings(**overrides) -> Settings:
    """Production + fully enabled OpenSky - but every response is mocked."""
    kwargs = {
        "_env_file": None,
        "database_url": "",
        "app_mode": "production",
        "opensky_enabled": True,
        "opensky_client_id": "cid",
        "opensky_client_secret": "s3cr3t",
        "mock_tick_interval": 0.05,
        "redis_timeout_s": 0.2,
    }
    kwargs.update(overrides)
    return Settings(**kwargs)


class GrowingProvider(AircraftProvider):
    """Live-style provider whose aircraft set grows over time."""

    def __init__(self) -> None:
        self.revealed = 1

    def get_entities(self) -> list[Entity]:
        return [
            Entity(
                id=f"aircraft:NEW{i}",
                type=EntityType.aircraft,
                identifier=f"LIVE{i:03d}",
                source=Source.opensky,
                icao24=f"ab{i:04d}",
            )
            for i in range(self.revealed)
        ]

    def get_positions(self) -> list[Position]:
        return [
            Position(
                entity_id=f"aircraft:NEW{i}",
                timestamp=TS,
                latitude=10.0 + i,
                longitude=-30.0,
                source=Source.opensky,
            )
            for i in range(self.revealed)
        ]


class ThreadProbeProvider(AircraftProvider):
    """Records which thread the collector polls it from."""

    def __init__(self) -> None:
        self.thread_ids: list[int] = []

    def get_entities(self) -> list[Entity]:
        self.thread_ids.append(threading.get_ident())
        return []

    def get_positions(self) -> list[Position]:
        return []


# -- collector stage ----------------------------------------------------------


def test_collector_registers_aircraft_that_appear_later():
    clock = SimulationClock(multiplier=0.0)
    provider = GrowingProvider()
    sim = DemoSimulation(
        clock=clock,
        aircraft_provider=provider,
        ship_provider=MockShipProvider(clock, seed=42),
        tick_interval=0.0,
    )
    first = sim.step()
    assert "aircraft:NEW0" in {e.id for e in first.entities}
    assert "aircraft:NEW1" not in {e.id for e in first.entities}

    provider.revealed = 3  # aircraft join the airspace later
    second = sim.step()
    ids = {e.id for e in second.entities}
    assert {"aircraft:NEW0", "aircraft:NEW1", "aircraft:NEW2"} <= ids
    assert any(e.id.startswith("ship:") for e in second.entities)  # ships intact


def test_collector_polls_providers_off_the_event_loop_thread():
    """Blocking provider I/O must never stall the WebSocket event loop."""
    clock = SimulationClock(multiplier=0.0)
    probe = ThreadProbeProvider()
    sim = DemoSimulation(
        clock=clock,
        aircraft_provider=probe,
        ship_provider=MockShipProvider(clock, seed=42),
        tick_interval=0.0,
    )
    loop_thread = threading.get_ident()  # asyncio.run drives the loop here

    async def main() -> None:
        stop = asyncio.Event()

        async def on_tick(_) -> None:
            stop.set()

        await sim.run(interval=0.0, on_tick=on_tick, stop_event=stop)

    asyncio.run(main())
    assert probe.thread_ids  # the provider was polled
    assert probe.thread_ids[0] != loop_thread  # but from a worker thread


# -- architecture guard -------------------------------------------------------


def test_frontend_never_calls_opensky():
    """The browser talks only to our backend; OpenSky stays server-side."""
    root = Path(__file__).resolve().parents[2] / "frontend" / "src"
    offenders = [
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file() and "opensky" in path.read_text(errors="ignore").lower()
    ]
    assert offenders == []


# -- full pipeline with mocked responses --------------------------------------


def test_live_opensky_pipeline_reaches_state_cache_and_websocket(monkeypatch):
    def factory(settings: Settings) -> OpenSkyProvider:
        # Mocked transport only - no real request is ever made.
        return OpenSkyProvider(
            settings,
            transport=httpx.MockTransport(Recorder(ok_handler)),
            sleep=lambda _: None,
        )

    monkeypatch.setattr(runtime_module, "OpenSkyProvider", factory)
    app = create_app(make_settings())

    with TestClient(app) as client:
        # OpenSky -> collector -> normalization -> current state (HTTP view)
        data = client.get("/api/aircraft").json()
        by_id = {item["entity"]["id"]: item for item in data}
        opensky = by_id["aircraft:c0ffee"]
        assert opensky["entity"]["source"] == "opensky"
        assert opensky["position"]["source"] == "opensky"
        assert opensky["entity"]["callsign"] == "CONAIR"

        # Mock mode is preserved for everything else (ships stay mock until AIS)
        ship_data = client.get("/api/ships").json()
        ship_sources = {item["entity"]["source"] for item in ship_data}
        assert ship_sources == {"mock"}

        # current state -> state cache (Redis when reachable, memory fallback)
        saved: list = []
        original_save = app.state.state_cache.save

        async def spy(snapshot) -> None:
            saved.append(snapshot)
            await original_save(snapshot)

        app.state.state_cache.save = spy
        for _ in range(100):
            if saved:
                break
            time.sleep(0.02)
        assert saved, "no state-cache save happened within 2s"
        assert "aircraft:c0ffee" in {entity.id for entity in saved[0].entities}

        # state -> WebSocket: late joiners get the full normalized snapshot
        with client.websocket_connect("/ws/live") as ws:
            snapshot = ws.receive_json()
            assert snapshot["type"] == "snapshot"
            ws_entities = {e["id"]: e for e in snapshot["entities"]}
            assert ws_entities["aircraft:c0ffee"]["source"] == "opensky"
            ws_positions = {p["entity_id"]: p for p in snapshot["positions"]}
            assert ws_positions["aircraft:c0ffee"]["source"] == "opensky"

