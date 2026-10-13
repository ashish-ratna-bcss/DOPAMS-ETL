#!/usr/bin/env python3
"""Read-only pre-cutover baseline for AI backfill (no writes)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.db import connections
from etl3.run_ai_backfill import print_status


def main():
    conn = connections.get_unified_connection(readonly=True)
    try:
        report = print_status(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT status, COALESCE(validation_status, ''), COUNT(*)
                FROM ai_extraction_attempts
                GROUP BY 1, 2
                ORDER BY 1, 2
                """
            )
            attempts = [
                {"status": s, "validation_status": v, "count": n}
                for s, v, n in cur.fetchall()
            ]
            cur.execute(
                "SELECT COUNT(*) FROM drug_extractions WHERE provenance = 'etl3_ai'"
            )
            ai_drugs = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM kb.drug_categories")
            kb_cats = cur.fetchone()[0]
        out = {
            "status_report": report,
            "attempt_groups": attempts,
            "ai_drug_rows": ai_drugs,
            "kb_drug_categories": kb_cats,
        }
        Path("/tmp/ai_backfill_precutover_baseline.json").write_text(
            json.dumps(out, indent=2, default=str), encoding="utf-8"
        )
        print("WROTE /tmp/ai_backfill_precutover_baseline.json", flush=True)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
