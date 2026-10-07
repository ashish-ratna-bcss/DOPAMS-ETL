-- Accused brief-facts enrichment for existing CCTNS accused_id only.
-- Derived fields sit beside accused_unified. No synthetic accused_id.
-- Applied to dopams_cctns only.

ALTER TABLE accused_enrichment
    ADD COLUMN IF NOT EXISTS accused_code VARCHAR(50),
    ADD COLUMN IF NOT EXISTS person_id VARCHAR(100),
    ADD COLUMN IF NOT EXISTS age INTEGER,
    ADD COLUMN IF NOT EXISTS alias_name VARCHAR(255),
    ADD COLUMN IF NOT EXISTS gender VARCHAR(50),
    ADD COLUMN IF NOT EXISTS occupation VARCHAR(255),
    ADD COLUMN IF NOT EXISTS address TEXT,
    ADD COLUMN IF NOT EXISTS phone_numbers VARCHAR(255),
    ADD COLUMN IF NOT EXISTS status VARCHAR(40),
    ADD COLUMN IF NOT EXISTS is_ccl BOOLEAN,
    ADD COLUMN IF NOT EXISTS key_details TEXT,
    ADD COLUMN IF NOT EXISTS field_sources JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN accused_enrichment.person_id IS
    'Existing CCTNS person_id when present. Never invented for a narrative-only name.';
COMMENT ON COLUMN accused_enrichment.is_ccl IS
    'True/false only from this accused age or an explicit CCL statement about them. NULL when unresolved.';
COMMENT ON COLUMN accused_enrichment.field_sources IS
    'Per-field provenance: DB | LLM_FALLBACK | source_category | age_rule | explicit_ccl.';
