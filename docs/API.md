# API

## REST
- `GET /api/health`
- `GET /api/history/{entity_id}`
- `GET /api/alerts`
- `POST /api/alerts/{id}/acknowledge`

## WebSocket
- `ws://host/ws/live`
- Streams normalized `position_update` messages.
