-- 0002_alerts.sql - alert persistence.
--
-- Design notes:
-- ------------
-- * alerts.id is a UUID or text primary key, let's use text (e.g. ULID or UUIDv4 string).
-- * entity_id references entities(entity_id).
-- * timestamps are timestamptz.
-- * Indexes cover the most common query patterns (listing alerts for an entity,
--   filtering by status).

CREATE TABLE alerts (
    id           text PRIMARY KEY,
    entity_id    text NOT NULL REFERENCES entities (entity_id) ON DELETE CASCADE,
    ts           timestamptz NOT NULL,
    anomaly_type text NOT NULL,
    score        integer NOT NULL,
    severity     text NOT NULL,
    description  text NOT NULL,
    detected_at  timestamptz NOT NULL,
    resolved_at  timestamptz,
    acknowledged boolean NOT NULL DEFAULT false
);

CREATE INDEX idx_alerts_entity ON alerts (entity_id);
CREATE INDEX idx_alerts_status ON alerts (resolved_at, acknowledged);
