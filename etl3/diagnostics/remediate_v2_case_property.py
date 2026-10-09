"""Download pending case_property MEDIA rows via existing FilesMediaServerETL helpers.

Only processes source_type=case_property, source_field=MEDIA, not downloaded,
not empty, with file_id, excluding PERMANENT errors.
Bounded by --limit. Does not start AI backfill. Does not touch dopams_cctns_v2.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

V2 = Path(__file__).resolve().parents[2] / "cctns-v2"
MEDIA_PKG = V2 / "etl-files" / "etl_files_media_server"
sys.path.insert(0, str(V2))
sys.path.insert(0, str(MEDIA_PKG))
sys.path.insert(0, str(MEDIA_PKG / "etl_files_media_server"))

# Ensure FILES_TABLE points at bookkeeping table used in this environment
os.environ.setdefault("FILES_TABLE", "file_media_bookkeeping")
os.environ.setdefault("FILES_MEDIA_BASE_PATH", "/mnt/shared-etl-files")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from config import DB_CONFIG
    import psycopg2
    from etl_files_media_server.main import FilesMediaServerETL

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, source_type, source_field, parent_id, file_id,
               has_field, is_empty, is_downloaded, download_attempts, download_error
        FROM public.file_media_bookkeeping
        WHERE source_type = 'case_property'
          AND source_field = 'MEDIA'
          AND (is_downloaded IS NOT TRUE)
          AND COALESCE(is_empty, false) IS NOT TRUE
          AND file_id IS NOT NULL
          AND (download_error IS NULL OR download_error NOT LIKE 'PERMANENT:%%')
        ORDER BY COALESCE(download_attempts,0) ASC, id ASC
        LIMIT %s
        """,
        (args.limit,),
    )
    rows = cur.fetchall()
    colnames = [d[0] for d in cur.description]
    print(json.dumps({"candidates": len(rows), "dry_run": args.dry_run}, indent=2))
    if args.dry_run or not rows:
        for r in rows[:10]:
            print(dict(zip(colnames, r)))
        conn.close()
        return

    etl = FilesMediaServerETL(repair=False)
    # reuse connection bootstrap
    etl.connect_db()
    stats = {"ok": 0, "fail": 0, "skip": 0}
    for r in rows:
        row = dict(zip(colnames, r))
        try:
            ok = etl.download_single_file(
                str(row["file_id"]),
                row["source_type"],
                row["source_field"],
            )
            if ok:
                stats["ok"] += 1
            else:
                stats["fail"] += 1
            print(f"id={row['id']} file_id={row['file_id']} ok={ok}", flush=True)
        except Exception as e:
            stats["fail"] += 1
            print(f"id={row['id']} ERR {e}", flush=True)
        # respect rate limit (~5 RPM)
        time.sleep(12)
    etl.close_db()
    conn.close()
    print(json.dumps({"stats": stats}, indent=2))


if __name__ == "__main__":
    main()
