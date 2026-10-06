"""Tests for GET /api/history/{entity_id} (windowing, caps, sources).

Runs without lifespan (no DB/Redis/loop needed): the app factory installs
in-memory placeholders, so the whole suite stays offline.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import DataQuality, Position, Source

TS0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
EID = "aircraft:HIST-TEST"


def make_settings(**overrides) -> Settings:
    kwargs = {
        "_env_file": None,
        "database_url": "",
        "mock_tick_interval": 3600.0,  # keep the simulation loop quiet
    }
    kwargs.update(overrides)
    return Settings(**kwargs)


def _pos(minute: float, entity_id: str = EID) -> Position:
    """Position whose latitude encodes its minute (easy order assertions)."""
    return Position(
        entity_id=entity_id,
        timestamp=TS0 + timedelta(minutes=minute),
        latitude=38.0 + minute,
        longitude=-28.0,
        source=Source.mock,
        quality=DataQuality.good,
        altitude=1000.0,
        speed=100.0,
        heading=90.0,
    )


def _client(**overrides) -> TestClient:
    return TestClient(create_app(make_settings(**overrides)))


def _seed(app, count: int = 9, entity_id: str = EID) -> None:
    app.state.runtime.history.record(
        [_pos(float(m), entity_id) for m in range(count)]
    )


def _lats(body) -> list[float]:
    return [p["latitude"] for p in body["points"]]


class FakeDatabase:
    """PostgreSQL stand-in recording load_track calls."""

    name = "postgresql"
    store_count = 0

    def __init__(self, rows=()):
        self.rows = list(rows)
        self.calls = []

    def store_history(self, entities, positions, *, seen_at=None) -> int:
        return 0

    def load_track(self, entity_id, *, since=None, until=None, limit=500):
        self.calls.append(
            {"entity_id": entity_id, "since": since, "until": until, "limit": limit}
        )
        return self.rows

    def close(self) -> None:
        return None


def test_unknown_entity_is_404():
    client = _client()
    assert client.get("/api/history/aircraft:NOPE").status_code == 404
    assert client.get("/api/history/ship:GHOST").status_code == 404


def test_known_entity_with_empty_history_is_200():
    client = _client()
    client.app.state.runtime.history.clear()
    response = client.get("/api/history/aircraft:DEMO001")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 0
    assert body["points"] == []
    assert body["source"] == "memory"


def test_bare_identifier_resolves_to_canonical_id():
    client = _client()
    response = client.get("/api/history/DEMO001")
    assert response.status_code == 200
    body = response.json()
    assert body["entity_id"] == "aircraft:DEMO001"
    assert body["count"] > 0  # deterministic backfill provides the demo trail


def test_memory_track_returned_oldest_first():
    client = _client()
    _seed(client.app, count=5)
    body = client.get(f"/api/history/{EID}").json()
    assert body["source"] == "memory"
    assert body["matched"] == 5 and body["count"] == 5
    assert not body["downsampled"]
    assert _lats(body) == [38.0, 39.0, 40.0, 41.0, 42.0]
    assert all(p["entity_id"] == EID for p in body["points"])


def test_since_until_window_is_inclusive():
    client = _client()
    _seed(client.app, count=9)
    since = (TS0 + timedelta(minutes=3)).isoformat()
    until = (TS0 + timedelta(minutes=5)).isoformat()
    body = client.get(
        f"/api/history/{EID}", params={"since": since, "until": until}
    ).json()
    assert _lats(body) == [41.0, 42.0, 43.0]
    assert body["matched"] == 3


def test_naive_since_is_treated_as_utc():
    client = _client()
    _seed(client.app, count=9)
    response = client.get(
        f"/api/history/{EID}", params={"since": "2026-01-01T00:03:00"}
    )
    assert response.status_code == 200
    assert response.json()["matched"] == 6  # minutes 3..8


def test_since_after_until_is_400():
    client = _client()
    response = client.get(
        f"/api/history/{EID}",
        params={
            "since": TS0.isoformat(),
            "until": (TS0 - timedelta(minutes=1)).isoformat(),
        },
    )
    assert response.status_code == 400


def test_limit_downsamples_evenly_keeping_endpoints():
    client = _client()
    _seed(client.app, count=6)  # minutes 0..5 -> lat 38..43
    body = client.get(f"/api/history/{EID}", params={"limit": 3}).json()
    assert body["count"] == 3
    assert body["matched"] == 6
    assert body["downsampled"] is True
    assert body["points"][0]["latitude"] == 38.0  # span preserved
    assert body["points"][-1]["latitude"] == 43.0


def test_limit_is_clamped_to_max_points():
    client = _client(history_api_max_points=4)
    _seed(client.app, count=6)
    body = client.get(f"/api/history/{EID}", params={"limit": 100}).json()
    assert body["matched"] == 6
    assert body["count"] == 4  # clamped to HISTORY_API_MAX_POINTS
    assert body["points"][-1]["latitude"] == 43.0  # newest kept


def test_non_positive_limit_is_422():
    client = _client()
    assert client.get(f"/api/history/{EID}", params={"limit": 0}).status_code == 422


def test_configured_database_is_preferred_and_window_forwarded():
    client = _client(history_api_max_points=50, history_api_default_limit=10)
    fake = FakeDatabase(rows=[_pos(0.0)])
    client.app.state.database = fake

    since = TS0.isoformat()
    body = client.get(
        f"/api/history/{EID}", params={"since": since, "limit": 7}
    ).json()

    assert body["source"] == "postgresql"
    assert fake.calls == [
        {
            "entity_id": EID,
            "since": TS0,
            "until": None,
            "limit": 50,  # fetch window = hard cap, not the page limit
        }
    ]
    assert body["count"] == 1


def test_empty_database_falls_back_to_memory():
    client = _client()
    client.app.state.database = FakeDatabase(rows=[])
    _seed(client.app, count=4)
    body = client.get(f"/api/history/{EID}").json()
    assert body["source"] == "memory"
    assert body["count"] == 4
