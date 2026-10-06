"""Run one ETL-3 incremental pass after the latest V1 cycle and a successful V2 run.

V1 cycles start every 6 hours at 00:30, 06:30, 12:30, and 18:30 IST. ETL-3
uses the latest cycle that started in the current or previous slot. That
cycle must be succeeded, and its four entity rows must share its run_id.
The media DAG is ignored.

V2 is ready when master_etl.py is not running and LAST_RUN in the V2 env file
is on or after the current slot's date. LAST_RUN moves only after a full
successful master_etl.py run.

A success that started after that V1 marker does not run again for the same
marker. Pass --check to print the decision without starting ETL-3.

Usage, from the repository root:
    python etl3/run_daily_when_sources_finished.py
    python etl3/run_daily_when_sources_finished.py --check
"""
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import dotenv_values

from etl3.config import settings
from etl3.db import connections
from etl3.source_readiness import IST, judge


def process_commands():
    result = subprocess.run(
        ["ps", "-eo", "args"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def latest_v1_cycle(conn, boundary):
    try:
        return _latest_v1_cycle(conn, boundary)
    except Exception as exc:
        # 42P01 undefined_table: schema migration has not created the cycle marker yet.
        if getattr(exc, "pgcode", None) != "42P01":
            raise
        conn.rollback()
        return None, []


def _latest_v1_cycle(conn, boundary):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT run_id::text, status, started_at, finished_at, cycle_start
            FROM cctns.cctns_v1_etl_cycle
            WHERE started_at >= %s
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (boundary,),
        )
        row = cur.fetchone()
    if row is None:
        return None, []
    cycle = {
        "run_id": row[0],
        "status": row[1],
        "started_at": row[2],
        "finished_at": row[3],
        "cycle_start": row[4],
    }
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT entity, status, started_at, finished_at, run_id::text
            FROM cctns.cctns_v1_etl_run_log
            WHERE run_id = %s
            """,
            (cycle["run_id"],),
        )
        entities = [
            {
                "entity": entity,
                "status": status,
                "started_at": started_at,
                "finished_at": finished_at,
                "run_id": run_id,
            }
            for entity, status, started_at, finished_at, run_id in cur.fetchall()
        ]
    return cycle, entities


def read_v2_last_run() -> str | None:
    values = dotenv_values(settings.V2_SOURCE_ENV_PATH)
    raw = values.get("LAST_RUN") if values else None
    return raw.strip() if isinstance(raw, str) and raw.strip() else None


def etl3_already_ran(conn, finished_at):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT run_id::text, started_at
            FROM consolidation_run_log
            WHERE status = 'success' AND started_at >= %s
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (finished_at,),
        )
        return cur.fetchone()


def collect_snapshot(now=None, commands=None):
    now = now if now is not None else datetime.now(IST)
    if commands is None:
        commands = process_commands()
    from etl3.source_readiness import cycle_boundary

    floor = cycle_boundary(now)
    v1 = connections.get_v1_source_connection()
    try:
        cycle, entities = latest_v1_cycle(v1, floor)
    finally:
        v1.close()

    prior_started = None
    if cycle and cycle.get("finished_at") is not None:
        unified = connections.get_unified_connection(readonly=True)
        try:
            prior = etl3_already_ran(unified, cycle["finished_at"])
        finally:
            unified.close()
        if prior:
            prior_started = prior[1]
    return {
        "now": now,
        "commands": commands,
        "cycle": cycle,
        "entities": entities,
        "v2_last_run": read_v2_last_run(),
        "etl3_prior_started_at": prior_started,
    }


def decide():
    snapshot = collect_snapshot()
    return judge(**snapshot)


def main():
    check_only = "--check" in sys.argv[1:]
    action, reason, start = decide()
    print(
        f"{datetime.now(IST).isoformat()} cycle_start={start.isoformat()} action={action} {reason}",
        flush=True,
    )
    if action != "run" or check_only:
        return 0
    root = Path(__file__).resolve().parent.parent
    completed = subprocess.run(
        [sys.executable, "-u", str(root / "etl3" / "run_phase5_incremental.py")],
        cwd=root,
    )
    return completed.returncode


if __name__ == "__main__":
    sys.exit(main())
