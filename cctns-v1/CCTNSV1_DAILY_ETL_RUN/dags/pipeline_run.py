"""
Shared pipeline logic, split into two independent runs to match the two
separate DAGs in this folder:

    run_simple_apis()    -- FIR, Court, Accused Details (plain unfiltered
                             GET each, no date chunking needed)
    run_accused_yearly() -- Accused date-range endpoint only (needs
                             7-day chunking + adaptive halving
                             against the flaky Oracle backend)

Data goes straight from the API into Postgres in memory -- no intermediate
JSON files. Stages: extract → validate (dedupe + FIR FK) → upsert → Postgres logs
(cctns_v1_etl_run_log, cctns_v1_etl_row_action, cctns_v1_audit_log on field updates).
Airflow also captures stdout in task logs.
A separate local log file would just be a third copy of the same
information, so there isn't one.

Runnable standalone for testing:
    python dags/pipeline_run.py simple
    python dags/pipeline_run.py accused

Current upsert-ready status per entity (see db/sql/001_schema_fix.sql):
    fir              -- ready (real PK, 0 duplicates)
    court            -- ready (natural_key in 004_natural_keys_court_accused_details.sql)
    accused_details  -- ready (same)
    accused          -- ready (natural_key in 006_natural_key_cctns_accused.sql)
Entities that aren't ready are fetched and logged (row count only) but not
written to Postgres -- see pipeline.md.
"""
import os
import sys
import uuid
import logging

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from apis.fir import fetch_fir  # noqa: E402
from apis.court import fetch_court  # noqa: E402
from apis.accused_details import fetch_accused_details  # noqa: E402
from apis.accused import fetch_accused  # noqa: E402
from db.connection import get_connection  # noqa: E402
from db.failed_windows import (  # noqa: E402
    failed_windows_for_run_log,
    normalize_failed_windows,
    record_open_failed_windows,
    resolve_windows_not_failing,
)
from db.run_lock import EntityRunLock  # noqa: E402
from db.run_log import abandon_stale_running, finish_entity_run, start_entity_run  # noqa: E402
from db.upsert import upsert_records  # noqa: E402
from db.validate import dedupe_batch, filter_orphan_fir  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("cctns_v1_etl.pipeline")

# Incomplete extract: windows failed → no upsert, Airflow must fail.
STATUS_EXTRACT_PARTIAL_FAILED = "extract_partial_failed"

SIMPLE_ENTITIES = {
    "fir":             {"fetch": fetch_fir,             "table": "cctns_fir",            "conflict_col": "fir_reg_num", "upsert_ready": True},
    "court":           {"fetch": fetch_court,            "table": "cctns_court",          "conflict_col": "natural_key", "upsert_ready": True},
    "accused_details": {"fetch": fetch_accused_details,  "table": "cctns_accused_details","conflict_col": "natural_key", "upsert_ready": True},
}

ACCUSED_YEARLY_ENTITY = {
    "accused": {"fetch": fetch_accused, "table": "cctns_accused", "conflict_col": "natural_key", "upsert_ready": True},
}


def raise_if_task_failed(entity: str, result: dict) -> None:
    """Airflow fails on extract/load errors and on any incomplete date-window extract."""
    if result.get("status") in (
        "extract_failed",
        "load_failed",
        STATUS_EXTRACT_PARTIAL_FAILED,
    ):
        raise RuntimeError(f"{entity} failed: {result}")


def _sync_failed_window_ledger(cur, entity: str, run_id: str, windows: list) -> None:
    """OPEN still-failing windows; RESOLVE prior OPEN windows no longer failing."""
    record_open_failed_windows(cur, entity, run_id, windows)
    resolve_windows_not_failing(cur, entity, run_id, windows)


def run_single_entity(entity_key: str) -> dict:
    """Run one API entity (used by per-task Airflow operators)."""
    registry = {**SIMPLE_ENTITIES, **ACCUSED_YEARLY_ENTITY}
    if entity_key not in registry:
        raise KeyError(f"Unknown entity: {entity_key}")
    run_id = str(uuid.uuid4())
    logger.info("=== entity=%s run_id=%s ===", entity_key, run_id)
    conn = get_connection()
    try:
        summary = _run_entities({entity_key: registry[entity_key]}, run_id, conn)
        result = summary[entity_key]
        logger.info("=== entity=%s result=%s ===", entity_key, result)
        return result
    finally:
        conn.close()


def _run_entities(entities: dict, run_id: str, conn) -> dict:
    """
    Pipeline stages per entity:
        1. API call (extract)
        2. Validate — batch duplicate removal + FIR parent check (child tables)
        3. Load — compare with DB: insert / update / ignore unchanged
        4. Log — cctns_v1_etl_run_log (summary) + cctns_v1_etl_row_action (insert/update)
                 + cctns_v1_audit_log (field-level on update, DB trigger)
                 + cctns_v1_failed_fetch_window (OPEN / RESOLVED)

    Each entity holds an exclusive process lock so scheduled + manual Airflow
    runs (or CLI) cannot hammer the same CCTNS API concurrently.
    """
    summary = {}
    for entity, cfg in entities.items():
        with EntityRunLock(entity):
            summary[entity] = _run_one_entity(entity, cfg, run_id, conn)
    return summary


