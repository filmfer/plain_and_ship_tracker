# Data Sources

## Provider Abstraction
The system uses the `AircraftProvider` and `ShipProvider` interfaces.

- **Mock**: Deterministic, offline-only. Default.
- **OpenSky**: Aircraft tracking (Production mode only).
- **AISHub**: Ship/AIS tracking (Production mode only).
- **Local ADS-B/AIS**: Future phase, supports local receiver hardware.

## Source Transparency
Every normalized entity retains a `source` tag (e.g., `mock`, `opensky`, `ais`, `local_ais`) to ensure data provenance is clear.
