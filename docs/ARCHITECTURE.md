# Architecture

Plain Boat Radar uses a modular, provider-independent pipeline.

## Data Pipeline
1.  **Collectors**: Providers (Mock, OpenSky, AISHub, Local) fetch raw data.
2.  **Normalization**: Providers map raw payloads to internal `Entity` and `Position` models.
3.  **Current State**: Cached in `CurrentStateService` (Redis-backed if configured).
4.  **History & Anomaly Engine**: Historical storage (PostgreSQL/PostGIS) and anomaly analysis (stale/loss/jump detection) run asynchronously.
5.  **API / WebSocket**: `LiveHub` fans out normalized position updates via WebSockets.
6.  **Frontend**: React + CesiumJS displays the 3D globe.

## Design Philosophy
- **Provider-Independent**: The core never depends on specific tracking APIs.
- **Offline-First**: Demo mode is always available without external dependencies.
- **Graceful Degradation**: Infrastructure (DB/Redis) is optional; the app falls back to memory if they are unavailable.
