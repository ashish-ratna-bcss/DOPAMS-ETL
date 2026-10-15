"""Controlled daily ETL-3 orchestration for the cctns-ai checkout.

Order:
  1. (optional) V1 ETL
  2. (optional) V2 ETL
  3. Source readiness gate (required unless --force)
  4. ETL-3 incremental consolidation (phase5)
  5. Enrichment (deterministic + AI per settings)
  6. Concise run summary

Does NOT enable Airflow/cron. Does NOT touch /home/eagle/dopams-cctns.
Overlapping runs are refused via the existing consolidation advisory lock.

Usage (repo root):
    python etl3/run_daily_pipeline.py --check
    python etl3/run_daily_pipeline.py --etl3-only
    python etl3/run_daily_pipeline.py --with-sources   # explicit; still not a schedule
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from etl3.config import settings
from etl3.db import connections
from etl3.enrichment.runner import run_enrichment
from etl3.run_ai_backfill import _eligible_accused_pending, _eligible_drug_pending
from etl3.run_daily_when_sources_finished import decide
from etl3.run_phase5_incremental import run_incremental
from etl3.sync.run_lock import ConcurrentRunError


ROOT = Path(__file__).resolve().parent.parent


def _run(cmd: list[str]) -> int:
    print(f"$ {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, cwd=str(ROOT)).returncode


def check_sources(force: bool = False) -> dict:
    action, reason, start = decide()
    print(
        f"source_gate action={action} reason={reason} cycle_start={start.isoformat()}",
        flush=True,
    )
    ready = action == "run" or force
    return {
        "ready": ready,
        "action": action,
        "reason": reason,
        "cycle_start": start.isoformat(),
        "forced": force,
    }


def run_etl3() -> dict:
    summary = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "target": settings.EXPECTED_UNIFIED_DBNAME,
        "incremental_run_id": None,
        "enrichment_run_id": None,
        "enrichment_stats": None,
        "status": "running",
    }
    if settings.EXPECTED_UNIFIED_DBNAME != "dopams_cctns_v2":
        raise SystemExit(f"Refusing target {settings.EXPECTED_UNIFIED_DBNAME!r}")

    conn = connections.get_unified_connection()
    try:
        try:
            inc_id = run_incremental(conn)
        except ConcurrentRunError as exc:
            summary["status"] = "blocked_concurrent"
            summary["error"] = str(exc)
            return summary
        summary["incremental_run_id"] = inc_id
        enrich_id = str(uuid.uuid4())
        stats = run_enrichment(conn, enrich_id)
        summary["enrichment_run_id"] = enrich_id
        summary["enrichment_stats"] = stats
        summary["status"] = "success"
    except Exception as exc:
        conn.rollback()
        summary["status"] = "failed"
        summary["error"] = str(exc)
        raise
    finally:
        conn.close()
        summary["finished_at"] = datetime.now(timezone.utc).isoformat()
    return summary


def check_ai_backfill_status(conn) -> dict:
    """Reuses run_ai_backfill.py's own pending-count queries -- no parallel
    implementation of backlog counting. 'Complete' means zero pending items
    of either kind, the same completion criterion run_ai_backfill.py itself
    reports, not merely that some process has exited."""
    drug_eligible, drug_settled, drug_pending = _eligible_drug_pending(conn)
    accused_eligible, accused_settled, accused_pending = _eligible_accused_pending(conn)
    return {
        "complete": drug_pending == 0 and accused_pending == 0,
        "drug_eligible": drug_eligible,
        "drug_settled": drug_settled,
        "drug_pending": drug_pending,
        "accused_eligible": accused_eligible,
        "accused_settled": accused_settled,
        "accused_pending": accused_pending,
    }


def check_no_conflicting_process(conn) -> dict:
    """Any OTHER live connection already running ETL-3 work (deterministic
    or AI) against this database, identified by the application_name every
    etl3.db.connections.get_unified_connection() caller shares. Excludes
    this check's own connection."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT pid, client_addr, state, query_start
            FROM pg_stat_activity
            WHERE application_name = 'etl3_unified' AND pid != pg_backend_pid()
            """
        )
        others = cur.fetchall()
    return {"clear": not others, "other_connections": others}


def check_migration_state(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('_migrations')")
        if cur.fetchone()[0] is None:
            return {"tracked": False, "applied": []}
        cur.execute("SELECT filename FROM _migrations ORDER BY filename")
        return {"tracked": True, "applied": [row[0] for row in cur.fetchall()]}


def check_activation_gate_using(conn) -> dict:
    """Same aggregation as check_activation_gate(), over an already-open
    connection -- the testable half, so tests can point it at a disposable
    database instead of the real one."""
    ai = check_ai_backfill_status(conn)
    conflict = check_no_conflicting_process(conn)
    migrations = check_migration_state(conn)

    blockers = []
    if not ai["complete"]:
        blockers.append(
            f"AI backfill incomplete: drug_pending={ai['drug_pending']}, "
            f"accused_pending={ai['accused_pending']}"
        )
    if not conflict["clear"]:
        blockers.append(
            f"conflicting etl3_unified connection(s) active: {conflict['other_connections']}"
        )

    return {
        "ready": not blockers,
        "blockers": blockers,
        "ai_backfill": ai,
        "conflicting_process": conflict,
        "migrations": migrations,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def check_activation_gate() -> dict:
    """Aggregate GO/NO-GO for enabling the daily schedule, against the
    real configured database.

    Read-only: does not start, stop, pause, or modify anything -- only
    reports. A human (or a systemd ExecStartPre, once the schedule is
    deliberately enabled) decides what to do with the result; this
    function never flips the schedule on itself.
    """
    conn = connections.get_unified_connection(readonly=True)
    try:
        return check_activation_gate_using(conn)
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Daily ETL-3 pipeline (not a scheduler)")
    parser.add_argument("--check", action="store_true", help="Source readiness only")
    parser.add_argument(
        "--gate-check", action="store_true",
        help="Report daily-schedule activation readiness (read-only; starts/stops nothing)",
    )
    parser.add_argument("--etl3-only", action="store_true", help="Skip V1/V2; run ETL-3 after gate")
    parser.add_argument("--with-sources", action="store_true", help="Explicitly run V1 then V2 then ETL-3")
    parser.add_argument("--force", action="store_true", help="Skip source-readiness gate")
    args = parser.parse_args()

    print("checkout=cctns-ai target=dopams_cctns_v2")
    print("schedule_status=NOT_ENABLED (manual/controlled entrypoint only)")

    if args.gate_check:
        result = check_activation_gate()
        print(json.dumps(result, indent=2, default=str))
        if result["ready"]:
            print("[GATE] READY - all automated activation conditions are currently satisfied.")
            print("[GATE] Enabling the schedule is still a separate, explicit, human action.")
        else:
            print("[GATE] NOT READY - do not enable the schedule:")
            for b in result["blockers"]:
                print(f"  - {b}")
        raise SystemExit(0 if result["ready"] else 1)

    if args.check:
        result = check_sources(force=False)
        print(json.dumps({"ready": result["ready"], "action": result["action"], "reason": result["reason"]}, indent=2))
        raise SystemExit(0 if result["ready"] else 1)

    if args.with_sources:
        rc = _run([sys.executable, "-u", "etl3/run_v1_etl.py", "--run-cycle"])
        if rc != 0:
            raise SystemExit(f"V1 ETL failed rc={rc}; refusing ETL-3")
        rc = _run([sys.executable, "-u", "etl3/run_v2_etl.py", "--run"])
        if rc != 0:
            raise SystemExit(f"V2 ETL failed rc={rc}; refusing ETL-3")

    if not args.with_sources and not args.etl3_only and not args.force:
        parser.print_help()
        print("\nChoose --check, --etl3-only, or --with-sources.")
        raise SystemExit(2)

    gate = check_sources(force=args.force)
    if not gate["ready"]:
        raise SystemExit("Source readiness gate failed; ETL-3 not started")

    summary = run_etl3()
    print(json.dumps(summary, indent=2, default=str))
    if summary["status"] != "success":
        raise SystemExit(1)
    print("[OK] daily pipeline ETL-3 pass finished")


if __name__ == "__main__":
    main()
