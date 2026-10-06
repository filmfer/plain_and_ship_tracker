"""Tests for the normalized tracking models."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.models import DataQuality, Entity, EntityType, Position, Source


def _entity(**overrides: object) -> Entity:
    base = {
        "id": "aircraft:DEMO001",
        "type": EntityType.aircraft,
        "identifier": "DEMO001",
        "source": Source.mock,
        "callsign": "DEMO001",
        "icao24": "abc123",
    }
    base.update(overrides)
    return Entity(**base)


def _position(**overrides: object) -> Position:
    base = {
        "entity_id": "aircraft:DEMO001",
        "timestamp": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "latitude": 38.5,
        "longitude": -28.7,
        "source": Source.mock,
    }
    base.update(overrides)
    return Position(**base)


def test_aircraft_entity_round_trip() -> None:
    entity = _entity()

    assert entity.type is EntityType.aircraft
    assert entity.source is Source.mock
    assert entity.mmsi is None
    assert entity.imo is None


def test_ship_entity_supports_maritime_identifiers() -> None:
    entity = _entity(
        id="ship:DEMO-SHIP-001",
        type=EntityType.ship,
        identifier="DEMO-SHIP-001",
        mmsi="999000001",
        imo="IMO9990001",
        callsign="DMO001",
        icao24=None,
    )

    assert entity.type is EntityType.ship
    assert entity.mmsi == "999000001"
    assert entity.imo == "IMO9990001"


def test_entity_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        _entity(unexpected="x")


def test_position_defaults_for_optional_fields() -> None:
    position = _position()

    assert position.altitude is None
    assert position.speed is None
    assert position.heading is None
    assert position.vertical_rate is None
    assert position.signal_age is None
    assert position.quality is DataQuality.unknown


def test_position_accepts_full_aircraft_payload() -> None:
    position = _position(
        altitude=10500.0,
        speed=245.5,
        heading=90.0,
        vertical_rate=-3.2,
        signal_age=2.0,
        quality=DataQuality.good,
    )

    assert position.altitude == 10500.0
    assert position.vertical_rate == -3.2
    assert position.quality is DataQuality.good


@pytest.mark.parametrize("latitude", [-90.1, 90.1, 200.0])
def test_position_rejects_invalid_latitude(latitude: float) -> None:
    with pytest.raises(ValidationError):
        _position(latitude=latitude)


@pytest.mark.parametrize("longitude", [-180.1, 180.1, 400.0])
def test_position_rejects_invalid_longitude(longitude: float) -> None:
    with pytest.raises(ValidationError):
        _position(longitude=longitude)


def test_position_rejects_naive_timestamp() -> None:
    with pytest.raises(ValidationError):
        _position(timestamp=datetime(2024, 1, 1))


def test_position_rejects_negative_speed() -> None:
    with pytest.raises(ValidationError):
        _position(speed=-1.0)


def test_position_rejects_negative_signal_age() -> None:
    with pytest.raises(ValidationError):
        _position(signal_age=-0.1)


@pytest.mark.parametrize("heading", [-1.0, 360.1, 400.0])
def test_position_rejects_out_of_range_heading(heading: float) -> None:
    with pytest.raises(ValidationError):
        _position(heading=heading)


def test_position_normalises_heading_360_to_zero() -> None:
    position = _position(heading=360.0)

    assert position.heading == 0.0


def test_position_course_defaults_to_none_and_validates_range() -> None:
    assert _position().course is None
    assert _position(course=360.0).course == 0.0
    with pytest.raises(ValidationError):
        _position(course=361.0)


def test_source_enum_values() -> None:
    assert {s.value for s in Source} == {
        "mock",
        "opensky",
        "local_adsb",
        "ais",
        "local_ais",
    }
