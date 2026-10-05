"""V1 accused_details rows that have no person_code.

Those rows stay in arrests_source. They are not given an invented person
id. The gap ledger is how they stay visible: the source itself has no
person key and no name, which is a source limitation, not a guessed link.
"""
from psycopg2.extras import execute_values

GAP_TYPE = "v1_person_key_absent"


def record_v1_missing_person_keys(conn) -> dict:
    from etl3.merger.current_state import fetch_latest_by_record_id

    missing = []
    present = []
    for record_id, _run, _created, _modified, payload, _obs in fetch_latest_by_record_id(
        conn, "arrests_source", "V1"
    ):
        code = payload.get("person_code")
        if code is None or str(code).strip() == "":
            missing.append(record_id)
        else:
            present.append(record_id)
    with conn.cursor() as cur:
        if missing:
            execute_values(
                cur,
                """
                INSERT INTO source_gap_ledger
                    (source_system, gap_type, gap_key, first_seen_at, status, source_evidence_table)
                VALUES %s
                ON CONFLICT (source_system, gap_type, gap_key) DO NOTHING
                """,
                [("V1", GAP_TYPE, record_id) for record_id in missing],
                template="(%s, %s, %s, now(), 'OPEN', 'cctns_accused_details')",
            )
        if present:
            cur.execute(
                """
                UPDATE source_gap_ledger
                SET status = 'RESOLVED'
                WHERE source_system = 'V1' AND gap_type = %s
                  AND gap_key = ANY(%s) AND status = 'OPEN'
                """,
                (GAP_TYPE, present),
            )
    return {"missing": len(missing), "with_key": len(present)}
