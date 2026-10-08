-- CCTNS accused_status values exceed VARCHAR(40)
-- (e.g. "Surrendered in court on anticipatory and released on bail").
-- Preserve full source text; do not truncate.

ALTER TABLE accused_enrichment
    ALTER COLUMN status TYPE VARCHAR(255);
