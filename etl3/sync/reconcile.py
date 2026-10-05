"""Classify source → observation → unified differences.

Classifications:
  EXPECTED                 observation is complete, and the unified count is
                           explainable (equal, or a documented logical collapse)
  INTENTIONALLY_EXCLUDED   the module is captured and is not merged
  UNRESOLVED               a current source key has no observation, or an
                           observed row has no current-state row and no
                           documented collapse
  MISMATCH                 unified rows exceed the logical source population,
                           or an integrity check failed
  KNOWN_SOURCE_LIMITATION  an open gap whose cause is the source, not ETL-3

Older rows may still say DEFECT or KNOWN_SOURCE_GAP. New rows use the names above.
"""
from psycopg2.extras import execute_values

from etl3.sync.catalog import MODULES

# Open gap_type -> why it is open. An unknown type is an ETL defect so a new
# gap cannot hide inside a known bucket.
GAP_CLASS = {
    "ora_06502_window": "KNOWN_SOURCE_LIMITATION",
    "unresolved_record_key": "KNOWN_SOURCE_LIMITATION",
    "v1_person_key_absent": "KNOWN_SOURCE_LIMITATION",
    "fk_retry_capped": "KNOWN_SOURCE_LIMITATION",
    "unlinked_accused": "KNOWN_SOURCE_LIMITATION",
    "unlinked_persons_placeholder": "KNOWN_SOURCE_LIMITATION",
    "address_unresolved": "DATA_QUALITY",
    "unresolved_arrest_accused_link": "UNRESOLVED_RELATIONSHIP",
    "unresolved_accused_person_link": "UNRESOLVED_RELATIONSHIP",
    "unresolved_interrogation_person_link": "UNRESOLVED_RELATIONSHIP",
    "unresolved_v1_ps_code": "UNRESOLVED_RELATIONSHIP",
    "ambiguous_v1_ps_code": "UNRESOLVED_RELATIONSHIP",
}


def classify_gap(gap_type: str) -> str:
    return GAP_CLASS.get(gap_type, "ETL_DEFECT")


def classify_module(*, source_count: int, observed_count: int, collapse: bool, defect: bool = False,
                    excluded: bool = False, unified_count=None) -> str:
    """A missing observation is UNRESOLVED even when the module is excluded.

    Exclusion applies only after the source row has been captured. A logical
    collapse (V1 accused grouping) may leave unified below source. Unified
    above that population is a MISMATCH. unified_count is optional so a
    caller that only knows the observation counts is not forced to invent one.
    """
    if observed_count < source_count:
        return "UNRESOLVED"
    if excluded:
        return "INTENTIONALLY_EXCLUDED"
    if defect:
        return "MISMATCH"
    if unified_count is None or collapse:
        if unified_count is not None and unified_count > source_count:
            return "MISMATCH"
        return "EXPECTED"
    if unified_count > source_count:
        return "MISMATCH"
    if unified_count < source_count:
        return "UNRESOLVED"
    return "EXPECTED"


def _observed_count(conn, spec) -> int:
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT count(DISTINCT source_record_id)
            FROM {spec['obs_table']}
            WHERE source_system = %s AND source_table = %s
            """,
            (spec["source_system"], spec["source_table"]),
        )
        return cur.fetchone()[0]


def _shares_unified_table(spec) -> bool:
    fellows = [
        other for other in MODULES
        if other["unified_table"] == spec["unified_table"]
        and other["source_system"] == spec["source_system"]
        and not other.get("excluded_from_unified")
    ]
    return len(fellows) > 1


def _unified_count(conn, spec) -> int:
    with conn.cursor() as cur:
        if _shares_unified_table(spec):
            cur.execute(
                f"""
                SELECT count(*) FROM {spec['unified_table']}
                WHERE source_system = %s AND source_module = %s
                """,
                (spec["source_system"], spec["module"]),
            )
        elif spec["has_source_system"]:
            cur.execute(
                f"SELECT count(*) FROM {spec['unified_table']} WHERE source_system = %s",
                (spec["source_system"],),
            )
        else:
            cur.execute(f"SELECT count(*) FROM {spec['unified_table']}")
        return cur.fetchone()[0]


def _integrity_defect(conn) -> bool:
    checks = [
        "SELECT count(*) FROM (SELECT crime_id FROM crimes_unified GROUP BY 1 HAVING count(*) > 1) d",
        "SELECT count(*) FROM (SELECT person_id FROM persons_unified GROUP BY 1 HAVING count(*) > 1) d",
        "SELECT count(*) FROM (SELECT accused_id FROM accused_unified GROUP BY 1 HAVING count(*) > 1) d",
        "SELECT count(*) FROM (SELECT source_system, gap_type, gap_key FROM source_gap_ledger GROUP BY 1, 2, 3 HAVING count(*) > 1) d",
        """
        SELECT count(*) FROM accused_unified a
        WHERE NOT EXISTS (SELECT 1 FROM crimes_unified c WHERE c.crime_id = a.crime_id)
        """,
        """
        SELECT count(*) FROM arrests_unified a
        WHERE a.accused_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM accused_unified x WHERE x.accused_id = a.accused_id)
        """,
    ]
    with conn.cursor() as cur:
        for sql in checks:
            cur.execute(sql)
            if cur.fetchone()[0]:
                return True
    return False


def reconcile(conn, source_counts: dict) -> list:
    """source_counts maps (source_system, module) -> current source pk count.

    Writes one reconciliation_run_log row per module plus one gap summary.
    Returns the classification rows.
    """
    defect = _integrity_defect(conn)
    rows = []
    for spec in MODULES:
        key = (spec["source_system"], spec["module"])
        source_count = source_counts.get(key)
        if source_count is None:
            continue
        observed = _observed_count(conn, spec)
        unified = _unified_count(conn, spec)
        status = classify_module(
            source_count=source_count,
            observed_count=observed,
            collapse=spec["collapse"],
            excluded=spec.get("excluded_from_unified", False),
            unified_count=unified,
        )
        rows.append((spec["source_system"], spec["source_table"], source_count, observed, unified,
                     source_count - observed, status))
    if defect:
        rows.append(("V1", "__integrity__", 0, 0, 0, 0, "MISMATCH"))

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT source_system, gap_type, count(*)
            FROM source_gap_ledger
            WHERE status = 'OPEN'
            GROUP BY source_system, gap_type
            """
        )
        for system, gap_type, open_gaps in cur.fetchall():
            rows.append((system, f"gap:{gap_type}", open_gaps, open_gaps, open_gaps, 0, classify_gap(gap_type)))

    if rows:
        with conn.cursor() as cur:
            execute_values(
                cur,
                """
                INSERT INTO reconciliation_run_log
                    (source_system, source_table, source_count, observed_count, unified_count, delta, status)
                VALUES %s
                """,
                rows,
            )
    return [
        {
            "source_system": system,
            "source_table": table,
            "source_count": source_count,
            "observed_count": observed,
            "unified_count": unified,
            "delta": delta,
            "status": status,
        }
        for system, table, source_count, observed, unified, delta, status in rows
    ]
