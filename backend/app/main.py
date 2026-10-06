"""FastAPI application entrypoint.

Assembles the runtime (clock -> providers -> simulation -> current state),
seeds the current state immediately, and runs the simulation loop in the
background. Each tick pushes a diff of changed positions to the ``/ws/live``
WebSocket hub.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.aircraft import router as aircraft_router
from app.api.alerts import router as alerts_router
from app.api.history import router as history_router
from app.api.live import router as live_router
from app.api.ship import router as ship_router
from app.config import Settings, get_settings
from app.db import NullDatabase, build_database
from app.history_write import HistoryWriteService
from app.live import LiveHub
from app.runtime import build_runtime
from app.simulation import SimulationSnapshot
from app.state_cache import MemoryStateCache, build_state_cache


def create_app(settings: Settings | None = None) -> FastAPI:
    """Application factory.

    Accepting an explicit ``settings`` keeps the app easily testable with an
    isolated configuration.
    """
    settings = settings or get_settings()
    runtime = build_runtime(settings)
    # Seed the current state so endpoints respond immediately (no waiting a tick).
    runtime.tick()
    live_hub = LiveHub(runtime.current_state, settings, runtime.clock)

    async def on_tick(snapshot: SimulationSnapshot) -> None:
        runtime.ingest(snapshot)
        await live_hub.publish(snapshot.positions)
        # Mirror the current entity state into the cache (Redis when available,
        # in-memory fallback otherwise). See app.state_cache.
        await app.state.state_cache.save(runtime.current_state.snapshot())
        # Persist meaningful position changes (threshold-gated). See
        # app.history_write. Runs in a thread: DB writes are blocking.
        await asyncio.to_thread(
            app.state.history_writer.record, snapshot.entities, snapshot.positions
        )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # Redis is optional: probe once and fall back to the in-memory cache so
        # demo/local startup never blocks or fails when Redis is missing.
        app.state.state_cache = await build_state_cache(settings)
        # PostgreSQL is optional too: empty/unreachable DATABASE_URL yields the
        # NullDatabase (graceful demo fallback), never a startup failure.
        app.state.database = await build_database(settings)
        app.state.history_writer = HistoryWriteService(app.state.database, settings)
        stop_event = asyncio.Event()
        task = asyncio.create_task(
            runtime.simulation.run(on_tick=on_tick, stop_event=stop_event)
        )
        try:
            yield
        finally:
            stop_event.set()
            await task
            await app.state.state_cache.close()
            app.state.database.close()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
    )

    app.state.runtime = runtime
    app.state.current_state = runtime.current_state
    app.state.live_hub = live_hub
    app.state.settings = settings
    # Placeholders until lifespan swaps in the configured (DB/Redis-backed)
    # services; on_tick only runs once the lifespan has installed the real ones.
    app.state.state_cache = MemoryStateCache()
    app.state.database = NullDatabase()
    app.state.history_writer = HistoryWriteService(app.state.database, settings)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(aircraft_router)
    app.include_router(ship_router)
    app.include_router(live_router)
    app.include_router(alerts_router)

    app.include_router(history_router)

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        """Liveness/readiness probe with mode transparency."""
        return {
            "status": "ok",
            "app_mode": settings.app_mode.value,
            "version": settings.app_version,
        }

    return app


app = create_app()
