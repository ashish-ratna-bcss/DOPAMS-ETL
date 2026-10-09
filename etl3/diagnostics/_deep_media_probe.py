"""Deep probe: V1 local_path samples + find V2 files table/schema."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "etl3"))

from db.connections import get_v1_source_connection, get_v2_source_connection


def main():
    v1 = get_v1_source_connection()
    cur = v1.cursor()
    cur.execute(
        """
        SELECT status, left(local_path,120), file_size_bytes, COUNT(*)
        FROM cctns.cctns_media_files
        WHERE local_path IS NOT NULL
        GROUP BY 1,2,3
        ORDER BY 4 DESC
        LIMIT 15
        """
    )
    print("V1_PATH_SAMPLES")
    for r in cur.fetchall():
        print(r)
    cur.execute(
        """
        SELECT local_path FROM cctns.cctns_media_files
        WHERE status='DOWNLOADED' AND local_path IS NOT NULL
        LIMIT 5
        """
    )
    for (path,) in cur.fetchall():
        exists = os.path.isfile(path) if path else False
        size = os.path.getsize(path) if exists else None
        print("PATH_CHECK", path, "exists=", exists, "size=", size)
        # also try basename under common mounts
        for base in (
            "/mnt/shared-etl-files",
            "/data/cctns_v1_media",
            "/home/eagle/media",
            "/var/cctns/media",
        ):
            if path and path.startswith("/"):
                print("  base_exists", base, os.path.isdir(base))
    v1.close()

    v2 = get_v2_source_connection()
    cur = v2.cursor()
    cur.execute(
        """
        SELECT table_schema, table_name
        FROM information_schema.tables
        WHERE table_name ILIKE '%file%' OR table_name ILIKE '%media%'
        ORDER BY 1,2
        """
    )
    print("V2_FILEISH_TABLES")
    for r in cur.fetchall():
        print(r)
    cur.execute(
        """
        SELECT nspname FROM pg_namespace
        WHERE nspname NOT LIKE 'pg_%' AND nspname <> 'information_schema'
        ORDER BY 1
        """
    )
    print("V2_SCHEMAS", [r[0] for r in cur.fetchall()])
    # search columns download_error
    cur.execute(
        """
        SELECT table_schema, table_name, column_name
        FROM information_schema.columns
        WHERE column_name IN ('download_error','is_downloaded','file_id','source_field')
        ORDER BY 1,2,3
        """
    )
    print("V2_MEDIA_COLS")
    for r in cur.fetchall():
        print(r)
    v2.close()


if __name__ == "__main__":
    main()
