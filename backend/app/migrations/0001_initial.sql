-- 0001_initial.sql - historical storage for tracked entities and positions.
--
-- Design notes
-- ------------
-- * All timestamps are ``timestamptz`` (instant on the UTC timeline); the
--   application only ever passes timezone-aware datetimes. ``ts`` is the
--   *measurement* time of a position, not the ingestion time.
-- * Positions are stored as ``geography(Point, 4326)`` so later distance work
--   (e.g. impossible-jump checks in metres, ST_DWithin) is accurate on the
--   spheroid and behaves correctly across the antimeridian. Latitude/longitude
--   are recovered with ST_X/ST_Y - no duplicated scalar columns.
-- * Indexes: only the two primary keys. The composite PK (entity_id, ts)
--   exactly covers this phase's single access pattern - reading one entity's
--   trail over a time window (WHERE entity_id = ? AND ts >= ? ORDER BY ts).
--   No spatial (GiST) index until a spatial query actually exists; no index on
--   bare ``ts`` until a cross-entity time query exists. Add them in a later
--   migration when a real query needs them.
-- * ``positions.entity_id`` foreign-keys to ``entities``; its leading column is
--   the positions PK, so the FK needs no extra index either.

CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE entities (
    entity_id    text PRIMARY KEY,
    entity_type  text NOT NULL CHECK (entity_type IN ('aircraft', 'ship')),
    identifier   text NOT NULL,
    source       text NOT NULL,
    name         text,
    callsign     text,
    registration text,
    icao24       text,
    mmsi         text,
    imo          text,
    first_seen   timestamptz,
    last_seen    timestamptz
);

CREATE TABLE positions (
    entity_id     text NOT NULL REFERENCES entities (entity_id) ON DELETE CASCADE,
    ts            timestamptz NOT NULL,
    geog          geography(Point, 4326) NOT NULL,
    source        text NOT NULL,
    quality       text NOT NULL DEFAULT 'unknown',
    altitude      double precision,
    speed         double precision,
    heading       double precision,
    course        double precision,
    vertical_rate double precision,
    signal_age    double precision,
    PRIMARY KEY (entity_id, ts)
);
