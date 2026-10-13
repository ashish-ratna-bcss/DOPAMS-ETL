"""RO report for a limited AI smoke window (pass since ISO timestamp)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from etl3.db.connections import get_unified_connection


def main() -> None:
    since = sys.argv[1] if len(sys.argv) > 1 else None
    if not since:
        raise SystemExit("usage: _limited_smoke_report_ro.py <since-iso-ts>")
    conn = get_unified_connection(readonly=True)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT count(*) AS attempts,
               count(DISTINCT crime_id) AS distinct_firs,
               count(*) FILTER (WHERE status='success') AS success,
               count(*) FILTER (WHERE status='invalid') AS invalid,
               count(*) FILTER (WHERE status='empty') AS empty,
               count(*) FILTER (WHERE status='timeout') AS timeout,
               count(*) FILTER (WHERE status='error') AS error,
               count(*) FILTER (WHERE validation_status='accepted') AS accepted,
               count(*) FILTER (WHERE validation_status='partial') AS partial,
               count(*) FILTER (WHERE validation_status='rejected') AS rejected,
               count(*) FILTER (WHERE error_message ILIKE 'malformed json%%') AS malformed,
               avg(length(raw_response)) FILTER (WHERE raw_response IS NOT NULL) AS avg_raw_len,
               max(length(raw_response)) FILTER (WHERE raw_response IS NOT NULL) AS max_raw_len,
               count(*) FILTER (WHERE status='invalid' AND raw_response IS NOT NULL AND length(raw_response)>0) AS invalid_with_raw
        FROM ai_extraction_attempts
        WHERE created_at >= %s::timestamptz
        """,
        (since,),
    )
    row = cur.fetchone()
    keys = [
        "attempts", "distinct_firs", "success", "invalid", "empty", "timeout",
        "error", "accepted", "partial", "rejected", "malformed", "avg_raw_len",
        "max_raw_len", "invalid_with_raw",
    ]
    summary = dict(zip(keys, row))
    print("=== SUMMARY ===")
    print(json.dumps(summary, indent=2, default=str))

    cur.execute(
        """
        SELECT status, validation_status, count(*),
               round(avg(length(raw_response)) FILTER (WHERE raw_response IS NOT NULL)::numeric, 1)
        FROM ai_extraction_attempts
        WHERE created_at >= %s::timestamptz
        GROUP BY 1, 2 ORDER BY 3 DESC
        """,
        (since,),
    )
    print("\n=== BY STATUS ===")
    for r in cur.fetchall():
        print(r)

    cur.execute(
        """
        SELECT id, crime_id, created_at, status, validation_status,
               attempt_count, left(coalesce(error_message,''), 100),
               length(coalesce(raw_response,''))
        FROM ai_extraction_attempts
        WHERE created_at >= %s::timestamptz
        ORDER BY created_at
        """,
        (since,),
    )
    print("\n=== ATTEMPTS ===")
    for r in cur.fetchall():
        print(r)

    # Duplicate processing: same digest settled twice in window (should be rare)
    cur.execute(
        """
        SELECT crime_id, input_hash, count(*)
        FROM ai_extraction_attempts
        WHERE created_at >= %s::timestamptz
        GROUP BY 1, 2
        HAVING count(*) > 3
        ORDER BY 3 DESC
        LIMIT 10
        """,
        (since,),
    )
    print("\n=== HIGH ATTEMPT COUNTS (same crime+hash) ===")
    rows = cur.fetchall()
    print(rows or "none")
    conn.close()


if __name__ == "__main__":
    main()
