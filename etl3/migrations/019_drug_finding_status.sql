-- Crime-level drug finding classification, additive and isolated from every
-- existing table: a brand-new table, no existing column/constraint/index
-- touched. drug_extractions stays item-level and only ever holds a real
-- source-supported drug row -- "No drug Found" is never a fabricated
-- drug_extractions row, it is a fact about the crime recorded here instead.
--
-- Only three terminal, successfully-evaluated outcomes are ever stored:
--   'Drug found'     -- at least one source-supported drug persisted
--                        (quantity supported or not -- a drug with a missing
--                        or unsupported quantity is still "Drug found").
--   'No drug Found'  -- exact mandated string: brief facts were fully
--                        processed and no valid drug survived validation
--                        (only non-drug property, or nothing proposed).
--   'Manual review'  -- an item described a seizure (quantity/unit/worth)
--                        but the substance was only suspected, never named
--                        -- never collapsed into "No drug Found".
-- Pending / failed / source-unavailable crimes simply have no row here yet;
-- that absence, not a 4th status value, is what keeps this table from ever
-- being written by a failed or AI-disabled run (see
-- etl3/enrichment/runner.py:_finalize_drug_extraction, the only writer).
--
-- Rollback: DROP TABLE IF EXISTS drug_finding_status;

CREATE TABLE drug_finding_status (
    crime_id            VARCHAR(100) PRIMARY KEY REFERENCES crimes_unified(crime_id) ON DELETE CASCADE,
    source_system       VARCHAR(2) NOT NULL CHECK (source_system IN ('V1','V2')),
    status              VARCHAR(30) NOT NULL CHECK (status IN ('Drug found', 'No drug Found', 'Manual review')),
    method              VARCHAR(60) NOT NULL,
    input_hash          TEXT NOT NULL,
    enrichment_run_id   UUID,
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_drug_finding_status_status ON drug_finding_status(status);
