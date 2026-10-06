"""Tests for the current-state service."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models import DataQuality, Entity, EntityType, Position, Source
from app.state import CurrentStateService, StateSnapshot

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _aircraft(entity_id: str = "aircraft:DEMO001") -> Entity:
    return Entity(
        id=entity_id,
        type=EntityType.aircraft,
        identifier="DEMO001",
        source=Source.mock,
        icao24="de0001",
    )


def _ship(entity_id: str = "ship:DEMO-SHIP-001") -> Entity:
    return Entity(
        id=entity_id,
        type=EntityType.ship,
        identifier="DEMO-SHIP-001",
        source=Source.mock,
        mmsi="999000001",
    )


def _position(
    entity_id: str,
    *,
    latitude: float = 38.5,
    longitude: float = -28.0,
    timestamp: datetime = T0,
) -> Position:
    return Position(
        entity_id=entity_id,
        timestamp=timestamp,
        latitude=latitude,
        longitude=longitude,
        source=Source.mock,
        quality=DataQuality.good,
    )


def test_update_stores_entities_and_positions() -> None:
    state = CurrentStateService()
    state.update(
        entities=[_aircraft(), _ship()],
        positions=[_position("aircraft:DEMO001"), _position("ship:DEMO-SHIP-001")],
    )

    assert len(state) == 2
    assert state.get_entity("aircraft:DEMO001") is not None
    assert state.get_position("ship:DEMO-SHIP-001") is not None


def test_unknown_ids_return_none() -> None:
    state = CurrentStateService()

    assert state.get_entity("nope") is None
    assert state.get_position("nope") is None


def test_later_position_overwrites_earlier() -> None:
    state = CurrentStateService()
    entity = _aircraft()
    state.update([entity], [_position(entity.id, latitude=38.5)])
    state.update(
        [entity],
        [_position(entity.id, latitude=40.0, timestamp=T0 + timedelta(minutes=1))],
    )

    assert state.get_position(entity.id).latitude == 40.0
    assert len(state) == 1  # not duplicated


def test_filter_by_entity_type() -> None:
    state = CurrentStateService()
    state.update(
        entities=[_aircraft(), _ship()],
        positions=[_position("aircraft:DEMO001"), _position("ship:DEMO-SHIP-001")],
    )

    aircraft_only = state.positions(EntityType.aircraft)
    ships_only = state.positions(EntityType.ship)

    assert [p.entity_id for p in aircraft_only] == ["aircraft:DEMO001"]
    assert [p.entity_id for p in ships_only] == ["ship:DEMO-SHIP-001"]
    assert len(state.entities(EntityType.ship)) == 1


def test_updated_at_derived_from_newest_position() -> None:
    state = CurrentStateService()
    state.update(
        entities=[_aircraft(), _ship()],
        positions=[
            _position("aircraft:DEMO001", timestamp=T0),
            _position("ship:DEMO-SHIP-001", timestamp=T0 + timedelta(minutes=5)),
        ],
    )

    assert state.updated_at == T0 + timedelta(minutes=5)


def test_updated_at_can_be_set_explicitly() -> None:
    state = CurrentStateService()
    explicit = T0 + timedelta(hours=1)
    state.update([_aircraft()], [_position("aircraft:DEMO001")], updated_at=explicit)

    assert state.updated_at == explicit


def test_clear_resets_state() -> None:
    state = CurrentStateService()
    state.update([_aircraft()], [_position("aircraft:DEMO001")])
    state.clear()

    assert len(state) == 0
    assert state.updated_at is None
    assert state.entities() == []
    assert state.positions() == []


def test_snapshot_exposes_immutable_view() -> None:
    state = CurrentStateService()
    state.update([_aircraft()], [_position("aircraft:DEMO001")])
    snapshot = state.snapshot()

    assert isinstance(snapshot, StateSnapshot)
    assert snapshot.updated_at == T0
    assert "aircraft:DEMO001" in snapshot.positions_by_id
    assert isinstance(snapshot.entities, tuple)


def test_empty_state_snapshot() -> None:
    state = CurrentStateService()

    assert state.updated_at is None
    assert state.snapshot().entities == ()
