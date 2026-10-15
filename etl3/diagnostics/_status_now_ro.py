#!/usr/bin/env python3
"""Read-only backlog + recent throughput snapshot."""
from __future__ import annotations

from datetime import datetime, timezone

from etl3.db.connections import get_unified_connection
from etl3.run_ai_backfill import print_status


def main() -> None:
    conn = get_unified_connection(readonly=True)
    cur = conn.cursor()
    now = datetime.now(timezone.utc)
    print(f"now_utc={now.isoformat()}")

    print("--- backlog ---")
    print_status(conn)

    for label, iv, mult in (
        ("1h", "1 hour", 1),
        ("6h", "6 hours", 1 / 6),
        ("12h", "12 hours", 1 / 12),
        ("24h", "24 hours", 1 / 24),
    ):
        cur.execute(
            f"""
            SELECT count(*) AS n,
                   count(*) FILTER (WHERE status='success') AS ok,
                   count(*) FILTER (WHERE validation_status='accepted') AS accepted,
                   count(*) FILTER (WHERE status='empty') AS empty,
                   count(*) FILTER (WHERE status='timeout') AS timeout,
                   count(*) FILTER (WHERE status='error') AS err
            FROM ai_extraction_attempts
            WHERE created_at >= now() - interval '{iv}'
            """
        )
        n, ok, acc, empty, to, err = cur.fetchone()
        print(
            f"last_{label}: attempts={n} ok={ok} accepted={acc} empty={empty} "
            f"timeout={to} err={err} equiv_per_hour={n * mult:.1f}"
        )

    cur.execute(
        """
        SELECT created_at, status, validation_status, source_module
        FROM ai_extraction_attempts
        ORDER BY created_at DESC
        LIMIT 8
        """
    )
    print("latest_attempts:")
    for r in cur.fetchall():
        print(" ", r)

    # pending burn vs morning-ish baseline if useful: just print current pending
    conn.close()


if __name__ == "__main__":
    main()
