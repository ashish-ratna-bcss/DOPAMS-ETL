"""
V2 source-observation capture: reads via V2Adapter (read-only), writes to
dopams_cctns's *_source tables. Never writes to V2. Never computes current
state.

Module -> destination *_source table:
  crimes, accused, persons, arrests, chargesheets, disposal, hierarchy map
  1:1 to their namesake *_source table. charge_sheet_updates feeds
  chargesheets_source (a second source_table tag in the same destination,
  per ETL3_UNIFIED_SCHEMA.sql's own comment). mo_seizures -> seizures_source.
  fsl_case_property -> fsl_source. interrogation_reports -> interrogation_source.

The 24 V2 `persons` rows with no etl_run_id (Phase 2 finding, root-caused
this phase -- see investigate section in PHASE3_SOURCE_OBSERVATION_STATUS.md):
confirmed by direct query to be real, referenced records (23/24 pointed to
by accused.person_id, 1/24 also by interrogation_reports.person_id) with
every other field NULL -- stub rows created to satisfy a relationship,
never enriched. They are correctly captured by capture_initial() below
because get_all_current_records() reads the table directly (SELECT *, no
etl_run_id filter) -- NOT because of any special-case code. They cannot
participate in capture_incremental() (no etl_run_id to discover a run from)
until V2's own persons ETL eventually enriches them, at which point they
become ordinary discoverable rows with no ETL-3 change needed. A
source_gap_ledger entry is written once to make this visibly tracked.
"""
from etl3.loaders.common import write_source_observation
from etl3.sources.v2.adapter import MODULE_PK

MODULE_DEST_TABLE = {
    "crimes": "crimes_source",
    "accused": "accused_source",
    "persons": "persons_source",
    "arrests": "arrests_source",
    "chargesheets": "chargesheets_source",
    "charge_sheet_updates": "chargesheets_source",
    "disposal": "disposal_source",
    "mo_seizures": "seizures_source",
    "properties": "properties_source",
    "fsl_case_property": "fsl_source",
    "interrogation_reports": "interrogation_source",
    "hierarchy": "hierarchy_source",
}


def _write_row(unified_conn, module: str, row: dict, source_run_id: str, consolidation_run_id: str) -> bool:
    pk_col = MODULE_PK[module]
    return write_source_observation(
        unified_conn,
        MODULE_DEST_TABLE[module],
        source_system="V2",
        source_table=module,
        source_record_id=str(row[pk_col]),
        source_run_id=source_run_id,
        source_created_at=row.get("date_created"),
        source_modified_at=row.get("date_modified"),
        source_fetched_at=row.get("fetched_at"),
        payload=row,
        consolidation_run_id=consolidation_run_id,
    )


def record_placeholder_persons_gap(unified_conn, count: int):
    with unified_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO source_gap_ledger
                (source_system, gap_type, gap_key, first_seen_at, status, source_evidence_table)
            VALUES ('V2', 'unlinked_persons_placeholder', 'persons.etl_run_id IS NULL', now(), 'OPEN', 'persons')
            ON CONFLICT (source_system, gap_type, gap_key)
            DO UPDATE SET first_seen_at = source_gap_ledger.first_seen_at
            """
        )


def capture_initial(unified_conn, module: str, consolidation_run_id: str) -> dict:
    """Baseline: read the full current table, one observation per row. This
    is the ONLY path that captures rows with no etl_run_id (e.g. the 24
    placeholder persons) -- it reads the table directly, not grouped by run."""
    from etl3.sources.v2.adapter import V2Adapter

    adapter = V2Adapter()
    inserted = replayed = no_etl_run_id = 0
    for row in adapter.get_all_current_records(module):
        run_id = str(row.get("etl_run_id")) if row.get("etl_run_id") else "__initial_no_run_id__"
        if row.get("etl_run_id") is None:
            no_etl_run_id += 1
        if _write_row(unified_conn, module, row, run_id, consolidation_run_id):
            inserted += 1
        else:
            replayed += 1

    if module == "persons" and no_etl_run_id:
        record_placeholder_persons_gap(unified_conn, no_etl_run_id)

    return {
        "module": module,
        "mode": "initial",
        "inserted": inserted,
        "already_present": replayed,
        "no_etl_run_id": no_etl_run_id,
    }


def capture_incremental(unified_conn, module: str, known_run_ids: set, consolidation_run_id: str) -> dict:
    """Discover runs (grouped by etl_run_id) not in known_run_ids, write one
    observation per touched record. By construction this can never see rows
    with a NULL etl_run_id -- that is not a bug, see this module's docstring."""
    from etl3.sources.v2.adapter import V2Adapter

    adapter = V2Adapter()
    runs = adapter.discover_new_runs(module, known_run_ids=known_run_ids)
    inserted = replayed = 0
    processed_run_ids = []

    for run in runs:
        recs = adapter.get_changed_records(module, run.source_run_id)
        for r in recs:
            row = adapter.get_source_record(module, r.source_record_id)
            if row is None:
                continue  # record existed when the run was discovered, gone by the time we read it -- extremely unlikely (V2 never deletes), not silently papered over: would show up as a reconciliation delta in a later phase
            if _write_row(unified_conn, module, row, run.source_run_id, consolidation_run_id):
                inserted += 1
            else:
                replayed += 1
        processed_run_ids.append(run.source_run_id)

    return {
        "module": module,
        "mode": "incremental",
        "runs_processed": processed_run_ids,
        "inserted": inserted,
        "already_present": replayed,
    }
