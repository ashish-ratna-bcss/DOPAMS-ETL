-- AI extraction auditability: persist model I/O without duplicating source text.
-- Applied only to the unified write target (dopams_cctns_v2 for the enhanced build).

ALTER TABLE ai_extraction_attempts
    ADD COLUMN IF NOT EXISTS source_system VARCHAR(2),
    ADD COLUMN IF NOT EXISTS source_module VARCHAR(100),
    ADD COLUMN IF NOT EXISTS raw_response TEXT,
    ADD COLUMN IF NOT EXISTS parsed_response JSONB,
    ADD COLUMN IF NOT EXISTS validation_status VARCHAR(40),
    ADD COLUMN IF NOT EXISTS validation_errors JSONB;

COMMENT ON COLUMN ai_extraction_attempts.source_system IS
    'V1 or V2 source of the brief-facts / FIR text used for the attempt.';
COMMENT ON COLUMN ai_extraction_attempts.source_module IS
    'Source table/module label (e.g. crimes, fir). Full source text is not duplicated.';
COMMENT ON COLUMN ai_extraction_attempts.raw_response IS
    'Bounded raw model response body for audit replay.';
COMMENT ON COLUMN ai_extraction_attempts.parsed_response IS
    'Parsed JSON payload after schema validation (pre source-evidence filter).';
COMMENT ON COLUMN ai_extraction_attempts.validation_status IS
    'accepted | rejected | partial | empty | n/a';
COMMENT ON COLUMN ai_extraction_attempts.validation_errors IS
    'List of {raw_drug_name, reason} rejection records.';
