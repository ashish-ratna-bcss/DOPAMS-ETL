"""Classify source → observation → unified differences.

Classifications:
  EXPECTED           counts agree, or a known collapse/retention explains them
  KNOWN_SOURCE_GAP   an open source_gap_ledger row already accounts for the area
  UNRESOLVED         a current source primary key has no observation
  DEFECT             duplicate keys, duplicate gap keys, or a foreign-key miss
"""
from psycopg2.extras import execute_values

from etl3.sync.catalog import MODULES


def classify_module(*, source_count: int, observed_count: int, collapse: bool, defect: bool = False,
                    excluded: bool = False) -> str:
    """excluded: the module is intentionally not merged into a unified table.

    A unified count below the source count is EXPECTED in that case. An
    observation shortfall is still UNRESOLVED, because the source row was
    not captured at all.
    """
    if defect and not excluded:
        return "DEFECT"
    if observed_count < source_count:
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


def _unified_count(conn, spec) -> int:
    with conn.cursor() as cur:
        if spec["has_source_system"]:
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
        )
        rows.append((spec["source_system"], spec["source_table"], source_count, observed, unified,
                     source_count - observed, status))
    if defect:
        rows.append(("V1", "__integrity__", 0, 0, 0, 0, "DEFECT"))

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT source_system, count(*)
            FROM source_gap_ledger
            WHERE status = 'OPEN'
            GROUP BY source_system
            """
        )
        for system, open_gaps in cur.fetchall():
            rows.append((system, "source_gap_ledger", open_gaps, open_gaps, open_gaps, 0, "KNOWN_SOURCE_GAP"))

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
