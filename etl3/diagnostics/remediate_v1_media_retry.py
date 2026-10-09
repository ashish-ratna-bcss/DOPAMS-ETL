"""Bounded retry of recoverable V1 media failures via existing sync_media path.

Safety:
  - Does not modify FIR/court business rows.
  - Updates only cctns.cctns_media_files bookkeeping via sync_media.update_media_status.
  - Skips statuses that already represent empty/unavailable DMS content (NOT_FOUND
    with 0-byte Alfresco responses) unless --include-not-found is set.
  - Default limit is small; use --limit to bound work.
  - Does not touch dopams_cctns_v2 or start AI backfill.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

V1_ROOT = Path(__file__).resolve().parents[2] / "cctns-v1" / "CCTNSV1_DAILY_ETL_RUN"
sys.path.insert(0, str(V1_ROOT))

from db.connection import get_connection  # noqa: E402
from sync_media import MediaItem, _process_single_item, update_media_status  # noqa: E402
from config import settings  # noqa: E402


def select_retry_candidates(conn, limit: int, include_not_found: bool):
    statuses = ["FAILED", "PENDING"]
    if include_not_found:
        statuses.append("NOT_FOUND")
    sql = """
        SELECT media_id, entity_type, fir_reg_num, attach_path, dms_file_name, status, error_message
        FROM cctns.cctns_media_files
        WHERE status = ANY(%s)
          AND (
            status = 'PENDING'
            OR error_message ILIKE '%%timeout%%'
            OR error_message ILIKE '%%timed out%%'
            OR error_message ILIKE '%%connection%%'
            OR error_message ILIKE '%%502%%'
            OR error_message ILIKE '%%503%%'
            OR error_message ILIKE '%%504%%'
            OR error_message ILIKE '%%429%%'
            OR (status = 'FAILED' AND (error_message IS NULL OR error_message NOT ILIKE '%%0 bytes%%'))
          )
        ORDER BY media_id
        LIMIT %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (statuses, limit))
        return cur.fetchall()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--include-not-found", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conn = get_connection()
    try:
        rows = select_retry_candidates(conn, args.limit, args.include_not_found)
        print(json.dumps({"candidates": len(rows), "dry_run": args.dry_run}, indent=2))
        if args.dry_run or not rows:
            for r in rows[:20]:
                print("CANDIDATE", r[0], r[1], r[5], (r[6] or "")[:80])
            return
        base_dir = settings.MEDIA_BASE_DIR
        stats = {"ok": 0, "failed": 0, "not_found": 0, "cached": 0}
        for media_id, entity_type, fir_reg_num, attach_path, dms_file_name, _st, _err in rows:
            item = MediaItem(media_id, entity_type, fir_reg_num, attach_path, dms_file_name)
            payload = _process_single_item(item, base_dir)
            result = payload["result"]
            update_media_status(conn, item, result)
            status = result.get("status")
            if status == "DOWNLOADED":
                stats["ok"] += 1
            elif status == "CACHED":
                stats["cached"] += 1
            elif status == "NOT_FOUND":
                stats["not_found"] += 1
            else:
                stats["failed"] += 1
            print(f"media_id={media_id} -> {status} size={result.get('file_size')} err={result.get('error_message')}")
        print(json.dumps({"stats": stats}, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
