"""
Phase 5 incremental sync.

Reads V1 and V2. Writes only dopams_cctns.

Order, and the commit points:
  1. Copy source-declared gaps.
  2. Capture source runs whose ids are not yet on an observation.
  3. Catch up current primary keys that run-id discovery cannot see.
  4. Recompute current state from the full observation set (Phase 4).
  5. Advance cursors only after that recompute commits.
  6. Write reconciliation classifications.

A crash before step 4 leaves the new observations committed and the cursor
unmoved. The next run treats those observations as already captured and
still recomputes current state from them. A crash after the cursor move
is a finished run.

Usage: python etl3/run_phase5_incremental.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from etl3.db import connections
from etl3.loaders import v1_observations as v1obs
from etl3.loaders import v2_observations as v2obs
from etl3.run_phase4_consolidation import _consolidate, run_with_run_log
from etl3.sync.catalog import MODULES, registry_gap
from etl3.sync.catchup import catch_up_all
from etl3.sync.cursor import known_run_ids
from etl3.sync.gaps import copy_source_gaps
from etl3.sync.reconcile import reconcile


def _capture_new_runs(conn, consolidation_run_id: str) -> list:
    results = []
    for spec in MODULES:
        known = known_run_ids(conn, spec["source_system"], spec["source_table"], spec["obs_table"])
        if spec["source_system"] == "V1":
            result = v1obs.capture_incremental(conn, spec["module"], known, consolidation_run_id)
        else:
            result = v2obs.capture_incremental(conn, spec["module"], known, consolidation_run_id)
        conn.commit()
        results.append(result)
    return results


def _mark_cursors_running(conn):
    with conn.cursor() as cur:
        cur.execute("UPDATE consolidation_cursor SET status = 'running'")
    conn.commit()


def run_incremental(conn):
    missing, extra = registry_gap()
    if missing or extra:
        raise RuntimeError(f"module registry does not match the adapters: missing={missing} extra={extra}")

    def work(conn, run_id, progress):
        gaps = copy_source_gaps(conn)
        conn.commit()
        progress["gaps"] = gaps
        captured = _capture_new_runs(conn, run_id)
        progress["captured_inserted"] = sum(item.get("inserted", 0) for item in captured)
        caught = catch_up_all(conn, run_id)
        conn.commit()
        progress["catchup_inserted"] = sum(item["inserted"] for item in caught)
        _mark_cursors_running(conn)
        source_counts = {
            (item["source_system"], item["module"]): item["source_count"] for item in caught
        }
        sources, changed = _consolidate(conn, run_id, progress)
        report = reconcile(conn, source_counts)
        conn.commit()
        progress["reconciliation"] = [
            {"table": row["source_table"], "system": row["source_system"], "status": row["status"], "delta": row["delta"]}
            for row in report
        ]
        observed = progress["captured_inserted"] + progress["catchup_inserted"]
        return sources, observed

    return run_with_run_log(conn, work)


def main():
    conn = connections.get_unified_connection()
    try:
        run_id = run_incremental(conn)
        print(f"incremental run {run_id} marked success.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
