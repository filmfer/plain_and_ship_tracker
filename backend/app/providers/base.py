"""Provider interfaces and shared provider concepts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum

from app.models import Entity, Position


class Scenario(str, Enum):
    """Deterministic behaviour a mock entity can exhibit.

    These describe *data patterns*, not real-world incidents; the anomaly
    engine (later phase) is responsible for flagging them.
    """

    normal = "NORMAL"
    stationary = "STATIONARY"
    signal_loss = "SIGNAL_LOSS"
    position_jump = "POSITION_JUMP"
    speed_anomaly = "SPEED_ANOMALY"
    heading_anomaly = "HEADING_ANOMALY"
    course_anomaly = "COURSE_ANOMALY"


class AircraftProvider(ABC):
    """Interface every aircraft data source must implement."""

    @abstractmethod
    def get_entities(self) -> list[Entity]:
        """Return the aircraft currently known to this provider."""

    @abstractmethod
    def get_positions(self) -> list[Position]:
        """Return the latest normalized positions for those aircraft."""


class ShipProvider(ABC):
    """Interface every ship/AIS data source must implement."""

    @abstractmethod
    def get_entities(self) -> list[Entity]:
        """Return the ships currently known to this provider."""

    @abstractmethod
    def get_positions(self) -> list[Position]:
        """Return the latest normalized positions for those ships."""


class AISProvider(ShipProvider):
    """Interface for ship sources delivering AIS-derived data.

    Refines :class:`ShipProvider` **without adding abstract methods**: every
    consumer (simulation loop, current state, API, WebSocket) works against
    the ``ShipProvider`` contract, so AIS sources are drop-in interchangeable
    and :class:`~app.providers.mock_ship.MockShipProvider` already conforms.

    Concrete providers (rule #6 provider tree):

    * ``MockShipProvider`` - deterministic demo (``source = mock``);
    * *future* external AIS provider - e.g. an AISHub-style collector
      (``source = ais``);
    * ``LocalAISProvider`` - a local AIS receiver (``source = local_ais``),
      opt-in via ``APP_MODE=local`` + ``LOCAL_AIS_ENABLED=true``; empty
      snapshots until a receiver is attached, no hardware required.

    AIS identity fields (MMSI, IMO, callsign, ship name - rule #19) live on
    the normalized :class:`~app.models.Entity`; providers fill what their
    feed provides and leave the rest ``None``. No real external AIS source is
    integrated yet - implementations plug in exactly like the interface's
    test fakes.
    """

