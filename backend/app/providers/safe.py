"""Safe wrappers to isolate the simulation from unstable providers."""

from __future__ import annotations

import logging
from datetime import datetime

from app.models import Entity, Position
from app.providers.base import AircraftProvider, ShipProvider

logger = logging.getLogger(__name__)

class SafeAircraftProvider(AircraftProvider):
    """Wrapper that catches exceptions and logs provider failures."""

    def __init__(self, provider: AircraftProvider):
        self._provider = provider

    def get_entities(self) -> list[Entity]:
        try:
            return self._provider.get_entities()
        except Exception as e:
            logger.error("AircraftProvider %s failed get_entities: %s", self._provider.__class__.__name__, e)
            return []

    def get_positions(self) -> list[Position]:
        try:
            return self._provider.get_positions()
        except Exception as e:
            logger.error("AircraftProvider %s failed get_positions: %s", self._provider.__class__.__name__, e)
            return []

    def position_at(self, when: datetime) -> list[Position]:
        if hasattr(self._provider, "position_at"):
            return self._provider.position_at(when)
        return []


class SafeShipProvider(ShipProvider):
    """Wrapper that catches exceptions and logs provider failures."""

    def __init__(self, provider: ShipProvider):
        self._provider = provider

    def get_entities(self) -> list[Entity]:
        try:
            return self._provider.get_entities()
        except Exception as e:
            logger.error("ShipProvider %s failed get_entities: %s", self._provider.__class__.__name__, e)
            return []

    def get_positions(self) -> list[Position]:
        try:
            return self._provider.get_positions()
        except Exception as e:
            logger.error("ShipProvider %s failed get_positions: %s", self._provider.__class__.__name__, e)
            return []

    def position_at(self, when: datetime) -> list[Position]:
        if hasattr(self._provider, "position_at"):
            return self._provider.position_at(when)
        return []
