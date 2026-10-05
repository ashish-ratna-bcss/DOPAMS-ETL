-- Repair for databases that applied the first Phase 6 chargesheet rekey
-- before update rows were protected from the chargesheets label.
-- Rows whose raw id belongs to charge_sheet_updates, and not to
-- chargesheets, are update rows.

UPDATE chargesheets_unified
SET source_module = 'charge_sheet_updates',
    charge_sheet_id = 'V2:charge_sheet_updates:' || source_record_id
WHERE source_system = 'V2'
  AND source_record_id IN (
      SELECT source_record_id FROM chargesheets_source
      WHERE source_system = 'V2' AND source_table = 'charge_sheet_updates'
  )
  AND source_record_id NOT IN (
      SELECT source_record_id FROM chargesheets_source
      WHERE source_system = 'V2' AND source_table = 'chargesheets'
  )
  AND charge_sheet_id NOT LIKE 'V2:charge_sheet_updates:%';
