-- Unique natural_key for court + accused_details (validated against live API pulls).
-- Court: base key + court_name, court_disposal_type, court_remarks (0 distinct-row collisions).
-- Accused details: includes address + is_arrested + arrest_surrender_dt (0 collisions on ~20k rows).

ALTER TABLE cctns.cctns_court
    ADD COLUMN IF NOT EXISTS natural_key TEXT GENERATED ALWAYS AS (
        COALESCE(fir_reg_num, '') || '|' ||
        COALESCE(chargesheet_dt::text, '') || '|' ||
        COALESCE(court_disposal_dt::text, '') || '|' ||
        COALESCE(court_case_num, '') || '|' ||
        COALESCE(court_name, '') || '|' ||
        COALESCE(court_disposal_type, '') || '|' ||
        COALESCE(court_remarks, '')
    ) STORED;

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
    ADD COLUMN IF NOT EXISTS natural_key TEXT GENERATED ALWAYS AS (
        COALESCE(fir_reg_num, '') || '|' ||
        COALESCE(person_code, '') || '|' ||
        COALESCE(accused_name, '') || '|' ||
        COALESCE(gender, '') || '|' ||
        COALESCE(age::text, '') || '|' ||
        COALESCE(father_name, '') || '|' ||
        COALESCE(mobile_1, '') || '|' ||
        COALESCE(accused_present_address, '') || '|' ||
        COALESCE(accused_permanent_address, '') || '|' ||
        COALESCE(is_arrested, '') || '|' ||
        COALESCE(arrest_surrender_dt::text, '')
    ) STORED;

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
