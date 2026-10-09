"""Progress + postgres activity for media consolidation."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.db.connections import get_unified_connection


def main() -> None:
    ps = subprocess.check_output(
        "ps -p $(pgrep -f run_media_consolidation.py | head -1) -o pid,etime,%cpu,rss,stat 2>/dev/null || echo DEAD",
        shell=True,
        text=True,
    )
    print("PS:\n", ps)
    c = get_unified_connection()
    cur = c.cursor()
    cur.execute("SELECT COUNT(*) FROM media_unified")
    print("media_unified", cur.fetchone()[0])
    cur.execute(
        """
        SELECT pid, state, wait_event_type, wait_event,
               now()-query_start AS age,
               left(query, 160)
        FROM pg_stat_activity
        WHERE datname = current_database()
          AND pid <> pg_backend_pid()
        ORDER BY query_start NULLS LAST
        """
    )
    print("PG_ACTIVITY:")
    for row in cur.fetchall():
        print(row)
    cur.execute(
        """
        SELECT run_id::text, status, now()-started_at AS age
        FROM consolidation_run_log
        WHERE status='running'
        """
    )
    print("running_runs", cur.fetchall())
    c.close()


if __name__ == "__main__":
    main()
