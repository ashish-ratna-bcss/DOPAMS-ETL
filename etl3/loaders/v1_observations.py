"""
V1 source-observation capture: reads via V1Adapter (read-only), writes to
dopams_cctns's *_source tables (read/write, via the unified connection
only). Never writes to V1. Never computes current state.

Module -> destination *_source table, per the entity mapping in
ETL3_MERGER_IMPLEMENTATION_PLAN.md section 3:
  fir              -> crimes_source
  accused          -> accused_source
  accused_details  -> arrests_source   (V1 packs arrest fields into this table)
  court            -> chargesheets_source

INITIAL_RUN_MARKER is used as source_run_id for baseline/full-table reads
that did not come from a specific cctns_v1_etl_run_log run -- it is not a
real run id and is never confused with one (real V1 run ids are UUIDs;
this is a fixed, obviously-not-a-uuid string).
"""
from etl3.loaders.common import write_source_observation
from etl3.sources.v1.adapter import V1Adapter

INITIAL_RUN_MARKER = "__initial__"

MODULE_DEST_TABLE = {
    "fir": "crimes_source",
    "accused": "accused_source",
    "accused_details": "arrests_source",
    "court": "chargesheets_source",
}

# best-effort per-module field names for source_created_at/source_modified_at
# -- V1 business tables all carry created_at/updated_at directly (confirmed
# Phase 2); falls back to None if a key is absent from a given payload.
_TS_FIELDS = {
    "fir": ("created_at", "updated_at"),
    "accused": ("created_at", "updated_at"),
    "accused_details": ("created_at", "updated_at"),
    "court": ("created_at", "updated_at"),
}


def _record_id_for(module: str, row: dict) -> str:
    if module == "fir":
        return row["fir_reg_num"]
    if module == "accused":
        return str(row["accused_id"])
    if module == "accused_details":
        return str(row["accused_id"])
    if module == "court":
        return str(row["court_id"])
    raise ValueError(module)


def _write_row(unified_conn, module: str, row: dict, source_run_id: str, consolidation_run_id: str) -> bool:
    created_field, modified_field = _TS_FIELDS[module]
    return write_source_observation(
        unified_conn,
        MODULE_DEST_TABLE[module],
        source_system="V1",
        source_table=module,
        source_record_id=_record_id_for(module, row),
        source_run_id=source_run_id,
        source_created_at=row.get(created_field),
        source_modified_at=row.get(modified_field),
        source_fetched_at=None,  # V1 has no per-row fetched_at; source_run_id + run metadata carries that
        payload=row,
        consolidation_run_id=consolidation_run_id,
    )


def _record_unresolved_gap(unified_conn, module: str, record_key: str, run_id: str, consolidation_run_id: str):
    """accused-only: an observation that genuinely cannot be resolved to a
    current row via record_key (root cause documented in V1Adapter.get_source_record).
    Surfaced in source_gap_ledger, never silently dropped."""
    with unified_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO source_gap_ledger
                (source_system, gap_type, gap_key, first_seen_at, status, source_evidence_table)
            VALUES ('V1', 'unresolved_record_key', %s, now(), 'OPEN', 'cctns_v1_etl_row_action')
            ON CONFLICT (source_system, gap_type, gap_key) DO NOTHING
            """,
            (f"{module}:{run_id}:{record_key}",),
        )


def capture_initial(unified_conn, module: str, consolidation_run_id: str) -> dict:
    """Baseline: read the full current table, one observation per row,
    source_run_id=INITIAL_RUN_MARKER. Idempotent -- safe to re-run; already-
    captured rows are no-ops via the UNIQUE constraint."""
    adapter = V1Adapter()
    inserted = replayed = 0
    for row in adapter.get_all_current_records(module):
        if _write_row(unified_conn, module, row, INITIAL_RUN_MARKER, consolidation_run_id):
            inserted += 1
        else:
            replayed += 1
    return {"module": module, "mode": "initial", "inserted": inserted, "already_present": replayed}


def capture_incremental(unified_conn, module: str, known_run_ids: set, consolidation_run_id: str) -> dict:
    """Discover runs not in known_run_ids, resolve their changed records, write
    observations. Returns a summary including which run ids were processed,
    so the caller can persist them to consolidation_cursor (a later phase's
    job -- this function does not touch consolidation_cursor itself)."""
    adapter = V1Adapter()
    runs = adapter.discover_new_runs(module, known_run_ids=known_run_ids)
    inserted = replayed = unresolved = 0
    processed_run_ids = []

    for run in runs:
        recs = adapter.get_changed_records(module, run.source_run_id)

        if module in ("fir", "accused"):
            # batch resolve: one query for every record_key in this run, not one query per record
            ids = [r.source_record_id for r in recs]
            resolved = adapter.get_records_by_ids(module, ids)
            for r in recs:
                row = resolved.get(r.source_record_id)
                if row is None:
                    if module == "accused":
                        _record_unresolved_gap(unified_conn, module, r.source_record_id, run.source_run_id, consolidation_run_id)
                    unresolved += 1
                    continue
                if _write_row(unified_conn, module, row, run.source_run_id, consolidation_run_id):
                    inserted += 1
                else:
                    replayed += 1

        else:  # accused_details, court -- resolve via parent FIR, capture that FIR's full current row set
            firs = sorted({adapter.extract_fir_reg_num(module, r.source_record_id) for r in recs} - {None})
            by_fir = adapter.get_records_for_firs(module, firs)
            for fir, rows in by_fir.items():
                for row in rows:
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
        "unresolved": unresolved,
    }
