"""Quick AI attempt / drug snapshot."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.db.connections import get_unified_connection


def main() -> None:
    c = get_unified_connection(readonly=True)
    cur = c.cursor()
    cur.execute("SELECT COUNT(*) FROM ai_extraction_attempts")
    print("attempts_total", cur.fetchone()[0])
    cur.execute("SELECT MAX(created_at) FROM ai_extraction_attempts")
    print("latest_attempt", cur.fetchone()[0])
    cur.execute("SELECT COUNT(*) FROM drug_extractions WHERE provenance='etl3_ai'")
    print("ai_drugs", cur.fetchone()[0])
    cur.execute(
        """
        SELECT status, COALESCE(validation_status,''), COUNT(*)
        FROM ai_extraction_attempts GROUP BY 1,2 ORDER BY 1,2
        """
    )
    for row in cur.fetchall():
        print("group", row)
    c.close()


if __name__ == "__main__":
    main()
