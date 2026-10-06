# Plain Boat Radar

A production-quality, open-source web application for tracking aircraft and ships.

## Quick Start (Demo Mode)

The application defaults to `demo` mode, providing a fully functional, offline simulation. No API keys or external services are required.

```bash
# 1. Initialize environment
cp .env.example .env

# 2. Launch the stack
docker compose up --build
```

Access the application at `http://localhost:5173`.

## Modes

- **Demo** (Default): Offline, deterministic mock data.
- **Local**: Supports local ADS-B/AIS receivers (requires hardware).
- **Production**: Integrates external providers (OpenSky, AISHub) via API keys in `.env`.
