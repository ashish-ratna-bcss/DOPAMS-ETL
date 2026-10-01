-- Redact PII in cctns_v1_audit_log (trigger + scrub existing rows).
-- Sensitive field names match aadhaar/mobile/email/passport/PAN/etc.

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

-- One-time scrub of plaintext PII already stored in the audit log.
UPDATE cctns.cctns_v1_audit_log
SET old_value = '[REDACTED]',
    new_value = '[REDACTED]'
WHERE cctns.cctns_v1_is_sensitive_audit_field(field_name)
  AND (
      (old_value IS NOT NULL AND old_value <> '' AND old_value <> '[REDACTED]')
   OR (new_value IS NOT NULL AND new_value <> '' AND new_value <> '[REDACTED]')
  );
