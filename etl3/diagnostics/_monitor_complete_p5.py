"""Monitor AI-disabled Phase-5 completion."""
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
        "ps aux | grep _complete_phase5_no_ai | grep -v grep || true",
        shell=True,
        text=True,
    )
    print("PROCESSES:")
    print(ps or "(none)")
    log = Path("/home/eagle/dopams-cctns-ai/logs/phase5_complete_no_ai.log")
    print(f"log_exists={log.exists()} bytes={log.stat().st_size if log.exists() else 0}")
    if log.exists():
        print("--- TAIL ---")
        print("\n".join(log.read_text(errors="replace").splitlines()[-50:]))
    tg = get_unified_connection(readonly=True)
    cur = tg.cursor()
    cur.execute(
        """
        SELECT run_id, status, started_at, finished_at, rows_observed
        FROM consolidation_run_log ORDER BY started_at DESC LIMIT 5
        """
    )
    print("--- RUNS ---")
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
