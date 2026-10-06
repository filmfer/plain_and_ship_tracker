"""Small geodesy helpers (no external dependencies).

Positions use WGS84 coordinates in decimal degrees. Distances use the mean
Earth radius; over the scales involved in this project the spherical
approximation is more than adequate and keeps the code dependency-free.
"""

from __future__ import annotations

from math import asin, atan2, cos, degrees, radians, sin, sqrt

# IUGG mean Earth radius in metres.
EARTH_RADIUS_M = 6_371_008.8


def distance_meters(
    latitude1: float,
    longitude1: float,
    latitude2: float,
    longitude2: float,
) -> float:
    """Great-circle distance between two WGS84 points in metres (haversine)."""
    phi1, phi2 = radians(latitude1), radians(latitude2)
    dphi = radians(latitude2 - latitude1)
    dlambda = radians(longitude2 - longitude1)
    a = sin(dphi / 2.0) ** 2 + cos(phi1) * cos(phi2) * sin(dlambda / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_M * asin(sqrt(a))


def offset_position(
    latitude: float,
    longitude: float,
    bearing_deg: float,
    distance_m: float,
) -> tuple[float, float]:
    """Return the point reached from ``(latitude, longitude)`` after travelling
    ``distance_m`` metres along ``bearing_deg`` (great-circle destination)."""
    lat1 = radians(latitude)
    lon1 = radians(longitude)
    bearing = radians(bearing_deg)
    angular = distance_m / EARTH_RADIUS_M

    lat2 = asin(sin(lat1) * cos(angular) + cos(lat1) * sin(angular) * cos(bearing))
    lon2 = lon1 + atan2(
        sin(bearing) * sin(angular) * cos(lat1),
        cos(angular) - sin(lat1) * sin(lat2),
    )

    # Normalise longitude into [-180, 180].
    normalised_lon = (degrees(lon2) + 540.0) % 360.0 - 180.0
    return degrees(lat2), normalised_lon
