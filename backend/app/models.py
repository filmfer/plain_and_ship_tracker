"""Normalized tracking models shared by all providers.

Aircraft and ships are represented by the same :class:`Entity` / :class:`Position`
pair so that the rest of the application (storage, anomaly engine, API,
WebSocket, frontend) never depends on a specific data provider. Providers are
responsible for mapping their raw payloads into these normalized objects.

Field semantics (see project rules #16):

* ``source`` always records data provenance for transparency.
* Fields that do not apply to an entity type are ``None`` (null), never omitted.
* ``timestamp`` is always timezone-aware (UTC).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, field_validator


class EntityType(str, Enum):
    """Kind of tracked entity."""

    aircraft = "aircraft"
    ship = "ship"


class Source(str, Enum):
    """Data provenance. Demo entities must be clearly identifiable as ``mock``."""

    mock = "mock"
    opensky = "opensky"
    local_adsb = "local_adsb"
    ais = "ais"
    local_ais = "local_ais"


class DataQuality(str, Enum):
    """Coarse data-quality rating for a single position report."""

    good = "good"
    degraded = "degraded"
    poor = "poor"
    unknown = "unknown"


class Entity(BaseModel):
    """A tracked aircraft or ship (identity, independent of position)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    type: EntityType
    identifier: str
    source: Source

    name: Optional[str] = None
    callsign: Optional[str] = None
    registration: Optional[str] = None
    icao24: Optional[str] = None
    mmsi: Optional[str] = None
    imo: Optional[str] = None


class Position(BaseModel):
    """A single normalized position report for an entity."""

    model_config = ConfigDict(extra="forbid")

    entity_id: str
    timestamp: datetime
    latitude: float
    longitude: float
    source: Source

    altitude: Optional[float] = None
    speed: Optional[float] = None
    heading: Optional[float] = None
    course: Optional[float] = None
    vertical_rate: Optional[float] = None
    signal_age: Optional[float] = None
    quality: DataQuality = DataQuality.unknown

    @field_validator("timestamp")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware (UTC)")
        return value

    @field_validator("latitude")
    @classmethod
    def _validate_latitude(cls, value: float) -> float:
        if not -90.0 <= value <= 90.0:
            raise ValueError("latitude must be within [-90, 90]")
        return value

    @field_validator("longitude")
    @classmethod
    def _validate_longitude(cls, value: float) -> float:
        if not -180.0 <= value <= 180.0:
            raise ValueError("longitude must be within [-180, 180]")
        return value

    @field_validator("speed")
    @classmethod
    def _validate_speed(cls, value: Optional[float]) -> Optional[float]:
        if value is not None and value < 0:
            raise ValueError("speed must be non-negative")
        return value

    @field_validator("signal_age")
    @classmethod
    def _validate_signal_age(cls, value: Optional[float]) -> Optional[float]:
        if value is not None and value < 0:
            raise ValueError("signal_age must be non-negative")
        return value

    @field_validator("heading", "course")
    @classmethod
    def _validate_degrees(cls, value: Optional[float]) -> Optional[float]:
        if value is None:
            return None
        if not 0.0 <= value <= 360.0:
            raise ValueError("heading/course must be within [0, 360]")
        # Normalise the 360 boundary to 0 degrees.
        return value % 360.0

class AnomalyType(str, Enum):
    position_stale = "POSITION_STALE"
    signal_lost = "SIGNAL_LOST"
    position_jump = "POSITION_JUMP"
    speed_inconsistent = "SPEED_INCONSISTENT"
    heading_inconsistent = "HEADING_INCONSISTENT"
    altitude_inconsistent = "ALTITUDE_INCONSISTENT"


class AlertState(str, Enum):
    normal = "NORMAL"
    watch = "WATCH"
    warning = "WARNING"
    critical = "CRITICAL"


class Alert(BaseModel):
    """Normalized alert for anomaly findings."""
    id: str
    entity_id: str
    timestamp: datetime
    type: AnomalyType
    score: int
    severity: AlertState
    description: str
    detected_at: datetime
    resolved_at: Optional[datetime] = None
    acknowledged: bool = False

