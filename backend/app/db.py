from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Iterable, Optional, Protocol

import psycopg
from psycopg.rows import tuple_row

from app.models import Alert, DataQuality, Entity, Position, Source

UPSERT_ENTITY_SQL = """
INSERT INTO entities (
    entity_id, entity_type, identifier, source, name, callsign,
    registration, icao24, mmsi, imo, first_seen, last_seen
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (entity_id) DO UPDATE SET
    first_seen = LEAST(entities.first_seen, EXCLUDED.first_seen),
    last_seen = GREATEST(entities.last_seen, EXCLUDED.last_seen),
    name = COALESCE(EXCLUDED.name, entities.name),
    callsign = COALESCE(EXCLUDED.callsign, entities.callsign),
    registration = COALESCE(EXCLUDED.registration, entities.registration),
    icao24 = COALESCE(EXCLUDED.icao24, entities.icao24),
    mmsi = COALESCE(EXCLUDED.mmsi, entities.mmsi),
    imo = COALESCE(EXCLUDED.imo, entities.imo)
"""

INSERT_POSITION_SQL = """
INSERT INTO positions (
    entity_id, ts, geog, source, quality, altitude, speed,
    heading, course, vertical_rate, signal_age
) VALUES (%s, %s, ST_MakePoint(%s, %s)::geography, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT DO NOTHING
"""

LOAD_TRACK_SQL = """
SELECT ts, ST_X(geog::geometry), ST_Y(geog::geometry), source, quality,
       altitude, speed, heading, course, vertical_rate, signal_age
FROM positions
WHERE entity_id = %s
  AND (%s IS NULL OR ts >= %s)
  AND (%s IS NULL OR ts <= %s)
ORDER BY ts DESC
LIMIT %s
"""

class Database(Protocol):
    name: str
    store_count: int
    def store_history(self, entities: Iterable[Entity], positions: Iterable[Position], *, seen_at: Optional[datetime] = None) -> int: ...
    def load_track(self, entity_id: str, *, since: Optional[datetime] = None, until: Optional[datetime] = None, limit: int = 500) -> list[Position]: ...
    def store_alert(self, alert: Alert) -> None: ...
    def resolve_alert(self, alert_id: str, resolved_at: datetime) -> None: ...
    def list_alerts(self, entity_id: Optional[str] = None, only_active: bool = False) -> list[Alert]: ...
    def get_alert(self, alert_id: str) -> Optional[Alert]: ...
    def acknowledge_alert(self, alert_id: str) -> None: ...
    def close(self) -> None: ...

class NullDatabase:
    name = "none"
    store_count = 0
    def store_history(self, *args, **kwargs) -> int:
        self.store_count += 1
        return 0
    def load_track(self, *args, **kwargs) -> list[Position]: return []
    def store_alert(self, alert: Alert) -> None: pass
    def resolve_alert(self, alert_id: str, resolved_at: datetime) -> None: pass
    def list_alerts(self, entity_id: Optional[str] = None, only_active: bool = False) -> list[Alert]: return []
    def get_alert(self, alert_id: str) -> Optional[Alert]: return None
    def acknowledge_alert(self, alert_id: str) -> None: pass
    def close(self) -> None: pass

class PostgresDatabase:
    name = "postgresql"
    store_count = 0

    def __init__(self, conn: Any):
        self._conn = conn

    def store_history(self, entities: Iterable[Entity], positions: Iterable[Position], *, seen_at: Optional[datetime] = None) -> int:
        self.store_count += 1
        inserted = 0
        with self._conn.transaction():
            for entity in entities:
                ts = seen_at or max((p.timestamp for p in positions if p.entity_id == entity.id), default=datetime.now())
                if ts.tzinfo is None: raise ValueError("timezone-aware")
                self._conn.execute(UPSERT_ENTITY_SQL, (
                    entity.id, entity.type.value, entity.identifier, entity.source.value,
                    entity.name, entity.callsign, entity.registration, entity.icao24,
                    entity.mmsi, entity.imo, ts, ts
                ))

            for pos in positions:
                cursor = self._conn.execute(INSERT_POSITION_SQL, (
                    pos.entity_id, pos.timestamp, pos.longitude, pos.latitude, pos.source.value,
                    pos.quality.value, pos.altitude, pos.speed, pos.heading, pos.course,
                    pos.vertical_rate, pos.signal_age
                ))
                inserted += cursor.rowcount
        return inserted

    def load_track(self, entity_id: str, *, since: Optional[datetime] = None, until: Optional[datetime] = None, limit: int = 500) -> list[Position]:
        if (since and since.tzinfo is None) or (until and until.tzinfo is None): raise ValueError("timezone-aware")
        cursor = self._conn.execute(LOAD_TRACK_SQL, (entity_id, since, since, until, until, limit))
        return [Position(
            entity_id=entity_id,
            timestamp=row[0],
            longitude=row[2],
            latitude=row[1],
            source=Source(row[3]),
            quality=DataQuality(row[4]),
            altitude=row[5],
            speed=row[6],
            heading=row[7],
            course=row[8],
            vertical_rate=row[9],
            signal_age=row[10]
        ) for row in cursor.fetchall()]

    def store_alert(self, alert: Alert) -> None: pass
    def resolve_alert(self, alert_id: str, resolved_at: datetime) -> None: pass
    def list_alerts(self, entity_id: Optional[str] = None, only_active: bool = False) -> list[Alert]: return []
    def get_alert(self, alert_id: str) -> Optional[Alert]: return None
    def acknowledge_alert(self, alert_id: str) -> None: pass
    def close(self) -> None: self._conn.close()

def _redact_url(url: str) -> str:
    if "@" not in url: return url
    parts = url.split("@")
    auth_part = parts[0].split("//")[1]
    if ":" in auth_part:
        return url.replace(auth_part, "***")
    return url

async def build_database(settings: Any, connector: Any = psycopg.connect) -> Database:
    if not settings.database_url: return NullDatabase()
    try:
        conn = await asyncio.to_thread(connector, settings.database_url, connect_timeout=settings.db_connect_timeout_s, row_factory=tuple_row, autocommit=True)
        return PostgresDatabase(conn)
    except Exception as e:
        logging.warning("PostgreSQL unavailable (%s); continuing without historical storage", e)
        return NullDatabase()

