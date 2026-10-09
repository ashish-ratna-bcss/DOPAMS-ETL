"""Capture + consolidate V1/V2 media metadata into dopams_cctns_v2.

Does not:
  - start AI backfill or daily scheduling
  - re-download media files
  - write to V1/V2
  - reset the database

Usage:
  python etl3/run_media_consolidation.py
  python etl3/run_media_consolidation.py --dry-run-counts
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from etl3.db import connections
from etl3.loaders.common import start_consolidation_run, finish_consolidation_run
from etl3.merger.media_consolidate import run_media_consolidation
from etl3.sync.catchup import catch_up_module
from etl3.sync.catalog import MODULES
from etl3.sync.run_lock import ConcurrentRunError, acquire, release


MEDIA_SPECS = [
    spec
    for spec in MODULES
    if spec["module"] in ("media", "file_media_bookkeeping")
]


def _capture_media(conn, run_id: str, batch_size: int = 1000) -> dict:
    """Prefer catch-up for media.

    V2 ``capture_incremental`` resolves each changed id with a single-row
    ``get_source_record`` call. For ~160k file_media_bookkeeping rows that is
    unusable. Catch-up uses ``fetch_rows_by_pk`` batches instead. V1 media has
    no etl_run_log entity, so incremental discovery is empty anyway.
    """
    results = {"captured": [], "catchup": [], "note": "media uses catch-up batches only"}
    for spec in MEDIA_SPECS:
        print(
            f"catchup start {spec['source_system']}/{spec['module']}",
            flush=True,
        )
        caught = catch_up_module(
            conn,
            spec,
            run_id,
            batch_size=batch_size,
            commit_every_batches=5,
        )
        conn.commit()
        print(
            f"catchup done {spec['source_system']}/{spec['module']}: "
            f"inserted={caught.get('inserted')} missing={caught.get('missing')} "
            f"source_count={caught.get('source_count')}",
            flush=True,
        )
        results["catchup"].append(caught)
    return results


def _report_counts(conn) -> dict:
    cur = conn.cursor()
    out = {}
    cur.execute(
        """
        SELECT source_system, source_table, COUNT(DISTINCT source_record_id)
        FROM media_source GROUP BY 1,2 ORDER BY 1,2
        """
    )
    out["observations"] = [
        {"source_system": a, "source_table": b, "distinct_ids": c} for a, b, c in cur.fetchall()
    ]
    cur.execute(
        """
        SELECT source_system, attachment_category, availability_status, COUNT(*)
        FROM media_unified
        GROUP BY 1,2,3 ORDER BY 1,2,3
        """
    )
    out["by_availability"] = [
        {
            "source_system": a,
            "attachment_category": b,
            "availability_status": c,
            "count": d,
        }
        for a, b, c, d in cur.fetchall()
    ]
    cur.execute("SELECT COUNT(*) FROM media_unified")
    out["unified_total"] = cur.fetchone()[0]
    cur.execute(
        """
        SELECT availability_status, COUNT(*) FROM media_unified GROUP BY 1 ORDER BY 1
        """
    )
    out["availability_totals"] = dict(cur.fetchall())
    cur.execute(
        """
        SELECT COUNT(*) FROM media_unified
        WHERE media_id LIKE 'V1:%%' AND media_id IN (
            SELECT media_id FROM media_unified WHERE media_id LIKE 'V2:%%'
        )
        """
    )
    # Collision check on unified pk is structural; also check raw source id overlap
    cur.execute(
        """
        SELECT COUNT(*) FROM (
          SELECT source_record_id FROM media_unified WHERE source_system='V1'
          INTERSECT
          SELECT source_record_id FROM media_unified WHERE source_system='V2'
        ) x
        """
    )
    out["raw_source_id_overlap_v1_v2"] = cur.fetchone()[0]
    cur.execute(
        """
        SELECT COUNT(*) FROM (
          SELECT media_id FROM media_unified GROUP BY 1 HAVING COUNT(*) > 1
        ) d
        """
    )
    out["duplicate_media_pk"] = cur.fetchone()[0]
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run-counts", action="store_true")
    parser.add_argument(
        "--consolidate-only",
        action="store_true",
        help="Skip observation catch-up; rebuild media_unified from existing media_source",
    )
    args = parser.parse_args()

    conn = connections.get_unified_connection()
    try:
        if args.dry_run_counts:
            print(json.dumps(_report_counts(conn), indent=2, default=str))
            return

        if not acquire(conn):
            raise ConcurrentRunError("another consolidation run holds the lock")
        run_id = None
        try:
            run_id = start_consolidation_run(conn)
            conn.commit()
            capture = {"catchup": [], "captured": [], "skipped": args.consolidate_only}
            if not args.consolidate_only:
                capture = _capture_media(conn, run_id)
            print("consolidating media_unified ...", flush=True)
            consolidated = run_media_consolidation(conn, run_id)
            conn.commit()
            print("consolidation flush done", flush=True)
            counts = _report_counts(conn)
            finish_consolidation_run(
                conn,
                run_id,
                status="success",
                sources_processed={"media": "media_only"},
                rows_observed=sum(c.get("inserted", 0) for c in capture.get("catchup", []))
                + sum(c.get("inserted", 0) for c in capture.get("captured", [])),
                rows_changed=consolidated.get("flush", {}).get("inserted", 0)
                + consolidated.get("flush", {}).get("updated", 0),
            )
            conn.commit()
            print(
                json.dumps(
                    {
                        "run_id": run_id,
                        "capture": capture,
                        "consolidated": consolidated,
                        "counts": counts,
                    },
                    indent=2,
                    default=str,
                )
            )
        except Exception:
            conn.rollback()
            if run_id:
                try:
                    finish_consolidation_run(
                        conn,
                        run_id,
                        status="failed",
                        sources_processed={"media": "media_only"},
                        rows_observed=0,
                        error_message="see logs",
                    )
                    conn.commit()
                except Exception:
                    conn.rollback()
            raise
        finally:
            release(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
