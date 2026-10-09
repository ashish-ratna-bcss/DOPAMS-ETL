"""Read-only probe of V1/V2 media schemas and unified tables."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "etl3"))

from db.connections import (
    get_unified_connection,
    get_v1_source_connection,
    get_v2_source_connection,
)

OUT = Path(__file__).resolve().parent / "reports" / "media_schema_probe.json"


def cols(conn, schema, table):
    cur = conn.cursor()
    cur.execute(
        """
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_schema=%s AND table_name=%s
        ORDER BY ordinal_position
        """,
        (schema, table),
    )
    return [{"name": a, "type": b, "nullable": c} for a, b, c in cur.fetchall()]


def main():
    out = {}
    v1 = get_v1_source_connection()
    out["v1_media_cols"] = cols(v1, "cctns", "cctns_media_files")
    cur = v1.cursor()
    cur.execute(
        "SELECT status, COUNT(1) FROM cctns.cctns_media_files GROUP BY 1 ORDER BY 1"
    )
    out["v1_status"] = cur.fetchall()
    cur.execute(
        """
        SELECT local_path, attach_path, dms_file_name, entity_type, fir_reg_num, status
        FROM cctns.cctns_media_files
        WHERE local_path IS NOT NULL
        ORDER BY media_id LIMIT 5
        """
    )
    out["v1_path_samples"] = [
        dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()
    ]
    cur.execute(
        """
        SELECT conname, pg_get_constraintdef(oid)
        FROM pg_constraint WHERE conrelid='cctns.cctns_media_files'::regclass
        """
    )
    out["v1_constraints"] = cur.fetchall()
    v1.close()

    v2 = get_v2_source_connection()
    out["v2_media_cols"] = cols(v2, "public", "file_media_bookkeeping")
    cur = v2.cursor()
    cur.execute(
        """
        SELECT source_type, source_field, COUNT(1)
        FROM file_media_bookkeeping GROUP BY 1,2 ORDER BY 1,2
        """
    )
    out["v2_by_type"] = cur.fetchall()
    colnames = [c["name"] for c in out["v2_media_cols"]]
    want = [
        c
        for c in (
            "id",
            "source_type",
            "source_field",
            "parent_id",
            "file_id",
            "file_path",
            "is_downloaded",
            "is_empty",
            "has_field",
            "download_error",
            "downloaded_at",
            "file_size",
            "file_size_bytes",
            "content_type",
            "mime_type",
            "created_at",
            "updated_at",
            "download_attempts",
            "etl_run_id",
            "fetched_at",
        )
        if c in colnames
    ]
    cur.execute(
        f"SELECT {', '.join(want)} FROM file_media_bookkeeping "
        f"WHERE is_downloaded IS TRUE ORDER BY id LIMIT 5"
    )
    out["v2_downloaded_samples"] = [
        dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()
    ]
    cur.execute(
        f"SELECT {', '.join(want)} FROM file_media_bookkeeping "
        f"WHERE file_id IS NULL LIMIT 3"
    )
    out["v2_null_file_id_samples"] = [
        dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()
    ]
    out["v2_selected_cols"] = want
    cur.execute(
        """
        SELECT conname, pg_get_constraintdef(oid)
        FROM pg_constraint WHERE conrelid='public.file_media_bookkeeping'::regclass
        """
    )
    out["v2_constraints"] = cur.fetchall()
    v2.close()

    tg = get_unified_connection(readonly=True)
    cur = tg.cursor()
    cur.execute(
        """
        SELECT table_name FROM information_schema.tables
        WHERE table_schema='public' AND table_name ILIKE '%media%' ORDER BY 1
        """
    )
    out["unified_media_tables"] = [r[0] for r in cur.fetchall()]
    cur.execute(
        """
        SELECT table_name FROM information_schema.tables
        WHERE table_schema='public'
          AND (table_name LIKE '%_source' OR table_name LIKE '%_unified')
        ORDER BY 1
        """
    )
    out["unified_tables"] = [r[0] for r in cur.fetchall()]
    # sample crimes_source shape
    out["crimes_source_cols"] = cols(tg, "public", "crimes_source")
    out["crimes_unified_cols"] = cols(tg, "public", "crimes_unified")
    tg.close()

    OUT.write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps({
        "report": str(OUT),
        "v1_cols": [c["name"] for c in out["v1_media_cols"]],
        "v2_cols": [c["name"] for c in out["v2_media_cols"]],
        "unified_media_tables": out["unified_media_tables"],
        "v1_status": out["v1_status"],
        "v2_types": len(out["v2_by_type"]),
    }, indent=2, default=str))


if __name__ == "__main__":
    main()
