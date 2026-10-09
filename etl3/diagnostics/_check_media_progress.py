"""Quick progress check for media consolidation."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.db.connections import get_unified_connection


def main() -> None:
    ps = subprocess.check_output(
        "ps aux | grep run_media_consolidation | grep -v grep || true",
        shell=True,
        text=True,
    )
    print("PROCESS:", (ps.strip() or "(none)"))
    log = Path("/home/eagle/dopams-cctns-ai/logs/media_consolidation.log")
    if log.exists():
        print(f"log_bytes={log.stat().st_size}")
        print("TAIL:", "\n".join(log.read_text(errors="replace").splitlines()[-15:]))
    c = get_unified_connection()
    cur = c.cursor()
    cur.execute("SELECT COUNT(*) FROM media_unified")
    print("media_unified", cur.fetchone()[0])
    cur.execute(
        """
        SELECT run_id::text, status, started_at, finished_at, error_message
        FROM consolidation_run_log
        ORDER BY started_at DESC LIMIT 1
        """
    )
    print("last_run", cur.fetchone())
    c.close()


if __name__ == "__main__":
    main()
