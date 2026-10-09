"""KB preservation + source RO safety checks for media consolidation review."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.db.connections import (
    get_unified_connection,
    get_v1_source_connection,
    get_v2_source_connection,
)
from etl3.media.paths import media_roots
import os


def main() -> None:
    out = {"roots": media_roots(), "root_exists": {}}
    for k, v in out["roots"].items():
        out["root_exists"][k] = os.path.isdir(v)

    tg = get_unified_connection()
    cur = tg.cursor()
    cur.execute(
        """
        SELECT n.nspname, c.relname
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind = 'r'
          AND (
            c.relname ILIKE '%kb%'
            OR n.nspname ILIKE '%kb%'
            OR c.relname ILIKE 'drug%'
            OR c.relname ILIKE 'geo%'
          )
        ORDER BY 1, 2
        """
    )
    tables = [(a, b) for a, b in cur.fetchall()]
    counts = {}
    for schema, table in tables:
        cur.execute(f'SELECT COUNT(*) FROM "{schema}"."{table}"')
        counts[f"{schema}.{table}"] = cur.fetchone()[0]
    out["kb_like_tables"] = counts

    cur.execute(
        """
        SELECT source_system, availability_status, COUNT(*)
        FROM media_unified GROUP BY 1,2 ORDER BY 1,2
        """
    )
    out["by_source_availability"] = [
        {"source_system": a, "availability_status": b, "count": c}
        for a, b, c in cur.fetchall()
    ]
    cur.execute("SELECT COUNT(*) FROM media_unified WHERE media_id LIKE 'V1:%%'")
    out["v1_unified"] = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM media_unified WHERE media_id LIKE 'V2:%%'")
    out["v2_unified"] = cur.fetchone()[0]

    # Source RO write rejection
    for label, factory in (
        ("V1", get_v1_source_connection),
        ("V2", get_v2_source_connection),
    ):
        try:
            c = factory()
            cur2 = c.cursor()
            try:
                if label == "V1":
                    cur2.execute(
                        "UPDATE cctns.cctns_media_files SET status=status WHERE false"
                    )
                else:
                    cur2.execute(
                        "UPDATE file_media_bookkeeping SET is_downloaded=is_downloaded WHERE false"
                    )
                c.commit()
                out[f"{label}_write_test"] = "UNEXPECTED_WRITE_ALLOWED"
            except Exception as exc:
                c.rollback()
                out[f"{label}_write_test"] = f"rejected:{type(exc).__name__}:{exc}"
            c.close()
        except Exception as exc:
            out[f"{label}_write_test"] = f"connect_error:{exc}"

    print(json.dumps(out, indent=2, default=str))
    tg.close()


if __name__ == "__main__":
    main()
