"""Inspect state after failed Phase-5 (observations may already be committed)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "etl3"))

from db.connections import get_unified_connection, get_v2_source_connection

IDS = [
    "6a825ec7d8a9e1156252d824",
    "6ac788d1515873145609a177",
    "6ac795b3d5f5f99c45fc93b9",
    "6ac7afe12308a466ad90e3be",
    "6ac7bd794090c46bdc603c11",
    "6ac7c31999703f12c73f99dc",
    "6ac7c9dfcf6b6f185de35b2f",
    "6ac7db0c99703fb493400ef1",
    "6ac7ecedd3448842629ff4d7",
    "6ac7f28c4ef853fa86d881b0",
]


def main():
    v2 = get_v2_source_connection()
    vc = v2.cursor()
    vc.execute("SELECT COUNT(*) FROM crimes")
    v2_count = vc.fetchone()[0]
    v2.close()

    tg = get_unified_connection()
    cur = tg.cursor()
    cur.execute("SELECT COUNT(*) FROM crimes_unified WHERE source_system='V2'")
    u_count = cur.fetchone()[0]
    cur.execute(
        "SELECT crime_id FROM crimes_unified WHERE source_system='V2' AND crime_id = ANY(%s) ORDER BY 1",
        (IDS,),
    )
    present = [r[0] for r in cur.fetchall()]
    cur.execute(
        """
        SELECT source_record_id, source_run_id, consolidation_run_id
        FROM crimes_source
        WHERE source_system='V2' AND source_record_id = ANY(%s)
        ORDER BY 1
        """,
        (IDS,),
    )
    obs = [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]
    cur.execute(
        """
        SELECT run_id, status, error_message, rows_observed, rows_changed, started_at, finished_at
        FROM consolidation_run_log WHERE run_id='aa14bd75-eaea-4fc2-a704-aa02f9276afa'
        """
    )
    run = dict(zip([d[0] for d in cur.description], cur.fetchone() or []))
    cur.execute("SELECT status, COUNT(1) FROM consolidation_cursor GROUP BY 1")
    cursors = cur.fetchall()
    cur.execute(
        """
        SELECT source_system, source_module, last_processed_source_run_id, status
        FROM consolidation_cursor WHERE source_system='V2' AND source_module='crimes'
        """
    )
    crime_cursor = dict(zip([d[0] for d in cur.description], cur.fetchone() or []))
    tg.close()
    out = {
        "v2_count": v2_count,
        "unified_v2": u_count,
        "missing_still": 10 - len(present),
        "present_in_unified": present,
        "obs_count": len(obs),
        "obs": obs,
        "failed_run": run,
        "cursors": cursors,
        "crime_cursor": crime_cursor,
    }
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
