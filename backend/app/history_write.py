"""Historical write service - persist only meaningful position changes.

This module decides *what is worth storing* in the historical store
(:mod:`app.db`); the database itself stores whatever it is handed. The goal is
compact history: identical or near-identical positions are never written
repeatedly (project rule #20).

Decision rule (per position, compared against that entity's last **written**
position):

1. First position seen for an entity -> always written (it seeds the baseline).
2. Otherwise BOTH of the following must hold:

   a. **Time gate** - ``position.timestamp - last_written.timestamp`` is at
      least ``MIN_TIME_BETWEEN_HISTORY_WRITES`` (rate limit), AND

   b. **Material change** - at least one of:

      * distance        >= ``MIN_POSITION_CHANGE_METERS``
      * |speed delta|   >= ``MIN_SPEED_CHANGE``   (same units as Position.speed)
      * heading delta   >= ``MIN_HEADING_CHANGE`` (degrees, circular 0..180)

   Failing either condition skips the write. A stationary entity therefore
   never rewrites its identical position, no matter how much time passes.

Other properties:

* Writes are batched: one atomic ``store_history`` call per ``record()``.
* On database failure baselines are **not** advanced, so the batch is retried
  on the next ``record()``; the first failure and the recovery are logged.
* ``record()`` never raises - a history write must not break the tick loop.
* Positions without a matching entity are dropped (the database foreign key
  would reject them anyway).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Optional

from app.config import Settings
from app.db import Database
from app.geo import distance_meters
from app.models import Entity, Position

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _LastWrite:
    """Baseline of the last position actually handed to the database."""

    timestamp: datetime
    latitude: float
    longitude: float
    speed: Optional[float]
    heading: Optional[float]


def _heading_delta(first: float, second: float) -> float:
    """Smallest absolute heading difference in degrees, in the range [0, 180]."""
    delta = abs(first - second) % 360.0
    return min(delta, 360.0 - delta)


class HistoryWriteService:
    """Threshold-gated writer for historical positions."""

    def __init__(self, database: Database, settings: Settings) -> None:
        self._database = database
        self._min_distance = settings.min_position_change_meters
        self._min_interval = timedelta(seconds=settings.min_time_between_history_writes)
        self._min_speed = settings.min_speed_change
        self._min_heading = settings.min_heading_change
        self._last: dict[str, _LastWrite] = {}
        self._failure_logged = False
        # Observability: total positions handed to the database successfully.
        self.write_count = 0

    @property
    def database(self) -> Database:
        return self._database

    def record(
        self,
        entities: Iterable[Entity],
        positions: Iterable[Position],
    ) -> int:
        """Store this tick's meaningful positions; return how many were written.

        Synchronous (blocking on the database) - async callers should wrap it
        with ``asyncio.to_thread``. Never raises.
        """
        entities_by_id = {entity.id: entity for entity in entities}
        candidates: list[tuple[str, Entity, Position]] = []
        for position in positions:
            entity = entities_by_id.get(position.entity_id)
            if entity is None:
                continue  # would violate the entities foreign key
            last = self._last.get(position.entity_id)
            if last is not None and not self._is_meaningful(last, position):
                continue
            candidates.append((position.entity_id, entity, position))

        if not candidates:
            return 0

        try:
            self._database.store_history(
                [entity for _, entity, _ in candidates],
                [position for _, _, position in candidates],
            )
        except Exception as exc:  # noqa: BLE001 - never break the tick loop
            if not self._failure_logged:
                self._failure_logged = True
                logger.warning(
                    "historical write failed; samples kept for retry (%s)", exc
                )
            return 0

        if self._failure_logged:
            self._failure_logged = False
            logger.info("historical write recovered")

        for entity_id, _, position in candidates:
            self._last[entity_id] = _LastWrite(
                timestamp=position.timestamp,
                latitude=position.latitude,
                longitude=position.longitude,
                speed=position.speed,
                heading=position.heading,
            )
        self.write_count += len(candidates)
        return len(candidates)

    def _is_meaningful(self, last: _LastWrite, position: Position) -> bool:
        """Apply the time gate plus the material-change thresholds."""
        if position.timestamp - last.timestamp < self._min_interval:
            return False  # rate limit (also rejects out-of-order timestamps)
        if (
            distance_meters(
                last.latitude, last.longitude, position.latitude, position.longitude
            )
            >= self._min_distance
        ):
            return True
        if (
            last.speed is not None
            and position.speed is not None
            and abs(position.speed - last.speed) >= self._min_speed
        ):
            return True
        if (
            last.heading is not None
            and position.heading is not None
            and _heading_delta(last.heading, position.heading) >= self._min_heading
        ):
            return True
        return False
