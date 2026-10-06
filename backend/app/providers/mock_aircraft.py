"""Deterministic mock aircraft provider.

Produces 8 clearly-synthetic aircraft (``DEMO001``..``DEMO008``) distributed
around the world. Movement is a pure function of simulated time obtained from
the injected :class:`~app.clock.Clock`, so behaviour is reproducible and can be
fast-forwarded in demo/test mode.

Units: altitude and vertical rate in metres / metres-per-second, speed in m/s,
heading in degrees. All entities use ``source="mock"``.

Scenarios are exercised deterministically:

* NORMAL          - steady straight-line motion.
* STATIONARY      - position frozen (speed 0).
* SIGNAL_LOSS     - updates stop after ``FREEZE_AT_S``; ``signal_age`` grows.
* POSITION_JUMP   - a sudden geographic discontinuity at ``EVENT_AT_S``.
* SPEED_ANOMALY   - an unrealistically high speed at ``EVENT_AT_S``.
* HEADING_ANOMALY - an abrupt heading change at ``EVENT_AT_S``.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.clock import Clock
from app.geo import offset_position
from app.models import DataQuality, Entity, EntityType, Position, Source
from app.providers.base import AircraftProvider, Scenario

# Seconds of simulated time after which scenario events occur.
FREEZE_AT_S = 120.0
EVENT_AT_S = 120.0

# Position-jump magnitude (degrees) and anomalous ground speed (m/s, ~17000 kt).
JUMP_LAT_OFFSET = 8.0
JUMP_LON_OFFSET = 8.0
ANOMALOUS_SPEED_MS = 9000.0

# Heading-anomaly: sharp turn applied at the event time.
HEADING_TURN_DEG = 150.0


@dataclass(frozen=True)
class _Aircraft:
    """Static description of one synthetic aircraft."""

    number: int
    name: str
    region: str
    scenario: Scenario
    latitude: float
    longitude: float
    altitude_m: float
    speed_ms: float
    heading_deg: float
    vertical_rate_ms: float

    @property
    def identifier(self) -> str:
        return f"DEMO{self.number:03d}"

    @property
    def entity_id(self) -> str:
        return f"aircraft:{self.identifier}"

    @property
    def icao24(self) -> str:
        return f"de{self.number:04d}"


# Deterministic nominal states (pre-seed). Spread across the globe, with two
# aircraft near the Azores for local development.
_NOMINAL_AIRCRAFT: tuple[_Aircraft, ...] = (
    _Aircraft(1, "Azores Atlantic Patrol", "Azores/Atlantic", Scenario.normal,
              38.5, -28.0, 10_668.0, 250.0, 90.0, 0.0),
    _Aircraft(2, "Europe Express", "Europe", Scenario.normal,
              50.0, 8.0, 10_000.0, 230.0, 45.0, 2.5),
    _Aircraft(3, "North America Hold", "North America", Scenario.stationary,
              40.0, -100.0, 9_000.0, 0.0, 0.0, 0.0),
    _Aircraft(4, "South America Lost", "South America", Scenario.signal_loss,
              -23.5, -46.6, 11_000.0, 240.0, 180.0, -1.5),
    _Aircraft(5, "Africa Jumper", "Africa", Scenario.position_jump,
              5.0, 20.0, 10_500.0, 235.0, 270.0, 0.0),
    _Aircraft(6, "Asia Fast", "Asia", Scenario.speed_anomaly,
              35.0, 139.0, 11_500.0, 245.0, 60.0, 1.0),
    _Aircraft(7, "Pacific Wanderer", "Pacific", Scenario.heading_anomaly,
              20.0, -160.0, 9_500.0, 220.0, 0.0, 0.0),
    _Aircraft(8, "Azores Approach", "Azores/Atlantic", Scenario.normal,
              37.0, -25.0, 10_200.0, 255.0, 120.0, -2.0),
)


class MockAircraftProvider(AircraftProvider):
    """In-memory, deterministic aircraft provider."""

    source = Source.mock

    def __init__(self, clock: Clock, seed: int = 42) -> None:
        self._clock = clock
        self._seed = seed
        self._epoch: datetime = clock.now()
        self._aircraft: tuple[_Aircraft, ...] = self._build_aircraft(seed)

    # -- provider interface -------------------------------------------------

    def get_entities(self) -> list[Entity]:
        return [
            Entity(
                id=ac.entity_id,
                type=EntityType.aircraft,
                identifier=ac.identifier,
                source=Source.mock,
                name=ac.name,
                callsign=ac.identifier,
                registration=ac.identifier,
                icao24=ac.icao24,
            )
            for ac in self._aircraft
        ]

    def get_positions(self) -> list[Position]:
        return self.position_at(self._clock.now())

    def position_at(self, when: datetime) -> list[Position]:
        """Normalized positions for all aircraft at an arbitrary instant.

        Movement is a pure function of time, so this doubles as a deterministic
        history/trail source.
        """
        return [self._position_for(ac, when) for ac in self._aircraft]

    # -- internals ----------------------------------------------------------

    @staticmethod
    def _build_aircraft(seed: int) -> tuple[_Aircraft, ...]:
        """Apply deterministic, seed-derived micro-variation to nominal states."""
        rng = random.Random(seed)
        result: list[_Aircraft] = []
        for ac in _NOMINAL_AIRCRAFT:
            speed = ac.speed_ms
            if speed > 0:  # keep stationary aircraft at zero speed
                speed = max(0.0, speed + rng.uniform(-5.0, 5.0))
            result.append(
                _Aircraft(
                    number=ac.number,
                    name=ac.name,
                    region=ac.region,
                    scenario=ac.scenario,
                    latitude=ac.latitude + rng.uniform(-0.5, 0.5),
                    longitude=ac.longitude + rng.uniform(-0.5, 0.5),
                    altitude_m=ac.altitude_m + rng.uniform(-500.0, 500.0),
                    speed_ms=speed,
                    heading_deg=ac.heading_deg + rng.uniform(-10.0, 10.0),
                    vertical_rate_ms=ac.vertical_rate_ms,
                )
            )
        return tuple(result)

    def _elapsed(self, now: datetime) -> float:
        return (now - self._epoch).total_seconds()


    def _position_for(self, ac: _Aircraft, now: datetime) -> Position:
        elapsed = self._elapsed(now)
        timestamp = now
        signal_age = 0.0
        quality = DataQuality.good
        speed = ac.speed_ms
        heading = ac.heading_deg % 360.0
        vertical_rate: float | None = ac.vertical_rate_ms
        lat, lon = ac.latitude, ac.longitude

        if ac.scenario is Scenario.stationary:
            speed = 0.0
            vertical_rate = 0.0
            lat, lon = ac.latitude, ac.longitude

        elif ac.scenario is Scenario.signal_loss:
            # Movement (and timestamps) stop at the freeze point; signal_age grows.
            moving = min(elapsed, FREEZE_AT_S)
            lat, lon = offset_position(
                ac.latitude, ac.longitude, ac.heading_deg, ac.speed_ms * moving
            )
            timestamp = self._epoch + timedelta(seconds=FREEZE_AT_S)
            signal_age = max(0.0, elapsed - FREEZE_AT_S)
            quality = DataQuality.degraded if signal_age > 0 else DataQuality.good

        elif ac.scenario is Scenario.position_jump:
            if elapsed < EVENT_AT_S:
                lat, lon = offset_position(
                    ac.latitude, ac.longitude, ac.heading_deg, ac.speed_ms * elapsed
                )
            else:
                # Teleport, then continue normally from the jumped location.
                lat, lon = offset_position(
                    ac.latitude + JUMP_LAT_OFFSET,
                    ac.longitude + JUMP_LON_OFFSET,
                    ac.heading_deg,
                    ac.speed_ms * (elapsed - EVENT_AT_S),
                )

        elif ac.scenario is Scenario.speed_anomaly:
            if elapsed < EVENT_AT_S:
                lat, lon = offset_position(
                    ac.latitude, ac.longitude, ac.heading_deg, ac.speed_ms * elapsed
                )
            else:
                base_lat, base_lon = offset_position(
                    ac.latitude, ac.longitude, ac.heading_deg, ac.speed_ms * EVENT_AT_S
                )
                lat, lon = offset_position(
                    base_lat, base_lon, ac.heading_deg,
                    ANOMALOUS_SPEED_MS * (elapsed - EVENT_AT_S),
                )
                speed = ANOMALOUS_SPEED_MS

        elif ac.scenario is Scenario.heading_anomaly:
            if elapsed < EVENT_AT_S:
                lat, lon = offset_position(
                    ac.latitude, ac.longitude, ac.heading_deg, ac.speed_ms * elapsed
                )
            else:
                base_lat, base_lon = offset_position(
                    ac.latitude, ac.longitude, ac.heading_deg, ac.speed_ms * EVENT_AT_S
                )
                heading = (ac.heading_deg + HEADING_TURN_DEG) % 360.0
                lat, lon = offset_position(
                    base_lat, base_lon, heading, ac.speed_ms * (elapsed - EVENT_AT_S)
                )

        else:  # NORMAL
            lat, lon = offset_position(
                ac.latitude, ac.longitude, ac.heading_deg, ac.speed_ms * elapsed
            )

        return Position(
            entity_id=ac.entity_id,
            timestamp=timestamp,
            latitude=lat,
            longitude=lon,
            source=Source.mock,
            altitude=ac.altitude_m,
            speed=speed,
            heading=heading,
            vertical_rate=vertical_rate,
            signal_age=signal_age,
            quality=quality,
        )

