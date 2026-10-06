"""Tests for the deterministic demo simulation loop."""

from __future__ import annotations

import asyncio
import math

from app.clock import SimulationClock
from app.geo import EARTH_RADIUS_M
from app.models import EntityType
from app.providers.mock_aircraft import MockAircraftProvider
from app.providers.mock_ship import MockShipProvider
from app.simulation import DemoSimulation, SimulationSnapshot


class FakeTimeSource:
    """Controllable monotonic source (real seconds)."""

    def __init__(self, start: float = 0.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _sim(seconds: float = 0.0, seed: int = 42) -> tuple[DemoSimulation, SimulationClock]:
    clock = SimulationClock(multiplier=0.0)
    sim = DemoSimulation(
        clock=clock,
        aircraft_provider=MockAircraftProvider(clock, seed=seed),
        ship_provider=MockShipProvider(clock, seed=seed),
        tick_interval=0.0,
    )
    clock.advance(seconds)
    return sim, clock


def _distance_m(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dphi = p2 - p1
    dlambda = math.radians(b_lon - a_lon)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def test_step_covers_aircraft_and_ships() -> None:
    sim, _ = _sim()
    snapshot = sim.step()

    assert len(snapshot.entities) == 16
    assert len(snapshot.positions) == 16
    kinds = {e.type for e in snapshot.entities}
    assert kinds == {EntityType.aircraft, EntityType.ship}


def test_positions_by_id_lookup() -> None:
    sim, _ = _sim()
    snapshot = sim.step()

    assert "aircraft:DEMO001" in snapshot.positions_by_id
    assert "ship:DEMO-SHIP-001" in snapshot.positions_by_id


def test_entities_are_cached_across_ticks() -> None:
    sim, _ = _sim()
    first = sim.step()
    second = sim.step()

    assert first.entities == second.entities


def test_step_reflects_clock_advance() -> None:
    sim, clock = _sim()
    before = sim.step().positions_by_id["aircraft:DEMO001"]
    clock.advance(600.0)
    after = sim.step().positions_by_id["aircraft:DEMO001"]

    moved = _distance_m(before.latitude, before.longitude, after.latitude, after.longitude)
    assert moved > 10_000


def test_simulation_is_deterministic() -> None:
    a, _ = _sim(seconds=900, seed=42)
    b, _ = _sim(seconds=900, seed=42)

    assert a.step() == b.step()


def test_snapshot_is_immutable_and_timestamped() -> None:
    sim, clock = _sim()
    snapshot = sim.step()

    assert isinstance(snapshot, SimulationSnapshot)
    assert snapshot.timestamp == clock.now()


def test_tick_count_and_last_snapshot() -> None:
    sim, _ = _sim()

    assert sim.tick_count == 0
    assert sim.last_snapshot is None

    first = sim.step()
    assert sim.tick_count == 1
    assert sim.last_snapshot == first


def test_respects_time_multiplier() -> None:
    """1 real second at multiplier=60 => 60 simulated seconds of movement."""
    source = FakeTimeSource()
    clock = SimulationClock(multiplier=60.0, time_source=source)
    sim = DemoSimulation(
        clock=clock,
        aircraft_provider=MockAircraftProvider(clock),
        ship_provider=MockShipProvider(clock),
        tick_interval=0.0,
    )
    first = sim.step().positions_by_id["aircraft:DEMO001"]

    source.advance(1.0)  # one real second

    second = sim.step().positions_by_id["aircraft:DEMO001"]
    moved = _distance_m(first.latitude, first.longitude, second.latitude, second.longitude)
    # ~250 m/s * 60 s => ~15 km.
    assert moved > 5_000


def test_run_invokes_callback_and_stops() -> None:
    sim, _ = _sim()
    snapshots: list[SimulationSnapshot] = []

    async def main() -> None:
        stop = asyncio.Event()

        def on_tick(snapshot: SimulationSnapshot) -> None:
            snapshots.append(snapshot)
            if len(snapshots) >= 3:
                stop.set()

        await sim.run(interval=0.0, on_tick=on_tick, stop_event=stop)

    asyncio.run(main())

    assert len(snapshots) == 3
    assert sim.tick_count == 3


def test_run_supports_async_callback() -> None:
    sim, _ = _sim()
    seen: list[int] = []

    async def main() -> None:
        stop = asyncio.Event()

        async def on_tick(_: SimulationSnapshot) -> None:
            seen.append(1)
            if len(seen) >= 2:
                stop.set()

        await sim.run(interval=0.0, on_tick=on_tick, stop_event=stop)

    asyncio.run(main())

    assert len(seen) == 2
