"""Application runtime assembly.

Wires the pipeline for a running instance:

    settings -> clock -> providers -> simulation -> current state

The API layer reads from ``current_state`` only; it never touches providers.
OpenSky and AISHub are wired for ``APP_MODE=production`` (see
:func:`build_providers`); local receivers arrive in later phases. Demo/local
always use the deterministic mocks so the app stays runnable fully offline.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.clock import Clock, build_clock
from app.config import AppMode, Settings
from app.history import HistoryService, sample_track
from app.providers.aishub import AishubProvider
from app.providers.base import AircraftProvider, ShipProvider
from app.providers.local_ais import LocalAISProvider
from app.providers.mock_aircraft import MockAircraftProvider
from app.providers.mock_ship import MockShipProvider
from app.providers.opensky import OpenSkyProvider
from app.providers.safe import SafeAircraftProvider, SafeShipProvider
from app.simulation import DemoSimulation, SimulationSnapshot
from app.state import CurrentStateService

logger = logging.getLogger(__name__)


@dataclass
class AppRuntime:
    """Container for the assembled runtime components."""

    settings: Settings
    clock: Clock
    aircraft_provider: AircraftProvider
    ship_provider: ShipProvider
    simulation: DemoSimulation
    current_state: CurrentStateService
    history: HistoryService

    def ingest(self, snapshot: SimulationSnapshot) -> None:
        """Push a simulation snapshot into the current state and history."""
        self.current_state.update(
            snapshot.entities, snapshot.positions, snapshot.timestamp
        )
        self.history.record(snapshot.positions)

    def tick(self) -> SimulationSnapshot:
        """Advance the simulation one step and update the current state."""
        snapshot = self.simulation.step()
        self.ingest(snapshot)
        return snapshot

    def backfill_history(self) -> None:
        """Seed bounded, deterministic trails from providers that can sample
        past instants (the mocks); live providers have no trail to sample."""
        at = self.clock.now()
        count = self.settings.history_sample_count
        interval = self.settings.history_sample_interval_s
        tracks: dict = {}
        for provider in (self.aircraft_provider, self.ship_provider):
            if callable(getattr(provider, "position_at", None)):
                tracks.update(sample_track(provider, at, count, interval))
        self.history.load(tracks)


def build_providers(settings: Settings, clock: Clock) -> tuple[AircraftProvider, ShipProvider]:
    """Construct the configured providers."""
    aircraft: AircraftProvider
    if settings.app_mode is not AppMode.production:
        if settings.opensky_enabled:
            logger.warning(
                "OPENSKY_ENABLED is ignored: APP_MODE=%s always uses mock "
                "providers (OpenSky is production-only)",
                settings.app_mode.value,
            )
        aircraft = MockAircraftProvider(clock, seed=settings.mock_seed)
    elif settings.opensky_enabled and settings.opensky_client_id.strip():
        aircraft = OpenSkyProvider(settings)
    else:
        reason = (
            "missing OPENSKY_CLIENT_ID"
            if settings.opensky_enabled
            else "OPENSKY_ENABLED is false"
        )
        logger.warning(
            "OpenSky not active (%s); using the mock aircraft provider", reason
        )
        aircraft = MockAircraftProvider(clock, seed=settings.mock_seed)

    return SafeAircraftProvider(aircraft), SafeShipProvider(_build_ship_provider(settings, clock))


def _build_ship_provider(settings: Settings, clock: Clock) -> ShipProvider:
    """Ships: local receiver, AISHub, or mock - in that opt-in order.

    * ``APP_MODE=local`` + ``LOCAL_AIS_ENABLED=true`` -> LocalAISProvider
      (empty snapshots until a receiver is attached; no hardware required).
    * ``APP_MODE=production`` + ``AISHUB_ENABLED=true`` + username -> AISHub.
    * Otherwise (demo always) -> the deterministic mock.
    """
    if settings.app_mode is AppMode.local and settings.local_ais_enabled:
        return LocalAISProvider(settings)
    if settings.app_mode is not AppMode.production:
        if settings.aishub_enabled:
            logger.warning(
                "AISHUB_ENABLED is ignored: APP_MODE=%s always uses mock ships",
                settings.app_mode.value,
            )
        if settings.local_ais_enabled:
            logger.warning(
                "LOCAL_AIS_ENABLED is ignored: APP_MODE=%s never uses the "
                "local receiver (local mode only)",
                settings.app_mode.value,
            )
        return MockShipProvider(clock, seed=settings.mock_seed)
    if settings.aishub_enabled and settings.aishub_username.strip():
        return AishubProvider(settings)
    reason = (
        "missing AISHUB_USERNAME"
        if settings.aishub_enabled
        else "AISHUB_ENABLED is false"
    )
    logger.warning("AISHub not active (%s); using the mock ships", reason)
    return MockShipProvider(clock, seed=settings.mock_seed)


def build_runtime(settings: Settings) -> AppRuntime:
    """Assemble a complete runtime for ``settings``."""
    clock = build_clock(settings)
    aircraft_provider, ship_provider = build_providers(settings, clock)
    simulation = DemoSimulation.from_settings(
        clock, aircraft_provider, ship_provider, settings
    )
    history = HistoryService(max_points_per_entity=settings.history_max_points)
    runtime = AppRuntime(
        settings=settings,
        clock=clock,
        aircraft_provider=aircraft_provider,
        ship_provider=ship_provider,
        simulation=simulation,
        current_state=CurrentStateService(),
        history=history,
    )
    runtime.backfill_history()
    return runtime
