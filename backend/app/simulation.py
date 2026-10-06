"""Single deterministic demo simulation loop.

One loop drives the whole simulation: each tick it refreshes the positions of
*all* mock aircraft and ships by asking the providers for their normalized
positions at the current simulated time. There is deliberately only one
coroutine (no per-entity tasks), and the loop sleeps between ticks so it stays
cheap on CPU.

Collector role: the same loop is the pipeline's collector stage for **any**
``AircraftProvider``/``ShipProvider`` (mock or OpenSky). It polls the provider
each tick - off the event loop, so blocking provider I/O never stalls
WebSockets - and the resulting snapshot feeds current state, the state cache
(Redis), history and the WebSocket hub. Raw provider payloads never leave the
provider boundary: normalization happens there.

Determinism: providers compute positions as pure functions of the injected
:class:`~app.clock.Clock`. Because that clock is a :class:`SimulationClock`
under ``APP_MODE=demo``/``local``, advancing it (manually or via
``MOCK_TIME_MULTIPLIER``) yields reproducible results.
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from app.clock import Clock
from app.config import Settings
from app.models import Entity, Position
from app.providers.base import AircraftProvider, ShipProvider


@dataclass(frozen=True)
class SimulationSnapshot:
    """Immutable view of the simulation at one instant."""

    timestamp: datetime
    entities: tuple[Entity, ...]
    positions: tuple[Position, ...]

    @property
    def positions_by_id(self) -> dict[str, Position]:
        """Positions keyed by ``entity_id`` for quick lookup."""
        return {position.entity_id: position for position in self.positions}


TickCallback = Callable[[SimulationSnapshot], object]


class DemoSimulation:
    """Coordinates the mock aircraft and ship providers on a single clock."""

    def __init__(
        self,
        clock: Clock,
        aircraft_provider: AircraftProvider,
        ship_provider: ShipProvider,
        tick_interval: float = 1.0,
    ) -> None:
        self._clock = clock
        self._aircraft = aircraft_provider
        self._ships = ship_provider
        self.tick_interval = tick_interval

        self._entities: tuple[Entity, ...] = ()
        self._last_snapshot: Optional[SimulationSnapshot] = None
        self._tick_count = 0

    @classmethod
    def from_settings(
        cls,
        clock: Clock,
        aircraft_provider: AircraftProvider,
        ship_provider: ShipProvider,
        settings: Settings,
    ) -> "DemoSimulation":
        """Build a simulation paced by the configured tick interval."""
        return cls(
            clock=clock,
            aircraft_provider=aircraft_provider,
            ship_provider=ship_provider,
            tick_interval=settings.mock_tick_interval,
        )

    # -- state --------------------------------------------------------------

    @property
    def tick_count(self) -> int:
        """Number of ticks processed so far."""
        return self._tick_count

    @property
    def last_snapshot(self) -> Optional[SimulationSnapshot]:
        """The most recent snapshot, or ``None`` before the first tick."""
        return self._last_snapshot

    # -- core ---------------------------------------------------------------

    def _ensure_entities(self) -> None:
        """Refresh identities every tick.

        Mocks return the same static set (snapshots stay identical to the
        cached-entities behaviour); live providers change over time, so
        aircraft appearing later in a session register as well.
        """
        self._entities = tuple(
            self._aircraft.get_entities() + self._ships.get_entities()
        )

    def step(self) -> SimulationSnapshot:
        """Advance the simulation by one tick and return the new snapshot."""
        self._ensure_entities()
        positions = tuple(
            self._aircraft.get_positions() + self._ships.get_positions()
        )
        self._tick_count += 1
        self._last_snapshot = SimulationSnapshot(
            timestamp=self._clock.now(),
            entities=self._entities,
            positions=positions,
        )
        return self._last_snapshot

    async def run(
        self,
        *,
        interval: Optional[float] = None,
        on_tick: Optional[TickCallback] = None,
        stop_event: Optional[asyncio.Event] = None,
    ) -> None:
        """Run the loop until ``stop_event`` is set.

        A single coroutine iterates all entities each tick; the loop sleeps for
        ``interval`` real seconds between ticks to keep CPU usage low. ``on_tick``
        may be a plain function or an async function.
        """
        pacing = self.tick_interval if interval is None else interval
        while stop_event is None or not stop_event.is_set():
            # Provider I/O (live adapters) runs in a worker thread so the
            # event loop keeps serving WebSockets; mocks are pure functions.
            snapshot = await asyncio.to_thread(self.step)
            if on_tick is not None:
                result = on_tick(snapshot)
                if inspect.isawaitable(result):
                    await result
            await asyncio.sleep(pacing)
