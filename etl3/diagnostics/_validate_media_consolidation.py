"""Post-consolidation validation report for media metadata.

Read-only against dopams_cctns_v2 (+ optional RO source counts).
Does not write sources, download files, or touch KB tables.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.db.connections import (
    get_unified_connection,
    get_v1_source_connection,
    get_v2_source_connection,
)
from etl3.media.paths import media_roots


def _safe_count(cur, sql: str, params=None):
    cur.execute(sql, params or ())
    return cur.fetchone()[0]


def main() -> None:
    report: dict = {"roots": media_roots()}
    for key, root in report["roots"].items():
        report.setdefault("root_exists", {})[key] = os.path.isdir(root)

    tg = get_unified_connection()
    cur = tg.cursor()

    cur.execute(
        """
        SELECT source_system, source_table,
               COUNT(*) AS rows,
               COUNT(DISTINCT source_record_id) AS distinct_ids
        FROM media_source GROUP BY 1,2 ORDER BY 1,2
        """
    )
    report["observations"] = [
        {"source_system": a, "source_table": b, "rows": c, "distinct_ids": d}
        for a, b, c, d in cur.fetchall()
    ]

    cur.execute("SELECT COUNT(*) FROM media_unified")
    report["unified_total"] = cur.fetchone()[0]

    cur.execute(
        """
        SELECT source_system, COUNT(*) FROM media_unified GROUP BY 1 ORDER BY 1
        """
    )
    report["unified_by_source"] = dict(cur.fetchall())

    cur.execute(
        """
        SELECT availability_status, COUNT(*)
        FROM media_unified GROUP BY 1 ORDER BY 1
        """
    )
    report["availability_totals"] = dict(cur.fetchall())

    cur.execute(
        """
        SELECT source_system, attachment_category, availability_status, COUNT(*)
        FROM media_unified
        GROUP BY 1,2,3 ORDER BY 1,2,3
        """
    )
    report["by_category_availability"] = [
        {
            "source_system": a,
            "attachment_category": b,
            "availability_status": c,
            "count": d,
        }
        for a, b, c, d in cur.fetchall()
    ]

    cur.execute(
        """
        SELECT COUNT(*) FROM media_unified
        WHERE parent_entity_id IS NULL OR parent_entity_id = ''
        """
    )
    report["missing_parent_entity_id"] = cur.fetchone()[0]

    cur.execute(
        """
        SELECT COUNT(*) FROM source_gap_ledger
        WHERE gap_type = 'unresolved_media_parent' AND status = 'OPEN'
        """
    )
    report["open_parent_gaps"] = cur.fetchone()[0]

    cur.execute(
        """
        SELECT COUNT(*) FROM (
          SELECT source_record_id FROM media_unified WHERE source_system='V1'
          INTERSECT
          SELECT source_record_id FROM media_unified WHERE source_system='V2'
        ) x
        """
    )
    report["raw_source_id_overlap_v1_v2"] = cur.fetchone()[0]

    cur.execute(
        """
        SELECT COUNT(*) FROM (
          SELECT media_id FROM media_unified GROUP BY 1 HAVING COUNT(*) > 1
        ) d
        """
    )
    report["duplicate_media_pk"] = cur.fetchone()[0]

    # Path accessibility summaries (status-based; do not claim all copied paths usable)
    cur.execute(
        """
        SELECT COUNT(*) FROM media_unified
        WHERE availability_status = 'VERIFIED_ACCESSIBLE'
        """
    )
    report["paths_verified_accessible"] = cur.fetchone()[0]

    cur.execute(
        """
        SELECT COUNT(*) FROM media_unified
        WHERE availability_status = 'BOOKKEEPING_DOWNLOADED_INACCESSIBLE'
          AND source_system = 'V1'
          AND (
            source_local_path LIKE '/home/tganb/dopams/media_cctnsv1/%%'
            OR availability_detail ILIKE '%%tganb%%'
          )
        """
    )
    report["v1_tganb_inaccessible"] = cur.fetchone()[0]

    cur.execute(
        """
        SELECT COUNT(*) FROM media_unified
        WHERE source_system = 'V1'
          AND availability_status = 'EMPTY_AT_SOURCE'
        """
    )
    report["v1_empty_at_source"] = cur.fetchone()[0]

    cur.execute(
        """
        SELECT COUNT(*) FROM media_unified
        WHERE source_system = 'V2'
          AND availability_status = 'UNRESOLVED_FILE_ID'
        """
    )
    report["v2_unresolved_file_id"] = cur.fetchone()[0]

    # KB preservation smoke
    cur.execute(
        """
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name LIKE 'kb_%%'
        ORDER BY 1
        """
    )
    kb_tables = [r[0] for r in cur.fetchall()]
    kb_counts = {}
    for t in kb_tables:
        cur.execute(f"SELECT COUNT(*) FROM {t}")  # noqa: S608 - trusted schema names
        kb_counts[t] = cur.fetchone()[0]
    report["kb_tables"] = kb_counts

    # Source RO counts
    try:
        v1 = get_v1_source_connection()
        v1c = v1.cursor()
        v1c.execute("SELECT COUNT(*) FROM cctns.cctns_media_files")
        report["v1_source_count"] = v1c.fetchone()[0]
        v1.close()
    except Exception as exc:
        report["v1_source_count_error"] = str(exc)

    try:
        v2 = get_v2_source_connection()
        v2c = v2.cursor()
        v2c.execute("SELECT COUNT(*) FROM file_media_bookkeeping")
        report["v2_source_count"] = v2c.fetchone()[0]
        v2c.execute(
            """
            SELECT COUNT(*) FROM file_media_bookkeeping
            WHERE source_type = 'case_property' AND file_id IS NULL
            """
        )
        report["v2_case_property_null_file_id"] = v2c.fetchone()[0]
        v2.close()
    except Exception as exc:
        report["v2_source_count_error"] = str(exc)

    # Idempotency hint: observation vs unified
    report["observation_vs_unified"] = {
        "obs_distinct": sum(x["distinct_ids"] for x in report["observations"]),
        "unified_total": report["unified_total"],
    }

    cur.execute(
        """
        SELECT run_id::text, status, started_at, finished_at,
               rows_observed, rows_changed, error_message
        FROM consolidation_run_log
        ORDER BY started_at DESC LIMIT 3
        """
    )
    report["recent_runs"] = [
        {
            "run_id": a,
            "status": b,
            "started_at": str(c),
            "finished_at": str(d) if d else None,
            "rows_observed": e,
            "rows_changed": f,
            "error": g,
        }
        for a, b, c, d, e, f, g in cur.fetchall()
    ]

    tg.close()

    out_path = Path(__file__).resolve().parent / "reports" / "media_consolidation_validation.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps(report, indent=2, default=str))
    print(f"\nWrote {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
