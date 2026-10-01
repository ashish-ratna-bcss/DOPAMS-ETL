-- ETL support objects in schema `cctns` (idempotent).

ALTER TABLE cctns.cctns_fir
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;

ALTER TABLE cctns.cctns_court
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;

ALTER TABLE cctns.cctns_accused_details
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;

ALTER TABLE cctns.cctns_accused
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;

CREATE TABLE IF NOT EXISTS cctns.cctns_v1_audit_log (
    id            BIGSERIAL PRIMARY KEY,
    table_name    TEXT NOT NULL,
    record_key    TEXT NOT NULL,
    field_name    TEXT NOT NULL,
    old_value     TEXT,
    new_value     TEXT,
    changed_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_audit_table_record ON cctns.cctns_v1_audit_log (table_name, record_key);
CREATE INDEX IF NOT EXISTS idx_audit_changed_at   ON cctns.cctns_v1_audit_log (changed_at);

CREATE OR REPLACE FUNCTION cctns.cctns_v1_is_sensitive_audit_field(field_name text)
RETURNS boolean AS $$
BEGIN
    RETURN field_name ~* '(aadhaar|aadhar|uidai|mobile|phone|email|passport|pan_card|pan$|voter|ration|bank|account|ifsc|dob|birth|card_no|card_num)';
END;
$$ LANGUAGE plpgsql IMMUTABLE;

CREATE OR REPLACE FUNCTION cctns.cctns_v1_redact_audit_value(field_name text, val text)
RETURNS text AS $$
BEGIN
    IF val IS NULL OR val = '' THEN
        RETURN val;
    END IF;
    IF cctns.cctns_v1_is_sensitive_audit_field(field_name) THEN
        RETURN '[REDACTED]';
    END IF;
    RETURN val;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

CREATE OR REPLACE FUNCTION cctns.cctns_v1_log_row_changes() RETURNS trigger AS $$
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
            INSERT INTO cctns.cctns_v1_audit_log (table_name, record_key, field_name, old_value, new_value)
            VALUES (
                TG_TABLE_NAME,
                rec_key,
                k,
                cctns.cctns_v1_redact_audit_value(k, old_j ->> k),
                cctns.cctns_v1_redact_audit_value(k, new_j ->> k)
            );
        END IF;
    END LOOP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_audit_fir ON cctns.cctns_fir;
CREATE TRIGGER trg_audit_fir
    AFTER UPDATE ON cctns.cctns_fir
    FOR EACH ROW EXECUTE FUNCTION cctns.cctns_v1_log_row_changes('fir_reg_num');

CREATE TABLE IF NOT EXISTS cctns.cctns_v1_etl_run_log (
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
CREATE INDEX IF NOT EXISTS idx_etl_run_log_entity_started ON cctns.cctns_v1_etl_run_log (entity, started_at DESC);
