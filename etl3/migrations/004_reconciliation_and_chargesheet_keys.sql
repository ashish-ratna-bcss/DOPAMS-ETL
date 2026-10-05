-- Phase 6. Non-destructive. Widens reconciliation status so the longer
-- classifications fit, and stops V1 court ids from sharing a primary key
-- with V2 charge-sheet update ids.
--
-- The old chargesheets_unified primary key was the raw source id. Every
-- V2 charge_sheet_updates.id also occurs as a V1 court_id, so the later
-- writer could replace the other feed's row. The namespaced key keeps
-- source_record_id as the raw id and puts the feed in the primary key.

ALTER TABLE reconciliation_run_log
    ALTER COLUMN status TYPE VARCHAR(40);

ALTER TABLE chargesheets_unified
    ADD COLUMN IF NOT EXISTS source_module VARCHAR(100);

UPDATE chargesheets_unified
SET source_module = 'court',
    charge_sheet_id = 'V1:court:' || source_record_id
WHERE source_system = 'V1'
  AND charge_sheet_id NOT LIKE 'V1:court:%';

UPDATE chargesheets_unified
SET source_module = 'charge_sheet_updates',
    charge_sheet_id = 'V2:charge_sheet_updates:' || source_record_id
WHERE source_system = 'V2'
  AND charge_sheet_id NOT LIKE 'V2:charge_sheet_updates:%'
  AND source_record_id IN (
      SELECT source_record_id FROM chargesheets_source
      WHERE source_system = 'V2' AND source_table = 'charge_sheet_updates'
  );

UPDATE chargesheets_unified
SET source_module = 'chargesheets',
    charge_sheet_id = 'V2:chargesheets:' || source_record_id
WHERE source_system = 'V2'
  AND source_module IS NULL
  AND charge_sheet_id NOT LIKE 'V2:chargesheets:%';

ALTER TABLE chargesheets_unified
    DROP CONSTRAINT IF EXISTS chargesheets_unified_source_system_source_record_id_key;

ALTER TABLE chargesheets_unified
    DROP CONSTRAINT IF EXISTS chargesheets_unified_module_record_key;

ALTER TABLE chargesheets_unified
    ADD CONSTRAINT chargesheets_unified_module_record_key
    UNIQUE (source_system, source_module, source_record_id);
