"""Bounded retry of recoverable V2 files-table download failures.

Uses the existing media server download helpers when importable; otherwise
reports candidates only (--dry-run default recommended first).

Does not invent files. Does not modify crime/person business rows.
Does not touch dopams_cctns_v2. Does not start AI backfill.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

V2_ROOT = Path(__file__).resolve().parents[2] / "cctns-v2"
MEDIA_ROOT = V2_ROOT / "etl-files" / "etl_files_media_server"
sys.path.insert(0, str(MEDIA_ROOT))
sys.path.insert(0, str(V2_ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--execute", action="store_true", help="Actually retry downloads")
    args = parser.parse_args()
    dry_run = not args.execute

    try:
        from config import DB_CONFIG  # media server config
        import psycopg2
    except Exception as e:
        print(json.dumps({"error": f"cannot import V2 media config: {e}"}))
        raise SystemExit(1)

    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database()")
            db = cur.fetchone()[0]
            cur.execute(
                """
                SELECT id, source_type, source_field, parent_id, file_id,
                       download_error, download_attempts, is_downloaded, is_empty
                FROM public.files
                WHERE (is_downloaded IS NOT TRUE)
                  AND COALESCE(is_empty, false) IS NOT TRUE
                  AND file_id IS NOT NULL
                  AND (
                    download_error IS NULL
                    OR download_error ILIKE %s
                    OR download_error ILIKE %s
                    OR download_error ILIKE %s
                    OR download_error ILIKE %s
                    OR download_error ILIKE %s
                    OR download_error ILIKE %s
                  )
                ORDER BY COALESCE(download_attempts,0) ASC, created_at ASC NULLS LAST
                LIMIT %s
                """,
                ("%timeout%", "%502%", "%503%", "%504%", "%429%", "%connection%", args.limit),
            )
            rows = cur.fetchall()
        print(json.dumps({
            "database": db,
            "candidates": len(rows),
            "dry_run": dry_run,
            "note": "HTTP 400 candidates excluded from auto-retry (need per-file source verification)",
            "sample": [
                {
                    "id": r[0], "source_type": r[1], "source_field": r[2],
                    "file_id": r[4], "error": (r[5] or "")[:100], "attempts": r[6],
                }
                for r in rows[:15]
            ],
        }, indent=2, default=str))

        if dry_run:
            # Separate count of HTTP 400 for reporting
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT source_type, source_field, COUNT(*)
                    FROM public.files
                    WHERE download_error ILIKE '%400%'
                    GROUP BY 1,2 ORDER BY 3 DESC
                    """
                )
                print("HTTP_400_BY_TYPE", cur.fetchall())
            return

        # Execute path: call media server main/downloader if available
        try:
            from etl_files_media_server.main import download_one  # type: ignore
        except Exception:
            try:
                # fallback: leave queued; operator runs media server
                print(json.dumps({
                    "executed": False,
                    "reason": "download_one helper not importable; run media server for queued retries",
                    "queued_candidates": len(rows),
                }))
                return
            except Exception:
                raise

        stats = {"ok": 0, "fail": 0}
        for r in rows:
            try:
                download_one(r)  # may vary by signature
                stats["ok"] += 1
            except Exception as e:
                stats["fail"] += 1
                print("FAIL", r[0], e)
        print(json.dumps({"stats": stats}))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
