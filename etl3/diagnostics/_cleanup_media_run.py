"""Stop stuck media consolidation and mark run failed."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "etl3"))

from db.connections import get_unified_connection

PID = 1115552


def main():
    subprocess.run(f"kill {PID}", shell=True)
    subprocess.run("sleep 2", shell=True)
    ps = subprocess.check_output(f"ps -p {PID} || echo killed", shell=True, text=True)
    print(ps)
    tg = get_unified_connection()
    cur = tg.cursor()
    cur.execute(
        """
        UPDATE consolidation_run_log
        SET status='failed', finished_at=now(),
            error_message='interrupted: restarted with slim media payloads'
        WHERE status='running'
        """
    )
    cur.execute(
        """
        UPDATE consolidation_cursor
        SET status='idle'
        WHERE status IN ('running', 'failed')
        """
    )
    tg.commit()
    print("cleaned")
    tg.close()


if __name__ == "__main__":
    main()
