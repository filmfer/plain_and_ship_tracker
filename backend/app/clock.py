"""Injectable clock system.

The whole application reads time through a :class:`Clock` instead of calling
``datetime.now()`` directly. This lets us:

* run against real wall-clock time (:class:`RealClock`), and
* fast-forward time deterministically in demo/tests (:class:`SimulationClock`),
  e.g. exercise a 15-minute "position unchanged" anomaly in 15 real seconds
  with ``MOCK_TIME_MULTIPLIER=60``.

Simulated time is defined as::

    now = start + (real_elapsed * multiplier) + manual_offset

Using :func:`time.monotonic` as the default source keeps the simulation immune
to system clock adjustments and independent of the timezone.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Protocol

from app.config import AppMode, Settings

# Fixed, timezone-aware anchor so every simulation starts from the same instant.
DEFAULT_SIMULATION_START = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def utcnow() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


class Clock(Protocol):
    """Minimal clock interface used throughout the application."""

    def now(self) -> datetime:
        """Return the current (timezone-aware, UTC) time."""
        ...


@dataclass
class RealClock:
    """Clock backed by real wall-clock time in UTC."""

    def now(self) -> datetime:
        return utcnow()


@dataclass
class SimulationClock:
    """Deterministic, injectable simulation clock.

    Parameters
    ----------
    start:
        Fixed simulated start instant (timezone-aware). Defaults to
        :data:`DEFAULT_SIMULATION_START`.
    multiplier:
        ``MOCK_TIME_MULTIPLIER``. ``1`` means 1 real second = 1 simulated
        second; ``60`` means 1 real second = 60 simulated seconds.
    time_source:
        Monotonic second source. Injectable for deterministic tests; defaults
        to :func:`time.monotonic`.
    """

    start: datetime = DEFAULT_SIMULATION_START
    multiplier: float = 1.0
    time_source: Callable[[], float] = time.monotonic

    _wall_start: float = field(init=False, repr=False)
    _offset: float = field(default=0.0, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.start.tzinfo is None:
            raise ValueError("start must be timezone-aware")
        if self.multiplier < 0:
            raise ValueError("multiplier must be non-negative")
        self._wall_start = self.time_source()

    def now(self) -> datetime:
        """Return the current simulated time."""
        real_elapsed = self.time_source() - self._wall_start
        simulated_seconds = real_elapsed * self.multiplier + self._offset
        return self.start + timedelta(seconds=simulated_seconds)

    def advance(self, seconds: float) -> None:
        """Manually advance simulated time (deterministic step for tests)."""
        if seconds < 0:
            raise ValueError("cannot advance by negative seconds")
        self._offset += seconds


def build_clock(settings: Settings) -> Clock:
    """Create the appropriate clock for the configured application mode.

    * ``production`` -> :class:`RealClock`
    * ``demo`` / ``local`` -> :class:`SimulationClock`
    """
    if settings.app_mode is AppMode.production:
        return RealClock()
    return SimulationClock(multiplier=settings.mock_time_multiplier)
