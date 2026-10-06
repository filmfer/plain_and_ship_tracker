"""Ship HTTP endpoints.

Mirror the aircraft endpoints and read only from the current-state service, so
no provider internals reach the frontend.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.api.aircraft import EntityPosition, _clock, _settings
from app.models import Entity, EntityType
from app.state import CurrentStateService

router = APIRouter(prefix="/api", tags=["ships"])


def _state(request: Request) -> CurrentStateService:
    return request.app.state.current_state


def _view(state: CurrentStateService, entity: Entity, request: Request) -> EntityPosition:
    from app.api.aircraft import _view as _base_view

    return _base_view(state, entity, _settings(request), _clock(request))


@router.get("/ships", response_model=list[EntityPosition])
def list_ships(request: Request) -> list[EntityPosition]:
    """Return every known ship with its latest position."""
    state = _state(request)
    return [_view(state, entity, request) for entity in state.entities(EntityType.ship)]


@router.get("/ships/{entity_id}", response_model=EntityPosition)
def get_ship(entity_id: str, request: Request) -> EntityPosition:
    """Return a single ship by full id (``ship:DEMO-SHIP-001``) or identifier."""
    state = _state(request)
    entity = state.get_entity(entity_id) or state.get_entity(f"ship:{entity_id}")
    if entity is None or entity.type is not EntityType.ship:
        raise HTTPException(status_code=404, detail="ship not found")
    return _view(state, entity, request)
