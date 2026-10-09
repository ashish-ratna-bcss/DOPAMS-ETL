"""Read-only snapshot of media_source / media_unified state."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.db.connections import get_unified_connection


def main() -> None:
    c = get_unified_connection()
    cur = c.cursor()
    out = {}
    cur.execute(
        """
        SELECT source_system, source_table,
               COUNT(*) AS rows,
               COUNT(DISTINCT source_record_id) AS distinct_ids
        FROM media_source
        GROUP BY 1, 2
        ORDER BY 1, 2
        """
    )
    out["media_source"] = [
        {"source_system": a, "source_table": b, "rows": c_, "distinct_ids": d}
        for a, b, c_, d in cur.fetchall()
    ]
    cur.execute("SELECT COUNT(*) FROM media_unified")
    out["media_unified_total"] = cur.fetchone()[0]
    cur.execute(
        """
        SELECT availability_status, COUNT(*)
        FROM media_unified
        GROUP BY 1
        ORDER BY 1
        """
    )
    out["availability"] = dict(cur.fetchall())
    cur.execute(
        """
        SELECT status, COUNT(*)
        FROM consolidation_run_log
        WHERE started_at > now() - interval '14 days'
        GROUP BY 1
        ORDER BY 1
        """
    )
    out["recent_runs"] = dict(cur.fetchall())
    cur.execute(
        """
        SELECT run_id, status, started_at, finished_at, error_message
        FROM consolidation_run_log
        ORDER BY started_at DESC
        LIMIT 5
        """
    )
    out["last_runs"] = [
        {
            "run_id": str(a),
            "status": b,
            "started_at": str(c_),
            "finished_at": str(d) if d else None,
            "error": e,
        }
        for a, b, c_, d, e in cur.fetchall()
    ]
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'consolidation_cursor'
        ORDER BY ordinal_position
        """
    )
    cursor_cols = [r[0] for r in cur.fetchall()]
    out["cursor_columns"] = cursor_cols
    cur.execute(
        """
        SELECT *
        FROM consolidation_cursor
        WHERE source_module ILIKE '%media%' OR source_module ILIKE '%file_media%'
        ORDER BY source_system, source_module
        """
    )
    cols = [d[0] for d in cur.description]
    out["cursors"] = []
    for row in cur.fetchall():
        item = dict(zip(cols, row))
        for col, val in list(item.items()):
            if val is not None and not isinstance(val, (str, int, float, bool)):
                item[col] = str(val)
        out["cursors"].append(item)
    print(json.dumps(out, indent=2, default=str))
    c.close()


if __name__ == "__main__":
    main()
