-- ============================================================================
-- Migration 003: current_as_of -> nullable, across every *_unified table.
--
-- Found during Phase 4, not assumed in advance: V2's 24 placeholder persons
-- rows (etl_run_id IS NULL, investigated and documented in Phase 3's status
-- doc) ALSO have NULL date_created and date_modified -- no timestamp
-- information exists for them at all, anywhere. They are still real,
-- referenced records (23/24 pointed to by accused.person_id) that must be
-- captured in persons_unified for FK integrity, so current_as_of cannot
-- stay NOT NULL. Rather than fabricate a timestamp (e.g. "now()", which
-- would misrepresent ETL-3's own ingestion time as a source fact),
-- current_as_of is made nullable everywhere, consistently, and left NULL
-- when the source genuinely provides nothing.
-- ============================================================================

ALTER TABLE crimes_unified ALTER COLUMN current_as_of DROP NOT NULL;
ALTER TABLE persons_unified ALTER COLUMN current_as_of DROP NOT NULL;
ALTER TABLE accused_unified ALTER COLUMN current_as_of DROP NOT NULL;
ALTER TABLE arrests_unified ALTER COLUMN current_as_of DROP NOT NULL;
ALTER TABLE chargesheets_unified ALTER COLUMN current_as_of DROP NOT NULL;
ALTER TABLE seizures_unified ALTER COLUMN current_as_of DROP NOT NULL;
ALTER TABLE properties_unified ALTER COLUMN current_as_of DROP NOT NULL;
ALTER TABLE fsl_unified ALTER COLUMN current_as_of DROP NOT NULL;
ALTER TABLE disposal_unified ALTER COLUMN current_as_of DROP NOT NULL;
ALTER TABLE interrogation_unified ALTER COLUMN current_as_of DROP NOT NULL;
ALTER TABLE hierarchy_unified ALTER COLUMN current_as_of DROP NOT NULL;
