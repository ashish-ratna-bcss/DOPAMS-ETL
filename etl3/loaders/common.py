"""
Shared helpers for writing into dopams_cctns's *_source tables.

Phase 3 scope, deliberately: this module ONLY writes append-only source
observations. It never touches a *_unified table, never resolves identity,
never computes a current-state row, and never writes to V1/V2 -- all
writes here go through etl3.db.connections.get_unified_connection(), the
same safety-checked connection used everywhere else in this codebase.
"""
import json
from datetime import date, datetime
from decimal import Decimal


def _json_default(o):
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, Decimal):
        return str(o)
    return str(o)


def write_source_observation(
    conn,
    dest_table: str,
    *,
    source_system: str,
    source_table: str,
    source_record_id: str,
    source_run_id: str,
    source_created_at,
    source_modified_at,
    source_fetched_at,
    payload: dict,
    consolidation_run_id: str,
) -> bool:
    """
    Idempotent insert into a *_source table. The UNIQUE(source_system,
    source_record_id, source_run_id) constraint (ETL3_UNIFIED_SCHEMA.sql)
    does the actual idempotency enforcement -- ON CONFLICT DO NOTHING means
    replaying the exact same (source, record, run) triple a second time is
    always a safe no-op, never a duplicate row and never an error.

    Returns True if a new row was actually inserted, False if it already
    existed (a replay).
    """
    with conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO {dest_table}
                (source_system, source_table, source_record_id, source_run_id,
                 source_created_at, source_modified_at, source_fetched_at,
                 payload, consolidation_run_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_system, source_record_id, source_run_id) DO NOTHING
            RETURNING id
            """,
            (
                source_system,
                source_table,
                source_record_id,
                source_run_id,
                source_created_at,
                source_modified_at,
                source_fetched_at,
                json.dumps(payload, default=_json_default),
                consolidation_run_id,
            ),
        )
        return cur.fetchone() is not None


def start_consolidation_run(conn) -> str:
    """Records a new row in consolidation_run_log, status='running'. Returns the run_id (str uuid)."""
    import uuid

    run_id = str(uuid.uuid4())
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO consolidation_run_log (run_id, status) VALUES (%s, 'running')",
            (run_id,),
        )
    return run_id


def finish_consolidation_run(conn, run_id: str, *, status: str, sources_processed: dict,
                              rows_observed: int, rows_changed: int = 0, error_message: str = None):
    import json as _json

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE consolidation_run_log
            SET finished_at = now(), status = %s, sources_processed = %s,
                rows_observed = %s, rows_changed = %s, error_message = %s
            WHERE run_id = %s
            """,
            (status, _json.dumps(sources_processed), rows_observed, rows_changed, error_message, run_id),
        )
