"""Tests for the injectable clock system."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.clock import (
    DEFAULT_SIMULATION_START,
    RealClock,
    SimulationClock,
    build_clock,
)
from app.config import AppMode, Settings


class FakeTimeSource:
    """Controllable monotonic source for deterministic tests."""

    def __init__(self, start: float = 0.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def test_real_clock_is_timezone_aware_utc() -> None:
    moment = RealClock().now()

    assert moment.tzinfo is not None
    assert moment.utcoffset() == timedelta(0)


def test_simulation_clock_starts_at_anchor() -> None:
    clock = SimulationClock(time_source=FakeTimeSource())

    assert clock.now() == DEFAULT_SIMULATION_START


def test_simulation_clock_scales_with_multiplier() -> None:
    source = FakeTimeSource()
    clock = SimulationClock(multiplier=60.0, time_source=source)

    source.advance(1.0)  # 1 real second

    assert clock.now() == DEFAULT_SIMULATION_START + timedelta(seconds=60)


def test_simulation_clock_multiplier_one_is_identity() -> None:
    source = FakeTimeSource()
    clock = SimulationClock(multiplier=1.0, time_source=source)

    source.advance(5.0)

    assert clock.now() == DEFAULT_SIMULATION_START + timedelta(seconds=5)


def test_fifteen_simulated_minutes_at_multiplier_60() -> None:
    """15 simulated minutes == 15 real seconds at MOCK_TIME_MULTIPLIER=60."""
    source = FakeTimeSource()
    clock = SimulationClock(multiplier=60.0, time_source=source)

    source.advance(15.0)  # 15 real seconds

    assert clock.now() - DEFAULT_SIMULATION_START == timedelta(minutes=15)


def test_manual_advance_is_deterministic_and_additive() -> None:
    clock = SimulationClock(time_source=FakeTimeSource())

    clock.advance(30)
    clock.advance(30)

    assert clock.now() == DEFAULT_SIMULATION_START + timedelta(seconds=60)


def test_rejects_negative_multiplier() -> None:
    with pytest.raises(ValueError):
        SimulationClock(multiplier=-1.0, time_source=FakeTimeSource())


def test_rejects_negative_advance() -> None:
    clock = SimulationClock(time_source=FakeTimeSource())

    with pytest.raises(ValueError):
        clock.advance(-1)


def test_rejects_naive_start() -> None:
    with pytest.raises(ValueError):
        SimulationClock(start=datetime(2024, 1, 1), time_source=FakeTimeSource())


def test_build_clock_in_demo_is_simulation() -> None:
    clock = build_clock(Settings(_env_file=None, app_mode=AppMode.demo))

    assert isinstance(clock, SimulationClock)


def test_build_clock_in_production_is_real() -> None:
    clock = build_clock(Settings(_env_file=None, app_mode=AppMode.production))

    assert isinstance(clock, RealClock)


def test_build_clock_uses_configured_multiplier() -> None:
    settings = Settings(_env_file=None, app_mode=AppMode.demo, mock_time_multiplier=60.0)

    clock = build_clock(settings)

    assert isinstance(clock, SimulationClock)
    assert clock.multiplier == 60.0


def test_build_clock_accepts_tz_aware_feature_detection() -> None:
    # Sanity: the anchor is tz-aware, matching clock construction guarantees.
    assert DEFAULT_SIMULATION_START.tzinfo == timezone.utc
