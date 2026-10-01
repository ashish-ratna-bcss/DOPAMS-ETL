"""
Phase 3 driver: runs capture_initial() for every V1 and V2 module, in one
consolidation run, committing after each module (so a crash loses at most
one module's progress, not the whole load -- and re-running is always safe,
since capture_initial is idempotent per module).

Usage: python etl3/run_phase3_initial_load.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from etl3.db import connections
from etl3.loaders import common, v1_observations as v1obs, v2_observations as v2obs
from etl3.sources.v1.adapter import V1Adapter
from etl3.sources.v2.adapter import V2Adapter

V1_MODULES = V1Adapter().supported_modules()
V2_MODULES = V2Adapter().supported_modules()


def main():
    conn = connections.get_unified_connection()
    run_id = common.start_consolidation_run(conn)
    conn.commit()
    print(f"consolidation_run_id = {run_id}\n")

    results = []
    total_inserted = 0
    for module in V1_MODULES:
        t0 = time.time()
        r = v1obs.capture_initial(conn, module, run_id)
        conn.commit()
        dt = time.time() - t0
        print(f"[V1] {module:20s} inserted={r['inserted']:6d} already_present={r['already_present']:6d}  ({dt:.1f}s)")
        results.append(("V1", module, r))
        total_inserted += r["inserted"]

    for module in V2_MODULES:
        t0 = time.time()
        r = v2obs.capture_initial(conn, module, run_id)
        conn.commit()
        dt = time.time() - t0
        extra = f" no_etl_run_id={r['no_etl_run_id']}" if r.get("no_etl_run_id") else ""
        print(f"[V2] {module:20s} inserted={r['inserted']:6d} already_present={r['already_present']:6d}{extra}  ({dt:.1f}s)")
        results.append(("V2", module, r))
        total_inserted += r["inserted"]

    common.finish_consolidation_run(
        conn, run_id, status="success",
        sources_processed={f"{s}:{m}": "initial" for s, m, _ in results},
        rows_observed=total_inserted,
    )
    conn.commit()
    conn.close()
    print(f"\nTotal rows inserted this run: {total_inserted}")
    print(f"consolidation_run_id {run_id} marked success.")


if __name__ == "__main__":
    main()
