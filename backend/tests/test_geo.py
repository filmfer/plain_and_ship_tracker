"""Tests for geodesy helpers."""

from __future__ import annotations

import math

from app.geo import EARTH_RADIUS_M, offset_position


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def test_zero_distance_returns_same_point() -> None:
    lat, lon = offset_position(38.5, -28.0, 45.0, 0.0)

    assert math.isclose(lat, 38.5, abs_tol=1e-9)
    assert math.isclose(lon, -28.0, abs_tol=1e-9)


def test_moving_north_increases_latitude() -> None:
    lat, _ = offset_position(0.0, 0.0, 0.0, 10_000.0)

    assert lat > 0.0


def test_moving_east_increases_longitude() -> None:
    _, lon = offset_position(0.0, 0.0, 90.0, 10_000.0)

    assert lon > 0.0


def test_one_degree_of_latitude_is_about_111_km() -> None:
    lat, _ = offset_position(0.0, 0.0, 0.0, 111_195.0)

    assert math.isclose(lat, 1.0, abs_tol=0.01)


def test_longitude_is_normalised_into_range() -> None:
    _, lon = offset_position(0.0, 179.0, 90.0, 500_000.0)

    assert -180.0 <= lon <= 180.0


def test_distance_is_preserved() -> None:
    lat, lon = offset_position(38.5, -28.0, 123.0, 250_000.0)

    assert math.isclose(_distance_m(38.5, -28.0, lat, lon), 250_000.0, rel_tol=1e-3)
