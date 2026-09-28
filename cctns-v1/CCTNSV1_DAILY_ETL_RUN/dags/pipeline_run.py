"""
Shared pipeline logic, split into two independent runs to match the two
separate DAGs in this folder:

    run_simple_apis()    -- FIR, Court, Accused Details (plain unfiltered
                             GET each, no date chunking needed)
    run_accused_yearly() -- Accused date-range endpoint only (needs
                             month-by-month chunking + adaptive halving
                             against the flaky Oracle backend)

Data goes straight from the API into Postgres in memory -- no intermediate
JSON files, and no local log files either. Logging goes to stdout/stderr
only: under Airflow that's captured automatically as the task's own log
(Airflow UI -> DAG -> task -> Logs), and the durable structured record of
every run (rows fetched/inserted/updated/unchanged, failed windows, status)
lives in cctns_v1_etl_run_log in Postgres itself -- see db/sql/001_schema_fix.sql.
A separate local log file would just be a third copy of the same
information, so there isn't one.

Runnable standalone for testing:
    python dags/pipeline_run.py simple
    python dags/pipeline_run.py accused

Current upsert-ready status per entity (see db/sql/001_schema_fix.sql):
    fir              -- ready (real PK, 0 duplicates)
    court            -- NOT ready (natural_key pending manual review)
    accused_details  -- NOT ready (same)
    accused          -- NOT ready (same)
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
from db.upsert import upsert_records  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("cctns_v1_etl.pipeline")

SIMPLE_ENTITIES = {
    "fir":             {"fetch": fetch_fir,             "table": "cctns_fir",            "conflict_col": "fir_reg_num", "upsert_ready": True},
    "court":           {"fetch": fetch_court,            "table": "cctns_court",          "conflict_col": "natural_key", "upsert_ready": False},
    "accused_details": {"fetch": fetch_accused_details,  "table": "cctns_accused_details","conflict_col": "natural_key", "upsert_ready": False},
}

ACCUSED_YEARLY_ENTITY = {
    "accused": {"fetch": fetch_accused, "table": "cctns_accused", "conflict_col": "natural_key", "upsert_ready": False},
}


def _run_entities(entities: dict, run_id: str, conn) -> dict:
    summary = {}
    for entity, cfg in entities.items():
        logger.info("--- %s: extracting ---", entity)
        try:
            records, failed_windows = cfg["fetch"]()
        except Exception:
            logger.exception("%s: EXTRACT FAILED", entity)
            summary[entity] = {"status": "extract_failed"}
            continue

        logger.info("%s: fetched=%d failed_windows=%d", entity, len(records), len(failed_windows))

        if not cfg["upsert_ready"]:
            logger.warning(
                "%s: fetched %d rows but NOT loaded -- natural_key/unique constraint "
                "not applied yet (see db/sql/001_schema_fix.sql). Nothing written to "
                "Postgres this run for this entity.",
                entity, len(records),
            )
            summary[entity] = {"status": "not_loaded_pending_key", "fetched": len(records),
                                "failed_windows": len(failed_windows)}
            continue

        cur = conn.cursor()
        try:
            result = upsert_records(cur, cfg["table"], cfg["conflict_col"], records)
            conn.commit()
            summary[entity] = {"status": "loaded", "fetched": len(records),
                                "failed_windows": len(failed_windows), **result}
        except Exception:
            conn.rollback()
            logger.exception("%s: LOAD FAILED", entity)
            summary[entity] = {"status": "load_failed"}
    return summary


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
    """Accused date-range endpoint only -- month-chunked + adaptive halving."""
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
