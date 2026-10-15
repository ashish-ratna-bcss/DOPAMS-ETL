-- Resolved nationality for domicile (from nationality field, name-embedded
-- country, or India when only Indian address geography is confirmed).
-- Does not overwrite persons_unified.nationality.

ALTER TABLE person_enrichment
    ADD COLUMN IF NOT EXISTS nationality VARCHAR(100);
