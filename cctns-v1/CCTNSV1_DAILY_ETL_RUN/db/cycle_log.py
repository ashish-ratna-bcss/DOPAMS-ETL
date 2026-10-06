"""Persist the V1 daily-cycle marker in cctns.cctns_v1_etl_cycle."""
from __future__ import annotations

from datetime import datetime

from config.settings import PG_ETL_SCHEMA


class SqlCycleLog:
    def __init__(self, conn):
        self.conn = conn

    def abandon_stale(self) -> None:
        cur = self.conn.cursor()
        cur.execute(
            f"""
            UPDATE {PG_ETL_SCHEMA}.cctns_v1_etl_cycle
            SET status = 'failed',
                finished_at = now(),
                error_message = COALESCE(
                    error_message,
                    'abandoned: process lost or superseded (cycle lock acquired)'
                )
            WHERE status = 'running'
              AND finished_at IS NULL
            """
        )
        self.conn.commit()

    def start(self, run_id: str, cycle_start: datetime) -> None:
        cur = self.conn.cursor()
        cur.execute(
            f"""
            INSERT INTO {PG_ETL_SCHEMA}.cctns_v1_etl_cycle
                (run_id, status, cycle_start)
            VALUES (%s, 'running', %s)
            """,
            (run_id, cycle_start),
        )
        self.conn.commit()

    def finish(self, run_id: str, status: str, error: str | None) -> None:
        cur = self.conn.cursor()
        cur.execute(
            f"""
            UPDATE {PG_ETL_SCHEMA}.cctns_v1_etl_cycle
            SET status = %s,
                finished_at = now(),
                error_message = %s
            WHERE run_id = %s
            """,
            (status, error, run_id),
        )
        self.conn.commit()

    def read_entities(self, run_id: str) -> list[dict]:
        cur = self.conn.cursor()
        cur.execute(
            f"""
            SELECT entity, status, started_at, finished_at, run_id::text
            FROM {PG_ETL_SCHEMA}.cctns_v1_etl_run_log
            WHERE run_id = %s
            """,
            (run_id,),
        )
        rows = []
        for entity, status, started_at, finished_at, row_run_id in cur.fetchall():
            rows.append(
                {
                    "entity": entity,
                    "status": status,
                    "started_at": started_at,
                    "finished_at": finished_at,
                    "run_id": row_run_id,
                }
            )
        return rows
