"""Current-state cache (Redis) with a graceful in-memory fallback.

The authoritative current state stays in ``CurrentStateService`` - API reads
remain synchronous, zero-latency and untouched by this module. This cache is a
*write-side mirror* of that state so that:

* other processes can read the latest normalized state from Redis, and
* a restart can recover the last known state.

Design constraints (project rules)
----------------------------------
* Existing interfaces are preserved - no endpoint, model or service changed.
* PostgreSQL is **not** involved; this is cache-only, never history.
* Redis is optional: demo/local must start offline with no Redis running.
  ``build_state_cache`` probes the connection once and falls back to an
  in-memory cache with a clear warning instead of failing startup.
* If Redis fails *later*, ``ResilientStateCache`` degrades to the in-memory
  cache, logs once, and recovers automatically when Redis returns.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Callable, Optional, Protocol

from app.config import Settings
from app.models import Entity, Position
from app.state import StateSnapshot

logger = logging.getLogger(__name__)

#: Redis key holding the JSON-encoded current state.
STATE_KEY = "radar:current_state:v1"


class StateCache(Protocol):
    """Minimal async interface for the current-state cache."""

    name: str
    save_count: int

    async def save(self, snapshot: StateSnapshot) -> None: ...

    async def load(self) -> Optional[StateSnapshot]: ...

    async def close(self) -> None: ...


# -- serialization ----------------------------------------------------------


def encode_state(snapshot: StateSnapshot) -> str:
    """Serialise a snapshot to a compact JSON document."""
    return json.dumps(
        {
            "updated_at": snapshot.updated_at.isoformat() if snapshot.updated_at else None,
            "entities": [entity.model_dump(mode="json") for entity in snapshot.entities],
            "positions": [position.model_dump(mode="json") for position in snapshot.positions],
        },
        separators=(",", ":"),
    )


def decode_state(raw: str) -> StateSnapshot:
    """Inverse of :func:`encode_state` (accepts the ``Z`` UTC suffix on py<3.11)."""
    doc = json.loads(raw)
    return StateSnapshot(
        updated_at=_parse_datetime(doc.get("updated_at")),
        entities=tuple(Entity.model_validate(item) for item in doc.get("entities") or ()),
        positions=tuple(Position.model_validate(item) for item in doc.get("positions") or ()),
    )


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    # datetime.fromisoformat() on Python < 3.11 cannot parse the "Z" suffix.
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


# -- implementations --------------------------------------------------------


class MemoryStateCache:
    """In-memory fallback: holds the last snapshot encoded exactly like Redis."""

    name = "memory"

    def __init__(self) -> None:
        self._raw: Optional[str] = None
        self.save_count = 0

    async def save(self, snapshot: StateSnapshot) -> None:
        self._raw = encode_state(snapshot)
        self.save_count += 1

    async def load(self) -> Optional[StateSnapshot]:
        return decode_state(self._raw) if self._raw else None

    async def close(self) -> None:
        return None


class RedisStateCache:
    """Current-state cache backed by Redis (single JSON document + TTL)."""

    name = "redis"

    def __init__(
        self,
        url: str,
        *,
        client: Any = None,
        key: str = STATE_KEY,
        ttl_s: int = 3600,
        timeout_s: float = 1.0,
    ) -> None:
        if client is None:
            # Imported lazily so the app runs even without the redis package.
            from redis.asyncio import Redis

            client = Redis.from_url(
                url,
                decode_responses=True,
                socket_connect_timeout=timeout_s,
                socket_timeout=timeout_s,
            )
        self._client = client
        self._key = key
        self._ttl_s = ttl_s
        self.save_count = 0

    async def ping(self) -> bool:
        """Probe connectivity (used once at startup, never per tick)."""
        return bool(await self._client.ping())

    async def save(self, snapshot: StateSnapshot) -> None:
        await self._client.set(self._key, encode_state(snapshot), ex=self._ttl_s)
        self.save_count += 1

    async def load(self) -> Optional[StateSnapshot]:
        raw = await self._client.get(self._key)
        return decode_state(raw) if raw else None

    async def close(self) -> None:
        await self._client.aclose()


class ResilientStateCache:
    """Write-through wrapper: degrades to memory on failure, recovers on success."""

    def __init__(self, primary: StateCache, fallback: Optional[StateCache] = None) -> None:
        self.primary = primary
        self.fallback = fallback or MemoryStateCache()
        self.degraded = False
        self.save_count = 0

    @property
    def name(self) -> str:
        return self.primary.name

    async def save(self, snapshot: StateSnapshot) -> None:
        try:
            await self.primary.save(snapshot)
        except Exception as exc:
            if not self.degraded:
                self.degraded = True
                logger.warning(
                    "state cache write failed (%s); using in-memory fallback until it recovers",
                    exc,
                )
            await self.fallback.save(snapshot)
        else:
            if self.degraded:
                self.degraded = False
                logger.info("state cache recovered; resuming writes to %s", self.primary.name)
        self.save_count += 1

    async def load(self) -> Optional[StateSnapshot]:
        try:
            snapshot = await self.primary.load()
        except Exception:
            return await self.fallback.load()
        # An empty primary means Redis never saw this state (fresh/restarted):
        # serve whatever the fallback holds instead of returning nothing.
        return snapshot if snapshot is not None else await self.fallback.load()

    async def close(self) -> None:
        try:
            await self.primary.close()
        except Exception:
            pass  # closing is best-effort
        await self.fallback.close()


async def build_state_cache(
    settings: Settings,
    *,
    client_factory: Optional[Callable[[str], Any]] = None,
) -> StateCache:
    """Build the configured cache **without ever blocking or failing startup**.

    Returns a :class:`MemoryStateCache` when caching is disabled, the URL is
    empty, the redis package is missing, or Redis is unreachable - with a clear
    development warning so demo/local keep working offline.
    """
    if not settings.state_cache_enabled or not settings.redis_url.strip():
        logger.info(
            "state cache disabled (STATE_CACHE_ENABLED=false or empty REDIS_URL); "
            "using the in-memory cache"
        )
        return MemoryStateCache()

    primary: Optional[RedisStateCache] = None
    try:
        client = client_factory(settings.redis_url) if client_factory else None
        primary = RedisStateCache(
            settings.redis_url,
            client=client,
            ttl_s=settings.redis_ttl_s,
            timeout_s=settings.redis_timeout_s,
        )
        if not await primary.ping():
            raise ConnectionError("ping returned false")
    except Exception as exc:
        if primary is not None:
            try:
                await primary.close()
            except Exception:
                pass  # best effort
        logger.warning(
            "Redis unavailable at %s (%s); continuing with the in-memory state cache "
            "(development fallback - start Redis or set REDIS_URL / STATE_CACHE_ENABLED)",
            settings.redis_url,
            exc,
        )
        return MemoryStateCache()

    logger.info("state cache connected to %s", settings.redis_url)
    return ResilientStateCache(primary)
