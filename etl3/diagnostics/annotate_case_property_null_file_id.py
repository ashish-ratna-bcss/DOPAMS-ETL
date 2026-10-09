"""Annotate case_property MEDIA rows that cannot be downloaded (null file_id).

Does not mark is_downloaded=true. Does not invent files.
Writes an explicit SOURCE download_error for accounting.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "etl3"))

# Use V2 write connection via psycopg2 + V2 env (bookkeeping update only).
from dotenv import dotenv_values
import psycopg2

MSG = "SOURCE: MEDIA present but file_id is null (cannot download)"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    env = dotenv_values(ROOT / "cctns-v2" / ".env")
    conn = psycopg2.connect(
        host=env["POSTGRES_HOST"],
        port=env["POSTGRES_PORT"],
        dbname=env["POSTGRES_DB"],
        user=env["POSTGRES_USER"],
        password=env["POSTGRES_PASSWORD"],
    )
    cur = conn.cursor()
    cur.execute("SELECT current_database()")
    assert cur.fetchone()[0] == "cctns-v2"
    cur.execute(
        """
        SELECT COUNT(*) FROM public.file_media_bookkeeping
        WHERE source_type='case_property' AND source_field='MEDIA'
          AND file_id IS NULL
          AND is_downloaded IS NOT TRUE
          AND COALESCE(is_empty,false) IS NOT TRUE
        """
    )
    n = cur.fetchone()[0]
    print(json.dumps({"candidates": n, "execute": args.execute}))
    if not args.execute:
        conn.close()
        return
    cur.execute(
        """
        UPDATE public.file_media_bookkeeping
        SET download_error = %s
        WHERE source_type='case_property' AND source_field='MEDIA'
          AND file_id IS NULL
          AND is_downloaded IS NOT TRUE
          AND COALESCE(is_empty,false) IS NOT TRUE
          AND (download_error IS NULL OR btrim(download_error)='')
        """,
        (MSG,),
    )
    print(json.dumps({"updated": cur.rowcount}))
    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
