"""Aircraft and search HTTP endpoints.

These read exclusively from the current-state service, so the frontend receives
normalized data and never sees provider internals.

Each view also carries tracking-data freshness (``data_state`` / ``data_age_s``)
computed server-side against the application clock -- required because demo
mode uses a SimulationClock whose timestamps diverge from wall-clock time.
These are tracking-data states, never safety assessments.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from app.clock import Clock
from app.config import Settings
from app.data_state import TrackingState, age_seconds, classify
from app.models import Entity, EntityType, Position
from app.search import entity_matches, normalize_query
from app.state import CurrentStateService

router = APIRouter(prefix="/api", tags=["aircraft"])


class EntityPosition(BaseModel):
    """An entity together with its latest position (if known)."""

    entity: Entity
    position: Optional[Position] = None
    data_state: TrackingState = TrackingState.unknown
    data_age_s: Optional[float] = None


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _clock(request: Request) -> Clock:
    return request.app.state.runtime.clock


def freshness(
    position: Optional[Position], settings: Settings, clock: Clock
) -> tuple[TrackingState, Optional[float]]:
    """Classify freshness of ``position`` against the application clock."""
    age = age_seconds(clock.now(), position.timestamp if position else None)
    return (
        classify(
            age,
            live_s=settings.data_state_live_s,
            recent_s=settings.data_state_recent_s,
            stale_s=settings.data_state_stale_s,
        ),
        age,
    )


def _view(
    state: CurrentStateService,
    entity: Entity,
    settings: Settings,
    clock: Clock,
) -> EntityPosition:
    position = state.get_position(entity.id)
    data_state, data_age_s = freshness(position, settings, clock)
    return EntityPosition(
        entity=entity,
        position=position,
        data_state=data_state,
        data_age_s=data_age_s,
    )


def _state(request: Request) -> CurrentStateService:
    return request.app.state.current_state


@router.get("/aircraft", response_model=list[EntityPosition])
def list_aircraft(request: Request) -> list[EntityPosition]:
    """Return every known aircraft with its latest position."""
    state = _state(request)
    settings, clock = _settings(request), _clock(request)
    return [
        _view(state, entity, settings, clock)
        for entity in state.entities(EntityType.aircraft)
    ]


@router.get("/aircraft/{entity_id}", response_model=EntityPosition)
def get_aircraft(entity_id: str, request: Request) -> EntityPosition:
    """Return a single aircraft by full id (``aircraft:DEMO001``) or identifier."""
    state = _state(request)
    entity = state.get_entity(entity_id) or state.get_entity(f"aircraft:{entity_id}")
    if entity is None or entity.type is not EntityType.aircraft:
        raise HTTPException(status_code=404, detail="aircraft not found")
    return _view(state, entity, _settings(request), _clock(request))


@router.get("/search", response_model=list[EntityPosition])
def search(
    request: Request,
    q: str = Query(..., min_length=1, description="flight number, callsign or id"),
) -> list[EntityPosition]:
    """Search entities by normalized identifier (flight number, MMSI, ...)."""
    state = _state(request)
    settings, clock = _settings(request), _clock(request)
    needle = normalize_query(q)
    return [
        _view(state, entity, settings, clock)
        for entity in state.entities()
        if entity_matches(entity, needle)
    ]
