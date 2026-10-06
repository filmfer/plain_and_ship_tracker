# Development

## Prerequisites
- Docker & Docker Compose
- Node.js (for frontend)
- Python 3.11+ (for backend)

## Setup
```bash
cp .env.example .env
docker compose up --build
```

## Testing
- Backend: `pytest backend/tests/`
- Linting (Ruff/ESLint): `ruff check backend` and `eslint frontend/src`

## Deployment
Production deployment uses standard Docker containers. Ensure `DATABASE_URL` and `REDIS_URL` are configured for persistent state and history.
