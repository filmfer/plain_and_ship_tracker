"""Deterministic mock ship provider.

Produces 8 clearly-synthetic ships (``DEMO-SHIP-001``..``DEMO-SHIP-008``) with
fictional names, MMSI/IMO-style demo identifiers, callsigns and ship types.

Movement is a pure function of simulated time from the injected
:class:`~app.clock.Clock`, so it is reproducible and fast-forwardable.

Units: speed in m/s (nominal speeds are given in knots and converted), course
over ground and heading in degrees, altitude/vertical rate are ``None`` for
ships. All entities use ``source="mock"``.

Scenarios exercised deterministically:

* NORMAL          - steady straight-line motion.
* STATIONARY      - position frozen (speed 0).
* SIGNAL_LOSS     - updates stop after ``FREEZE_AT_S``; ``signal_age`` grows.
* POSITION_JUMP   - a sudden geographic discontinuity at ``EVENT_AT_S``.
* SPEED_ANOMALY   - an impossible speed at ``EVENT_AT_S``.
* COURSE_ANOMALY  - an abrupt course change at ``EVENT_AT_S``.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.clock import Clock
from app.geo import offset_position
from app.models import DataQuality, Entity, EntityType, Position, Source
from app.providers.base import AISProvider, Scenario

# Nautical mile per hour -> metres per second.
KNOT_TO_MS = 0.514444

# Seconds of simulated time after which scenario events occur.
FREEZE_AT_S = 120.0
EVENT_AT_S = 120.0

# Position-jump magnitude (degrees) and impossible speed (m/s, ~156 kt).
JUMP_LAT_OFFSET = 5.0
JUMP_LON_OFFSET = 5.0
ANOMALOUS_SPEED_MS = 80.0

# Course-anomaly: sharp turn applied at the event time.
COURSE_TURN_DEG = 90.0


@dataclass(frozen=True)
class _Ship:
    """Static description of one synthetic ship."""

    number: int
    name: str
    ship_type: str
    region: str
    scenario: Scenario
    latitude: float
    longitude: float
    speed_kt: float
    course_deg: float

    @property
    def identifier(self) -> str:
        return f"DEMO-SHIP-{self.number:03d}"

    @property
    def entity_id(self) -> str:
        return f"ship:{self.identifier}"

    @property
    def mmsi(self) -> str:
        return f"9990000{self.number:02d}"

    @property
    def imo(self) -> str:
        return f"IMO999000{self.number}"

    @property
    def callsign(self) -> str:
        return f"DEMO{self.number:02d}"

    @property
    def speed_ms(self) -> float:
        return self.speed_kt * KNOT_TO_MS


# Deterministic nominal states (pre-seed). Spread across the globe, with two
# vessels near the Azores for local development.
_NOMINAL_SHIPS: tuple[_Ship, ...] = (
    _Ship(1, "Atlantic Trader", "Cargo", "Azores/Atlantic", Scenario.normal,
          38.0, -28.5, 12.0, 90.0),
    _Ship(2, "North Sea Star", "Tanker", "Europe", Scenario.normal,
          54.0, 4.0, 10.0, 200.0),
    _Ship(3, "Gulf Anchorage", "Bulk Carrier", "North America", Scenario.stationary,
          29.0, -94.0, 0.0, 0.0),
    _Ship(4, "Rio Grande", "Container", "South America", Scenario.signal_loss,
          -23.0, -43.0, 18.0, 180.0),
    _Ship(5, "Cape Runner", "Cargo", "Africa", Scenario.position_jump,
          -33.0, 18.0, 14.0, 270.0),
    _Ship(6, "Pacific Pearl", "Passenger", "Asia", Scenario.speed_anomaly,
          35.0, 140.0, 20.0, 45.0),
    _Ship(7, "Coral Voyager", "Fishing", "Pacific", Scenario.course_anomaly,
          20.0, -155.0, 5.0, 0.0),
    _Ship(8, "Azores Ferry", "Passenger", "Azores/Atlantic", Scenario.normal,
          37.5, -25.5, 16.0, 120.0),
)


class MockShipProvider(AISProvider):
    """In-memory, deterministic ship provider."""

    source = Source.mock

    def __init__(self, clock: Clock, seed: int = 42) -> None:
        self._clock = clock
        self._seed = seed
        self._epoch: datetime = clock.now()
        self._ships: tuple[_Ship, ...] = self._build_ships(seed)

    # -- provider interface -------------------------------------------------

    def get_entities(self) -> list[Entity]:
        return [
            Entity(
                id=ship.entity_id,
                type=EntityType.ship,
                identifier=ship.identifier,
                source=Source.mock,
                name=ship.name,
                callsign=ship.callsign,
                mmsi=ship.mmsi,
                imo=ship.imo,
            )
            for ship in self._ships
        ]

    def get_positions(self) -> list[Position]:
        return self.position_at(self._clock.now())

    def position_at(self, when: datetime) -> list[Position]:
        """Normalized positions for all ships at an arbitrary instant.

        Movement is a pure function of time, so this doubles as a deterministic
        history/trail source.
        """
        return [self._position_for(ship, when) for ship in self._ships]

    # -- internals ----------------------------------------------------------

    @staticmethod
    def _build_ships(seed: int) -> tuple[_Ship, ...]:
        """Apply deterministic, seed-derived micro-variation to nominal states."""
        rng = random.Random(seed)
        result: list[_Ship] = []
        for ship in _NOMINAL_SHIPS:
            speed_kt = ship.speed_kt
            if speed_kt > 0:  # keep stationary ships at zero speed
                speed_kt = max(0.0, speed_kt + rng.uniform(-1.0, 1.0))
            result.append(
                _Ship(
                    number=ship.number,
                    name=ship.name,
                    ship_type=ship.ship_type,
                    region=ship.region,
                    scenario=ship.scenario,
                    latitude=ship.latitude + rng.uniform(-0.3, 0.3),
                    longitude=ship.longitude + rng.uniform(-0.3, 0.3),
                    speed_kt=speed_kt,
                    course_deg=ship.course_deg + rng.uniform(-5.0, 5.0),
                )
            )
        return tuple(result)

    def _elapsed(self, now: datetime) -> float:
        return (now - self._epoch).total_seconds()


    def _position_for(self, ship: _Ship, now: datetime) -> Position:
        elapsed = self._elapsed(now)
        timestamp = now
        signal_age = 0.0
        quality = DataQuality.good
        speed = ship.speed_ms
        course = ship.course_deg % 360.0
        heading = ship.course_deg % 360.0  # ships mostly follow their course
        lat, lon = ship.latitude, ship.longitude

        if ship.scenario is Scenario.stationary:
            speed = 0.0
            lat, lon = ship.latitude, ship.longitude

        elif ship.scenario is Scenario.signal_loss:
            # Movement (and timestamps) stop at the freeze point; signal_age grows.
            moving = min(elapsed, FREEZE_AT_S)
            lat, lon = offset_position(
                ship.latitude, ship.longitude, ship.course_deg, ship.speed_ms * moving
            )
            timestamp = self._epoch + timedelta(seconds=FREEZE_AT_S)
            signal_age = max(0.0, elapsed - FREEZE_AT_S)
            quality = DataQuality.degraded if signal_age > 0 else DataQuality.good

        elif ship.scenario is Scenario.position_jump:
            if elapsed < EVENT_AT_S:
                lat, lon = offset_position(
                    ship.latitude, ship.longitude, ship.course_deg, ship.speed_ms * elapsed
                )
            else:
                # Teleport, then continue normally from the jumped location.
                lat, lon = offset_position(
                    ship.latitude + JUMP_LAT_OFFSET,
                    ship.longitude + JUMP_LON_OFFSET,
                    ship.course_deg,
                    ship.speed_ms * (elapsed - EVENT_AT_S),
                )

        elif ship.scenario is Scenario.speed_anomaly:
            if elapsed < EVENT_AT_S:
                lat, lon = offset_position(
                    ship.latitude, ship.longitude, ship.course_deg, ship.speed_ms * elapsed
                )
            else:
                base_lat, base_lon = offset_position(
                    ship.latitude, ship.longitude, ship.course_deg, ship.speed_ms * EVENT_AT_S
                )
                lat, lon = offset_position(
                    base_lat, base_lon, ship.course_deg,
                    ANOMALOUS_SPEED_MS * (elapsed - EVENT_AT_S),
                )
                speed = ANOMALOUS_SPEED_MS

        elif ship.scenario is Scenario.course_anomaly:
            if elapsed < EVENT_AT_S:
                lat, lon = offset_position(
                    ship.latitude, ship.longitude, ship.course_deg, ship.speed_ms * elapsed
                )
            else:
                base_lat, base_lon = offset_position(
                    ship.latitude, ship.longitude, ship.course_deg, ship.speed_ms * EVENT_AT_S
                )
                course = (ship.course_deg + COURSE_TURN_DEG) % 360.0
                heading = course
                lat, lon = offset_position(
                    base_lat, base_lon, course, ship.speed_ms * (elapsed - EVENT_AT_S)
                )

        else:  # NORMAL
            lat, lon = offset_position(
                ship.latitude, ship.longitude, ship.course_deg, ship.speed_ms * elapsed
            )

        return Position(
            entity_id=ship.entity_id,
            timestamp=timestamp,
            latitude=lat,
            longitude=lon,
            source=Source.mock,
            altitude=None,
            speed=speed,
            heading=heading,
            course=course,
            vertical_rate=None,
            signal_age=signal_age,
            quality=quality,
        )

