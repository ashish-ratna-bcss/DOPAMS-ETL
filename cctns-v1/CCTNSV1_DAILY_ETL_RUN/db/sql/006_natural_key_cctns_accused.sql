-- Accused dossier (date-range POST) — same upsert pattern as 004.
-- Key includes from_dt/to_dt because one FIR can have multiple dossier rows per window.

ALTER TABLE cctns.cctns_accused
    ADD COLUMN IF NOT EXISTS natural_key TEXT;

CREATE OR REPLACE FUNCTION cctns.trg_cctns_accused_natural_key() RETURNS trigger AS $$
BEGIN
    NEW.natural_key :=
        COALESCE(NEW.fir_reg_num, '') || '|' ||
        COALESCE(NEW.accused_name, '') || '|' ||
        COALESCE(NEW.father_name, '') || '|' ||
        COALESCE(NEW.dob::text, '') || '|' ||
        COALESCE(NEW.mobile_1, '') || '|' ||
        COALESCE(NEW.gender, '') || '|' ||
        COALESCE(NEW.age::text, '') || '|' ||
        COALESCE(NEW.present_address, '') || '|' ||
        COALESCE(NEW.permanent_address, '') || '|' ||
        COALESCE(NEW.from_dt::text, '') || '|' ||
        COALESCE(NEW.to_dt::text, '') || '|' ||
        COALESCE(NEW.arrest_surrender_dt::text, '');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_cctns_accused_natural_key ON cctns.cctns_accused;
CREATE TRIGGER trg_cctns_accused_natural_key
    BEFORE INSERT OR UPDATE ON cctns.cctns_accused
    FOR EACH ROW EXECUTE FUNCTION cctns.trg_cctns_accused_natural_key();

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_cctns_accused_natural_key'
    ) THEN
        ALTER TABLE cctns.cctns_accused
            ADD CONSTRAINT uq_cctns_accused_natural_key UNIQUE (natural_key);
    END IF;
END $$;

DROP TRIGGER IF EXISTS trg_audit_accused ON cctns.cctns_accused;
CREATE TRIGGER trg_audit_accused
    AFTER UPDATE ON cctns.cctns_accused
    FOR EACH ROW EXECUTE FUNCTION cctns.cctns_v1_log_row_changes('natural_key');