def _run_one_entity(entity: str, cfg: dict, run_id: str, conn) -> dict:
    cur = conn.cursor()
    abandoned = abandon_stale_running(cur, entity)
    if abandoned:
        conn.commit()
        logger.warning(
            "%s: closed %s stale status=running row(s) after acquiring run lock",
            entity,
            abandoned,
        )
    log_id = start_entity_run(cur, run_id, entity)
    conn.commit()

    logger.info("--- %s [1/4] API extract ---", entity)
    try:
        records, failed_windows_raw = cfg["fetch"]()
    except Exception as err:
        conn.rollback()
        logger.exception("%s: EXTRACT FAILED", entity)
        cur = conn.cursor()
        finish_entity_run(
            cur,
            log_id,
            status="extract_failed",
            error_message=str(err),
        )
        conn.commit()
        return {"status": "extract_failed"}

    failed_windows = normalize_failed_windows(failed_windows_raw)
    fw_log = failed_windows_for_run_log(failed_windows) if failed_windows else None
    fetched = len(records)
    logger.info("%s: fetched=%d failed_windows=%d", entity, fetched, len(failed_windows))

    # Fail-closed: any failed date window means incomplete source → no upsert,
    # durable OPEN ledger rows, Airflow task failure via raise_if_task_failed.
    if failed_windows:
        logger.error(
            "%s: extract incomplete — %d failed window(s); skipping upsert",
            entity,
            len(failed_windows),
        )
        cur = conn.cursor()
        _sync_failed_window_ledger(cur, entity, run_id, failed_windows)
        finish_entity_run(
            cur,
            log_id,
            status=STATUS_EXTRACT_PARTIAL_FAILED,
            rows_fetched=fetched,
            failed_windows=fw_log,
            error_message=f"{len(failed_windows)} date window(s) failed; load skipped",
        )
        conn.commit()
        return {
            "status": STATUS_EXTRACT_PARTIAL_FAILED,
            "fetched": fetched,
            "failed_windows": len(failed_windows),
        }

    # Full extract succeeded: any prior OPEN gaps for this entity are resolved.
    cur = conn.cursor()
    _sync_failed_window_ledger(cur, entity, run_id, [])
    conn.commit()

    if not cfg["upsert_ready"]:
        logger.warning(
            "%s: fetched %d rows but NOT loaded -- upsert not enabled for this entity.",
            entity,
            fetched,
        )
        cur = conn.cursor()
        finish_entity_run(
            cur,
            log_id,
            status="not_loaded",
            rows_fetched=fetched,
            failed_windows=None,
        )
        conn.commit()
        return {
            "status": "not_loaded_pending_key",
            "fetched": fetched,
            "failed_windows": 0,
        }

    logger.info("--- %s [2/4] validate — dedupe batch + FIR relationship ---", entity)
    records, dupes_removed = dedupe_batch(entity, records)
    records, orphan_skipped = filter_orphan_fir(entity, records, conn)
    logger.info(
        "%s: after filter rows=%d batch_dupes_removed=%d orphan_fir_skipped=%d",
        entity,
        len(records),
        dupes_removed,
        orphan_skipped,
    )

    logger.info("--- %s [3/4] load — insert / update / ignore unchanged ---", entity)
    cur = conn.cursor()
    try:
        result = upsert_records(
            cur,
            cfg["table"],
            cfg["conflict_col"],
            records,
            entity=entity,
            run_id=run_id,
        )
        finish_entity_run(
            cur,
            log_id,
            status="loaded",
            rows_fetched=fetched,
            rows_inserted=result["inserted"],
            rows_updated=result["updated"],
            rows_unchanged=result["unchanged"],
            rows_batch_dupes_removed=dupes_removed,
            rows_orphan_fir_skipped=orphan_skipped,
            failed_windows=None,
        )
        conn.commit()
        logger.info("--- %s [4/4] run log + row actions committed ---", entity)
        return {
            "status": "loaded",
            "fetched": fetched,
            "failed_windows": 0,
            "batch_dupes_removed": dupes_removed,
            "orphan_fir_skipped": orphan_skipped,
            **result,
        }
    except Exception as err:
        conn.rollback()
        logger.exception("%s: LOAD FAILED", entity)
        cur = conn.cursor()
        finish_entity_run(
            cur,
            log_id,
            status="load_failed",
            rows_fetched=fetched,
            rows_batch_dupes_removed=dupes_removed,
            rows_orphan_fir_skipped=orphan_skipped,
            error_message=str(err),
            failed_windows=None,
        )
        conn.commit()
        return {"status": "load_failed"}


def run_simple_apis():
    """FIR, Court, Accused Details -- 3 plain unfiltered-GET endpoints."""
    run_id = str(uuid.uuid4())
    logger.info("=== Starting simple-APIs run_id=%s (fir, court, accused_details) ===", run_id)
    conn = get_connection()
    try:
        summary = _run_entities(SIMPLE_ENTITIES, run_id, conn)
    finally:
        conn.close()
    logger.info("=== simple-APIs run %s complete ===", run_id)
    for entity, result in summary.items():
        logger.info("  %-16s %s", entity, result)
    return run_id, summary


def run_accused_yearly():
    """Accused date-range endpoint only -- 7-day chunks + adaptive halving."""
    run_id = str(uuid.uuid4())
    logger.info("=== Starting accused-yearly run_id=%s (accused date-range) ===", run_id)
    conn = get_connection()
    try:
        summary = _run_entities(ACCUSED_YEARLY_ENTITY, run_id, conn)
    finally:
        conn.close()
    logger.info("=== accused-yearly run %s complete ===", run_id)
    for entity, result in summary.items():
        logger.info("  %-16s %s", entity, result)
    return run_id, summary


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "simple"
    if mode == "simple":
        run_simple_apis()
    elif mode == "accused":
        run_accused_yearly()
    else:
        print("Usage: python dags/pipeline_run.py [simple|accused]")
        sys.exit(1)
