-- Unique natural_key for court + accused_details (expressions validated against live API).
-- Uses BEFORE INSERT/UPDATE triggers (timestamps are not immutable for GENERATED columns).

ALTER TABLE cctns.cctns_court
    ADD COLUMN IF NOT EXISTS natural_key TEXT;

CREATE OR REPLACE FUNCTION cctns.trg_cctns_court_natural_key() RETURNS trigger AS $$
BEGIN
    NEW.natural_key :=
        COALESCE(NEW.fir_reg_num, '') || '|' ||
        COALESCE(NEW.chargesheet_dt::text, '') || '|' ||
        COALESCE(NEW.court_disposal_dt::text, '') || '|' ||
        COALESCE(NEW.court_case_num, '') || '|' ||
        COALESCE(NEW.court_name, '') || '|' ||
        COALESCE(NEW.court_disposal_type, '') || '|' ||
        COALESCE(NEW.court_remarks, '');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_cctns_court_natural_key ON cctns.cctns_court;
CREATE TRIGGER trg_cctns_court_natural_key
    BEFORE INSERT OR UPDATE ON cctns.cctns_court
    FOR EACH ROW EXECUTE FUNCTION cctns.trg_cctns_court_natural_key();

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_cctns_court_natural_key'
    ) THEN
        ALTER TABLE cctns.cctns_court
            ADD CONSTRAINT uq_cctns_court_natural_key UNIQUE (natural_key);
    END IF;
END $$;

ALTER TABLE cctns.cctns_accused_details
    ADD COLUMN IF NOT EXISTS natural_key TEXT;

CREATE OR REPLACE FUNCTION cctns.trg_cctns_accused_details_natural_key() RETURNS trigger AS $$
BEGIN
    NEW.natural_key :=
        COALESCE(NEW.fir_reg_num, '') || '|' ||
        COALESCE(NEW.person_code, '') || '|' ||
        COALESCE(NEW.accused_name, '') || '|' ||
        COALESCE(NEW.gender, '') || '|' ||
        COALESCE(NEW.age::text, '') || '|' ||
        COALESCE(NEW.father_name, '') || '|' ||
        COALESCE(NEW.mobile_1, '') || '|' ||
        COALESCE(NEW.accused_present_address, '') || '|' ||
        COALESCE(NEW.accused_permanent_address, '') || '|' ||
        COALESCE(NEW.is_arrested, '') || '|' ||
        COALESCE(NEW.arrest_surrender_dt::text, '');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_cctns_accused_details_natural_key ON cctns.cctns_accused_details;
CREATE TRIGGER trg_cctns_accused_details_natural_key
    BEFORE INSERT OR UPDATE ON cctns.cctns_accused_details
    FOR EACH ROW EXECUTE FUNCTION cctns.trg_cctns_accused_details_natural_key();

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_cctns_accused_details_natural_key'
    ) THEN
        ALTER TABLE cctns.cctns_accused_details
            ADD CONSTRAINT uq_cctns_accused_details_natural_key UNIQUE (natural_key);
    END IF;
END $$;

DROP TRIGGER IF EXISTS trg_audit_court ON cctns.cctns_court;
CREATE TRIGGER trg_audit_court
    AFTER UPDATE ON cctns.cctns_court
    FOR EACH ROW EXECUTE FUNCTION cctns.cctns_v1_log_row_changes('natural_key');

DROP TRIGGER IF EXISTS trg_audit_accused_details ON cctns.cctns_accused_details;
CREATE TRIGGER trg_audit_accused_details
    AFTER UPDATE ON cctns.cctns_accused_details
    FOR EACH ROW EXECUTE FUNCTION cctns.cctns_v1_log_row_changes('natural_key');
