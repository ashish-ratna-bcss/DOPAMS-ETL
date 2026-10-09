"""Monitor Phase-5 remediation run status."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "etl3"))

from db.connections import get_unified_connection


def main():
    ps = subprocess.check_output(
        "ps aux | grep -E '_run_phase5|run_phase5' | grep -v grep || true",
        shell=True,
        text=True,
    )
    print("PROCESSES:")
    print(ps or "(none)")
    log = Path("/home/eagle/dopams-cctns-ai/logs/phase5_gap_remediation.log")
    if log.exists():
        text = log.read_text(errors="replace")
        print(f"LOG_BYTES={len(text)}")
        print("--- LOG TAIL ---")
        print("\n".join(text.splitlines()[-40:]))
    else:
        print("LOG missing")

    tg = get_unified_connection(readonly=True)
    cur = tg.cursor()
    cur.execute(
        """
        SELECT run_id, status, started_at, finished_at, rows_observed, rows_changed
        FROM consolidation_run_log
        ORDER BY started_at DESC
        LIMIT 5
        """
    )
    print("--- RUN LOG ---")
    for r in cur.fetchall():
        print(r)
    cur.execute("SELECT status, COUNT(1) FROM consolidation_cursor GROUP BY 1")
    print("cursors", cur.fetchall())
    report = Path(
        "/home/eagle/dopams-cctns-ai/etl3/diagnostics/reports/phase5_gap_remediation_validation.json"
    )
    print("report_exists", report.exists())
    tg.close()


if __name__ == "__main__":
    main()
