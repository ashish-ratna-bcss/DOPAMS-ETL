"""Persist per-entity run summary to cctns.cctns_v1_etl_run_log."""
import json
from typing import Any

from config.settings import PG_ETL_SCHEMA


def abandon_stale_running(cur, entity: str) -> int:
    """Close leftover status=running rows after we hold the entity run lock.

    Those rows are from killed/restarted workers; leaving them open falsely
    suggests concurrent extracts are still in flight.
    """
    cur.execute(
        f"""
        UPDATE {PG_ETL_SCHEMA}.cctns_v1_etl_run_log
        SET status = 'extract_failed',
            finished_at = now(),
            error_message = COALESCE(
                error_message,
                'abandoned: process lost or superseded (run lock acquired)'
            )
        WHERE entity = %s
          AND status = 'running'
          AND finished_at IS NULL
        """,
        (entity,),
    )
    return cur.rowcount or 0


def start_entity_run(cur, run_id: str, entity: str) -> int:
    cur.execute(
        f"""
        INSERT INTO {PG_ETL_SCHEMA}.cctns_v1_etl_run_log (run_id, entity, status)
        VALUES (%s, %s, 'running')
        RETURNING id
        """,
        (run_id, entity),
    )
    row = cur.fetchone()
    return row[0]


def finish_entity_run(cur, log_id: int, *, status: str, **fields: Any) -> None:
    sets = ["status = %s", "finished_at = now()"]
    params: list[Any] = [status]

    for key in (
        "rows_fetched",
        "rows_inserted",
        "rows_updated",
        "rows_unchanged",
        "rows_batch_dupes_removed",
        "rows_orphan_fir_skipped",
        "failed_windows",
        "error_message",
    ):
        if key not in fields:
            continue
        value = fields[key]
        if key == "failed_windows" and value is not None and not isinstance(value, str):
            value = json.dumps(value)
        sets.append(f"{key} = %s")
        params.append(value)

    params.append(log_id)
    cur.execute(
        f"""
        UPDATE {PG_ETL_SCHEMA}.cctns_v1_etl_run_log
        SET {", ".join(sets)}
        WHERE id = %s
        """,
        params,
    )


def log_row_action(cur, run_id: str, entity: str, table: str, record_key: str, action: str) -> None:
    if action not in ("insert", "update"):
        return
    cur.execute(
        f"""
        INSERT INTO {PG_ETL_SCHEMA}.cctns_v1_etl_row_action
            (run_id, entity, table_name, record_key, action)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (run_id, entity, table, record_key, action),
    )
