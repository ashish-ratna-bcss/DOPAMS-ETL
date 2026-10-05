"""Copy source-declared gaps into source_gap_ledger.

Idempotent. A gap this function has already written is not reopened and
is not duplicated. Nothing here invents a person, an accused link, or a
resolved status the source did not already record.
"""
from psycopg2.extras import execute_values

from etl3.sources.v1.adapter import V1Adapter
from etl3.sources.v2.adapter import V2Adapter


def copy_source_gaps(conn) -> dict:
    entries = []
    for gap in V1Adapter().get_source_gap_state() + V2Adapter().get_source_gap_state():
        status = "RESOLVED" if (gap.status or "").upper() == "RESOLVED" else "OPEN"
        entries.append((gap.source_system, gap.gap_type, gap.gap_key, status, gap.source_evidence_table))
    if not entries:
        return {"offered": 0, "newly_inserted": 0}
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM source_gap_ledger")
        before = cur.fetchone()[0]
        execute_values(
            cur,
            """
            INSERT INTO source_gap_ledger
                (source_system, gap_type, gap_key, first_seen_at, status, source_evidence_table)
            VALUES %s
            ON CONFLICT (source_system, gap_type, gap_key) DO NOTHING
            """,
            entries,
            template="(%s, %s, %s, now(), %s, %s)",
        )
        cur.execute("SELECT count(*) FROM source_gap_ledger")
        after = cur.fetchone()[0]
    return {"offered": len(entries), "newly_inserted": after - before}
