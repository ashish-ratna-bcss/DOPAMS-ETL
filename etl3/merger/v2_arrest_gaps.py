"""
Record V2 arrests that have no accused_unified link.

Does not change current-state computation. arrests_unified.accused_id stays
NULL. No person_id is invented and no fuzzy match is attempted.

The gap is the same shape as V1's unresolved_arrest_accused_link
(source_system, gap_type, gap_key), with source_system='V2'. The key is the
source arrest id plus a reason taken only from data already on the row:

  source_person_id_null
      latest arrests_source payload has person_id NULL, so there is no
      (crime_id, person_id) lookup key.
  no_accused_for_crime_person
      person_id is present, but accused_unified has no row for that
      (crime_id, person_id). The link is still not guessed.
"""
from psycopg2.extras import execute_values

GAP_TYPE = "unresolved_arrest_accused_link"
EVIDENCE_TABLE = "arrests"


def record_v2_unresolved_arrest_accused_gaps(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT a.source_record_id, latest.person_id
            FROM arrests_unified a
            JOIN LATERAL (
                SELECT payload->>'person_id' AS person_id
                FROM arrests_source s
                WHERE s.source_system = 'V2'
                  AND s.source_record_id = a.source_record_id
                ORDER BY s.id DESC
                LIMIT 1
            ) latest ON true
            WHERE a.source_system = 'V2'
              AND a.accused_id IS NULL
            ORDER BY a.source_record_id
            """
        )
        rows = cur.fetchall()

    null_person = 0
    person_without_accused = 0
    gaps = []
    for source_record_id, person_id in rows:
        if person_id:
            reason = "no_accused_for_crime_person"
            person_without_accused += 1
        else:
            reason = "source_person_id_null"
            null_person += 1
        gaps.append(("V2", GAP_TYPE, f"arrest_id={source_record_id}|reason={reason}"))

    inserted = 0
    if gaps:
        with conn.cursor() as cur:
            inserted_rows = execute_values(
                cur,
                """
                INSERT INTO source_gap_ledger
                    (source_system, gap_type, gap_key, first_seen_at, status, source_evidence_table)
                VALUES %s
                ON CONFLICT (source_system, gap_type, gap_key) DO NOTHING
                RETURNING id
                """,
                gaps,
                template="(%s, %s, %s, now(), 'OPEN', 'arrests')",
                fetch=True,
            )
            inserted = len(inserted_rows)
    return {
        "unresolved_arrests": len(rows),
        "source_person_id_null": null_person,
        "person_without_accused_row": person_without_accused,
        "newly_inserted": inserted,
    }


def resolve_arrest_gaps_now_linked(conn) -> int:
    """Close gap rows whose arrest now has an accused_id.

    The link itself is only the existing exact (crime_id, person_id)
    lookup. This does not invent a person or an accused. A later copy of
    the same gap key does not reopen it (ON CONFLICT DO NOTHING).
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE source_gap_ledger g
            SET status = 'RESOLVED'
            WHERE g.status = 'OPEN'
              AND g.gap_type = 'unresolved_arrest_accused_link'
              AND (
                (g.source_system = 'V2' AND EXISTS (
                    SELECT 1 FROM arrests_unified a
                    WHERE a.source_system = 'V2'
                      AND a.accused_id IS NOT NULL
                      AND g.gap_key LIKE 'arrest_id=' || a.source_record_id || '|reason=%'
                ))
                OR
                (g.source_system = 'V1' AND EXISTS (
                    SELECT 1 FROM arrests_unified a
                    WHERE a.source_system = 'V1'
                      AND a.accused_id IS NOT NULL
                      AND right(g.gap_key, char_length('accused_details_id=' || a.source_record_id))
                          = 'accused_details_id=' || a.source_record_id
                ))
              )
            """
        )
        return cur.rowcount
