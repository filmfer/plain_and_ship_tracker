-- 0003_add_spatial_index.sql - Add GIST index for spatial queries.
--
-- Design notes:
-- ------------
-- Now that historical storage is established, index the geometry to allow 
-- efficient spatial lookups (e.g., area-of-interest queries).
CREATE INDEX idx_positions_geog ON positions USING GIST (geog);
