"""Inspect why case_property MEDIA has null file_id — shapes from bookkeeping + source."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "etl3"))

from db.connections import get_v2_source_connection

OUT = Path(__file__).resolve().parent / "reports" / "case_property_null_file_id.json"


def main():
    v2 = get_v2_source_connection()
    cur = v2.cursor()
    cur.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema='public' AND table_name='file_media_bookkeeping'
        ORDER BY ordinal_position
        """
    )
    cols = [r[0] for r in cur.fetchall()]
    cur.execute(
        """
        SELECT *
        FROM public.file_media_bookkeeping
        WHERE source_type='case_property' AND source_field='MEDIA'
          AND file_id IS NULL
          AND download_error LIKE 'SOURCE: MEDIA present but file_id is null%%'
        LIMIT 5
        """
    )
    samples = [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]
    cur.execute(
        """
        SELECT COUNT(*) FILTER (WHERE file_id IS NULL) null_fid,
               COUNT(*) FILTER (WHERE file_id IS NOT NULL) has_fid,
               COUNT(*) FILTER (WHERE is_downloaded IS TRUE) downloaded,
               COUNT(*) FILTER (WHERE download_error LIKE 'SOURCE:%%') source_annot,
               COUNT(*) total
        FROM public.file_media_bookkeeping
        WHERE source_type='case_property' AND source_field='MEDIA'
        """
    )
    summary = dict(zip(["null_fid", "has_fid", "downloaded", "source_annot", "total"], cur.fetchone()))

    # identity disk missing counts
    cur.execute(
        """
        SELECT
          COUNT(*) FILTER (WHERE download_error LIKE 'PERMANENT:%%') permanent,
          COUNT(*) FILTER (WHERE download_error ILIKE '%%missing on disk%%') disk_missing,
          COUNT(*) FILTER (WHERE download_error ILIKE '%%400%%') http400
        FROM public.file_media_bookkeeping
        WHERE source_type='person' AND source_field='IDENTITY_DETAILS'
        """
    )
    identity = dict(zip(["permanent", "disk_missing", "http400"], cur.fetchone()))

    # check case_property / fsl_case_property for media-like columns
    for table in ("case_property", "fsl_case_property", "properties"):
        cur.execute(
            """
            SELECT column_name, data_type FROM information_schema.columns
            WHERE table_schema='public' AND table_name=%s
              AND (column_name ILIKE '%%media%%' OR column_name ILIKE '%%file%%')
            """,
            (table,),
        )
        summary.setdefault("media_cols", {})[table] = cur.fetchall()

    # if fsl has media, sample against bookkeeping parent_ids
    cur.execute(
        """
        SELECT parent_id FROM public.file_media_bookkeeping
        WHERE source_type='case_property' AND source_field='MEDIA' AND file_id IS NULL
        LIMIT 20
        """
    )
    pids = [r[0] for r in cur.fetchall()]
    lookups = []
    for table in ("case_property", "fsl_case_property"):
        cur.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema='public' AND table_name=%s
            """,
            (table,),
        )
        tcols = [r[0] for r in cur.fetchall()]
        id_col = next((c for c in ("case_property_id", "id", "_id") if c in tcols), None)
        if not id_col:
            continue
        media_cols = [c for c in tcols if "media" in c.lower() or c.lower() in ("files", "file", "attachments")]
        for pid in pids[:5]:
            if media_cols:
                sel = ", ".join([id_col] + media_cols)
                cur.execute(f"SELECT {sel} FROM {table} WHERE {id_col}::text=%s", (str(pid),))
                row = cur.fetchone()
                lookups.append({"table": table, "parent_id": pid, "row": dict(zip([id_col] + media_cols, row)) if row else None})
            else:
                cur.execute(f"SELECT {id_col} FROM {table} WHERE {id_col}::text=%s", (str(pid),))
                row = cur.fetchone()
                lookups.append({"table": table, "parent_id": pid, "exists": row is not None, "media_cols": []})

    out = {
        "bookkeeping_cols": cols,
        "summary": summary,
        "samples": samples,
        "identity": identity,
        "lookups": lookups,
    }
    OUT.write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps({"report": str(OUT), "summary": summary, "identity": identity, "lookups": lookups[:6]}, indent=2, default=str))
    v2.close()


if __name__ == "__main__":
    main()
