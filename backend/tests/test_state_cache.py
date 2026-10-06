"""Targeted tests for the Redis current-state cache and its fallbacks.

All tests are offline: no Redis server is required. A tiny async fake client
stands in for ``redis.asyncio.Redis``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import DataQuality, Entity, EntityType, Position, Source
from app.state import CurrentStateService, StateSnapshot
from app.state_cache import (
    STATE_KEY,
    MemoryStateCache,
    RedisStateCache,
    ResilientStateCache,
    build_state_cache,
    decode_state,
    encode_state,
)

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def make_settings(**overrides: Any) -> Settings:
    """Settings with no ``.env`` (explicit overrides keep tests env-proof)."""
    return Settings(_env_file=None, **overrides)


def make_snapshot() -> StateSnapshot:
    """A small but realistic snapshot (one aircraft + one ship)."""
    state = CurrentStateService()
    state.update(
        entities=[
            Entity(
                id="aircraft:DEMO001",
                type=EntityType.aircraft,
                identifier="DEMO001",
                source=Source.mock,
                icao24="de0001",
            ),
            Entity(
                id="ship:DEMO-SHIP-001",
                type=EntityType.ship,
                identifier="DEMO-SHIP-001",
                source=Source.mock,
                mmsi="999000001",
            ),
        ],
        positions=[
            Position(
                entity_id="aircraft:DEMO001",
                timestamp=T0,
                latitude=38.5,
                longitude=-28.0,
                source=Source.mock,
                quality=DataQuality.good,
            ),
            Position(
                entity_id="ship:DEMO-SHIP-001",
                timestamp=T0,
                latitude=37.0,
                longitude=-27.0,
                source=Source.mock,
                quality=DataQuality.good,
            ),
        ],
        updated_at=T0,
    )
    return state.snapshot()


class FakeRedis:
    """Async stand-in for the redis client (offline, deterministic)."""

    def __init__(
        self,
        *,
        ping_ok: Any = True,
        fail_write: bool = False,
        fail_read: bool = False,
    ) -> None:
        self.ping_ok = ping_ok
        self.fail_write = fail_write
        self.fail_read = fail_read
        self.data: dict = {}
        self.last_ttl: Optional[int] = None
        self.closed = False

    async def ping(self) -> bool:
        if isinstance(self.ping_ok, Exception):
            raise self.ping_ok
        return bool(self.ping_ok)

    async def set(self, key: str, value: str, ex: Optional[int] = None) -> None:
        if self.fail_write:
            raise ConnectionError("redis write failed")
        self.last_ttl = ex
        self.data[key] = value

    async def get(self, key: str) -> Optional[str]:
        if self.fail_read:
            raise ConnectionError("redis read failed")
        return self.data.get(key)

    async def aclose(self) -> None:
        self.closed = True


# -- serialization -----------------------------------------------------------


def test_encode_decode_round_trip() -> None:
    snapshot = make_snapshot()

    assert decode_state(encode_state(snapshot)) == snapshot


def test_decode_accepts_z_suffix_timestamps() -> None:
    raw = encode_state(make_snapshot()).replace("+00:00", "Z")

    assert decode_state(raw) == make_snapshot()


# -- MemoryStateCache --------------------------------------------------------


def test_memory_cache_round_trip() -> None:
    cache = MemoryStateCache()
    snapshot = make_snapshot()

    asyncio.run(cache.save(snapshot))

    assert cache.name == "memory"
    assert cache.save_count == 1
    loaded = asyncio.run(cache.load())
    assert loaded == snapshot


def test_memory_cache_empty_returns_none() -> None:
    assert asyncio.run(MemoryStateCache().load()) is None


# -- RedisStateCache ---------------------------------------------------------


def test_redis_cache_round_trip_uses_key_and_ttl() -> None:
    client = FakeRedis()
    cache = RedisStateCache("redis://example:6379/0", client=client, ttl_s=123)
    snapshot = make_snapshot()

    asyncio.run(cache.save(snapshot))

    assert STATE_KEY in client.data
    assert client.last_ttl == 123
    assert cache.name == "redis"
    assert cache.save_count == 1
    assert asyncio.run(cache.load()) == snapshot


def test_redis_cache_missing_key_returns_none() -> None:
    cache = RedisStateCache("redis://example:6379/0", client=FakeRedis())

    assert asyncio.run(cache.load()) is None


# -- build_state_cache (graceful startup) ------------------------------------


def test_build_disabled_returns_memory_without_connecting() -> None:
    def factory(_url: str) -> Any:
        raise AssertionError("must not connect when caching is disabled")

    cache = asyncio.run(
        build_state_cache(make_settings(state_cache_enabled=False), client_factory=factory)
    )

    assert isinstance(cache, MemoryStateCache)


def test_build_empty_url_returns_memory() -> None:
    cache = asyncio.run(build_state_cache(make_settings(redis_url="  ")))

    assert isinstance(cache, MemoryStateCache)


def test_build_falls_back_to_memory_when_redis_unreachable(caplog: Any) -> None:
    client = FakeRedis(ping_ok=ConnectionError("connection refused"))

    with caplog.at_level(logging.WARNING, logger="app.state_cache"):
        cache = asyncio.run(
            build_state_cache(
                make_settings(redis_url="redis://127.0.0.1:1/0"),
                client_factory=lambda _url: client,
            )
        )

    assert isinstance(cache, MemoryStateCache)  # startup never fails
    assert any("in-memory state cache" in r.getMessage() for r in caplog.records)


def test_build_uses_redis_when_available() -> None:
    client = FakeRedis()

    cache = asyncio.run(build_state_cache(make_settings(), client_factory=lambda _url: client))

    assert isinstance(cache, ResilientStateCache)
    assert cache.name == "redis"
    assert client.closed is False


# -- ResilientStateCache -----------------------------------------------------


def test_resilient_cache_degrades_to_memory_and_recovers() -> None:
    client = FakeRedis(fail_write=True)
    cache = ResilientStateCache(RedisStateCache("redis://x", client=client))
    snapshot = make_snapshot()

    asyncio.run(cache.save(snapshot))  # must not raise

    assert cache.degraded is True
    assert asyncio.run(cache.load()) == snapshot  # served from the fallback

    client.fail_write = False
    asyncio.run(cache.save(snapshot))

    assert cache.degraded is False
    assert STATE_KEY in client.data  # writes resumed on the primary
    assert cache.save_count == 2


def test_resilient_cache_logs_warning_once_when_degrading(caplog: Any) -> None:
    client = FakeRedis(fail_write=True)
    cache = ResilientStateCache(RedisStateCache("redis://x", client=client))
    snapshot = make_snapshot()

    with caplog.at_level(logging.WARNING, logger="app.state_cache"):
        asyncio.run(cache.save(snapshot))
        asyncio.run(cache.save(snapshot))

    warnings = [r for r in caplog.records if "fallback" in r.getMessage()]
    assert len(warnings) == 1  # no log spam on every tick


# -- application integration -------------------------------------------------


def test_app_mirrors_current_state_into_cache() -> None:
    app = create_app(make_settings(state_cache_enabled=False))

    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        cache = app.state.state_cache
        assert isinstance(cache, MemoryStateCache)

        deadline = time.monotonic() + 5.0
        while cache.save_count == 0 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert cache.save_count > 0

        snapshot = asyncio.run(cache.load())
        assert snapshot is not None
        assert len(snapshot.entities) == 16  # 8 aircraft + 8 ships
        assert {p.entity_id for p in snapshot.positions} == {e.id for e in snapshot.entities}


def test_app_starts_gracefully_without_redis() -> None:
    """Demo must start offline with no Redis running (clear fallback, no crash)."""
    app = create_app(make_settings(redis_url="redis://127.0.0.1:1/0", redis_timeout_s=0.5))

    with TestClient(app) as client:
        assert client.get("/api/health").json()["status"] == "ok"
        assert isinstance(app.state.state_cache, MemoryStateCache)
