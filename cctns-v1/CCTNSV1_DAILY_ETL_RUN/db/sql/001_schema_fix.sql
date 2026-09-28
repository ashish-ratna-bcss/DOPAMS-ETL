-- ============================================================================
-- DRAFT -- DO NOT RUN YET.
--
-- This adds the unique constraint + natural_key that the nightly upsert
-- (db/upsert.py) needs to know "have I seen this record before". It is
-- blocked on one open problem, found during real duplicate analysis on
-- dopams-new and confirmed by the user needing to review it personally:
--
--   The obvious composite key per table (fir_reg_num + person_code +
--   accused_name for cctns_accused_details; similar for cctns_accused) is
--   too narrow. Example found: fir_reg_num=2023055220298,
--   person_code=202305522029834080, accused_name="unknown" matches 18 ROWS
--   that are 18 GENUINELY DIFFERENT PEOPLE (different age/gender/address/
--   etc. per row) -- multiple unidentified accused in the same case all get
--   logged with the same generic name/code. Enforcing a unique constraint on
--   the narrow key below would make the nightly upsert silently collapse
--   those 18 people into 1 on the very first run.
--
-- Before running this file:
--   1. Run db/sql/000_dedupe_exact_only.sql first (safe, already scoped to
--      true full-row duplicates only).
--   2. Manually review the ambiguous groups (query below) and decide, per
--      group, whether they're real duplicates or real distinct people.
--   3. Adjust the natural_key expressions below accordingly -- what's here
--      is a first draft, not a reviewed decision.
--
-- Query to pull up the ambiguous groups for review:
--   SELECT fir_reg_num, person_code, accused_name, count(*) AS rows_in_group
--   FROM cctns_accused_details
--   GROUP BY fir_reg_num, person_code, accused_name
--   HAVING count(*) > 1
--   ORDER BY rows_in_group DESC;
-- ============================================================================

BEGIN;

ALTER TABLE cctns_fir
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
-- cctns_fir already has a real PK (fir_reg_num) and 0 duplicates -- safe as-is.

-- ---- DRAFT keys below, pending the review described above ----

-- ALTER TABLE cctns_court
--     ADD COLUMN IF NOT EXISTS natural_key TEXT GENERATED ALWAYS AS (
--         COALESCE(fir_reg_num, '') || '|' ||
--         COALESCE(chargesheet_dt::text, '') || '|' ||
--         COALESCE(court_disposal_dt::text, '') || '|' ||
--         COALESCE(court_case_num, '')
--     ) STORED,
--     ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
-- ALTER TABLE cctns_court ADD CONSTRAINT uq_cctns_court_natural_key UNIQUE (natural_key);

-- ALTER TABLE cctns_accused_details
--     ADD COLUMN IF NOT EXISTS natural_key TEXT GENERATED ALWAYS AS (
--         COALESCE(fir_reg_num, '') || '|' ||
--         COALESCE(person_code, '') || '|' ||
--         COALESCE(accused_name, '') || '|' ||
--         COALESCE(gender, '') || '|' ||
--         COALESCE(age::text, '') || '|' ||
--         COALESCE(father_name, '') || '|' ||
--         COALESCE(mobile_1, '')
--     ) STORED,
--     ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
-- ALTER TABLE cctns_accused_details ADD CONSTRAINT uq_cctns_accused_details_natural_key UNIQUE (natural_key);

-- ALTER TABLE cctns_accused
--     ADD COLUMN IF NOT EXISTS natural_key TEXT GENERATED ALWAYS AS (
--         COALESCE(fir_reg_num, '') || '|' ||
--         COALESCE(accused_name, '') || '|' ||
--         COALESCE(father_name, '') || '|' ||
--         COALESCE(dob::text, '') || '|' ||
--         COALESCE(mobile_1, '')
--     ) STORED,
--     ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
-- ALTER TABLE cctns_accused ADD CONSTRAINT uq_cctns_accused_natural_key UNIQUE (natural_key);

-- ----------------------------------------------------------------------------
-- Audit log + run log: not blocked on the key decision, safe to run once
-- uncommented -- these don't depend on which key design is chosen.
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS cctns_v1_audit_log (
    id            BIGSERIAL PRIMARY KEY,
    table_name    TEXT NOT NULL,
    record_key    TEXT NOT NULL,
    field_name    TEXT NOT NULL,
    old_value     TEXT,
    new_value     TEXT,
    changed_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_audit_table_record ON cctns_v1_audit_log (table_name, record_key);
CREATE INDEX IF NOT EXISTS idx_audit_changed_at   ON cctns_v1_audit_log (changed_at);

CREATE OR REPLACE FUNCTION cctns_v1_log_row_changes() RETURNS trigger AS $$
DECLARE
    old_j jsonb := to_jsonb(OLD);
    new_j jsonb := to_jsonb(NEW);
    pk_col text := TG_ARGV[0];
    rec_key text := new_j ->> pk_col;
    k text;
BEGIN
    FOR k IN SELECT jsonb_object_keys(new_j) LOOP
        IF k IN ('created_at', 'updated_at', 'natural_key') THEN
            CONTINUE;
        END IF;
        IF old_j -> k IS DISTINCT FROM new_j -> k THEN
            INSERT INTO cctns_v1_audit_log (table_name, record_key, field_name, old_value, new_value)
            VALUES (TG_TABLE_NAME, rec_key, k, old_j ->> k, new_j ->> k);
        END IF;
    END LOOP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Triggers for cctns_court / cctns_accused_details / cctns_accused are added
-- once their natural_key columns above are uncommented and applied.
DROP TRIGGER IF EXISTS trg_audit_fir ON cctns_fir;
CREATE TRIGGER trg_audit_fir
    AFTER UPDATE ON cctns_fir
    FOR EACH ROW EXECUTE FUNCTION cctns_v1_log_row_changes('fir_reg_num');

CREATE TABLE IF NOT EXISTS cctns_v1_etl_run_log (
    id              BIGSERIAL PRIMARY KEY,
    run_id          UUID NOT NULL,
    entity          TEXT NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    status          TEXT NOT NULL DEFAULT 'running',
    rows_fetched    INTEGER DEFAULT 0,
    rows_inserted   INTEGER DEFAULT 0,
    rows_updated    INTEGER DEFAULT 0,
    rows_unchanged  INTEGER DEFAULT 0,
    failed_windows  JSONB,
    error_message   TEXT
);
CREATE INDEX IF NOT EXISTS idx_etl_run_log_entity_started ON cctns_v1_etl_run_log (entity, started_at DESC);

COMMIT;
