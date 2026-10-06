"""Run one ETL-3 incremental pass after the V1 and V2 cycles for the same slot.

V1 and V2 each start on the fixed clock 00:00, 06:00, 12:00, and 18:00 IST.
ETL-3 runs slot N only when that slot's V1 cycle and that slot's V2 cycle
have both succeeded. The checker starts the pass on its next look; there is
no extra delay. A success from another slot is not reused. Media is ignored.

V2 success for a slot is that slot's master.log LAST_RUN persist line.
LAST_RUN in the V2 env file must also be on or after the slot date. Neither
signal is written by a failed or partial V2 run.

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
from etl3.source_readiness import IST, discover_v2_runs, judge


def process_commands():
    result = subprocess.run(
        ["ps", "-eo", "args"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def recent_v1_cycles(conn, floor):
    try:
        return _recent_v1_cycles(conn, floor)
    except Exception as exc:
        # 42P01 undefined_table: schema migration has not created the cycle marker yet.
        if getattr(exc, "pgcode", None) != "42P01":
            raise
        conn.rollback()
        return [], {}


def _recent_v1_cycles(conn, floor):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT run_id::text, status, started_at, finished_at, cycle_start
            FROM cctns.cctns_v1_etl_cycle
            WHERE started_at >= %s
            ORDER BY started_at DESC
            """,
            (floor,),
        )
        cycles = [
            {
                "run_id": run_id,
                "status": status,
                "started_at": started_at,
                "finished_at": finished_at,
                "cycle_start": cycle_start,
            }
            for run_id, status, started_at, finished_at, cycle_start in cur.fetchall()
        ]
    if not cycles:
        return [], {}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT entity, status, started_at, finished_at, run_id::text
            FROM cctns.cctns_v1_etl_run_log
            WHERE run_id::text = ANY(%s)
            """,
            ([cycle["run_id"] for cycle in cycles],),
        )
        by_run: dict[str, list] = {}
        for entity, status, started_at, finished_at, run_id in cur.fetchall():
            by_run.setdefault(run_id, []).append(
                {
                    "entity": entity,
                    "status": status,
                    "started_at": started_at,
                    "finished_at": finished_at,
                    "run_id": run_id,
                }
            )
    return cycles, by_run


def read_v2_last_run() -> str | None:
    values = dotenv_values(settings.V2_SOURCE_ENV_PATH)
    raw = values.get("LAST_RUN") if values else None
    return raw.strip() if isinstance(raw, str) and raw.strip() else None


def etl3_successes_since(conn, floor):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT started_at
            FROM consolidation_run_log
            WHERE status = 'success' AND started_at >= %s
            ORDER BY started_at DESC
            """,
            (floor,),
        )
        return [row[0] for row in cur.fetchall()]


def v2_log_roots() -> list[Path]:
    roots = [Path(settings.V2_SOURCE_ENV_PATH).resolve().parent / "etl_master" / "logs"]
    roots.append(Path("/logs"))
    return roots


def collect_snapshot(now=None, commands=None):
    now = now if now is not None else datetime.now(IST)
    if commands is None:
        commands = process_commands()
    from etl3.source_readiness import cycle_boundary

    floor = cycle_boundary(now)
    v1 = connections.get_v1_source_connection()
    try:
        cycles, entities_by_run_id = recent_v1_cycles(v1, floor)
    finally:
        v1.close()

    unified = connections.get_unified_connection(readonly=True)
    try:
        successes = etl3_successes_since(unified, floor)
    finally:
        unified.close()
    return {
        "now": now,
        "commands": commands,
        "cycles": cycles,
        "entities_by_run_id": entities_by_run_id,
        "v2_runs": discover_v2_runs(v2_log_roots()),
        "v2_last_run": read_v2_last_run(),
        "etl3_successes": successes,
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
