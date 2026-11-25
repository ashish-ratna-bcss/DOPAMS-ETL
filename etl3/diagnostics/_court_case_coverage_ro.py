#!/usr/bin/env python3
"""Read-only court-case-number coverage: unified v2 vs CCTNS v2 source."""
from __future__ import annotations

from etl3.db.connections import get_unified_connection, get_v2_source_connection


def q(cur, sql, params=None):
    cur.execute(sql, params or ())
    return cur.fetchall()


def unified() -> None:
    conn = get_unified_connection(readonly=True)
    cur = conn.cursor()
    print("=== dopams_cctns_v2.chargesheets_unified ===")
    for r in q(
        cur,
        """
        SELECT source_system, coalesce(source_module,'(null)'),
               count(*) AS n,
               count(*) FILTER (
                 WHERE court_case_num IS NOT NULL AND btrim(court_case_num) <> ''
               ) AS with_case_no,
               count(*) FILTER (
                 WHERE court_name IS NOT NULL AND btrim(court_name) <> ''
               ) AS with_court_name
        FROM chargesheets_unified
        GROUP BY 1, 2
        ORDER BY 1, 2
        """,
    ):
        print(" ", r)

    print("=== dopams_cctns_v2.chargesheet_enrichment ===")
    try:
        for r in q(
            cur,
            """
            SELECT source_system,
                   count(*) AS n,
                   count(*) FILTER (
                     WHERE taken_on_file_court_case_no IS NOT NULL
                       AND btrim(taken_on_file_court_case_no) <> ''
                   ) AS with_case_no
            FROM chargesheet_enrichment
            GROUP BY 1
            ORDER BY 1
            """,
        ):
            print(" ", r)
    except Exception as e:
        conn.rollback()
        print(" enrichment_err", type(e).__name__, e)

    print("=== source payload keys (chargesheets_source sample) ===")
    try:
        for r in q(
            cur,
            """
            SELECT source_system, source_module, count(*)
            FROM chargesheets_source
            GROUP BY 1, 2
            ORDER BY 1, 2
            """,
        ):
            print(" source_rows", r)
        for r in q(
            cur,
            """
            SELECT source_system, module,
                   count(*) AS n,
                   count(*) FILTER (
                     WHERE payload ? 'taken_on_file_court_case_no'
                       AND nullif(btrim(payload->>'taken_on_file_court_case_no'), '') IS NOT NULL
                   ) AS payload_case_no,
                   count(*) FILTER (
                     WHERE payload ? 'court_case_num'
                       AND nullif(btrim(payload->>'court_case_num'), '') IS NOT NULL
                   ) AS payload_court_case_num
            FROM (
              SELECT DISTINCT ON (source_system, source_module, source_record_id)
                     source_system, source_module AS module, payload
              FROM chargesheets_source
              ORDER BY source_system, source_module, source_record_id, observed_at DESC NULLS LAST
            ) s
            GROUP BY 1, 2
            ORDER BY 1, 2
            """,
        ):
            print(" payload", r)
    except Exception as e:
        conn.rollback()
        print(" source_err", type(e).__name__, str(e)[:300])
    conn.close()


def v2_source() -> None:
    print("=== cctns v2 source DB ===")
    try:
        conn = get_v2_source_connection()
    except Exception as e:
        print(" v2_connect_err", type(e).__name__, str(e)[:300])
        return
    cur = conn.cursor()
    cur.execute("SELECT current_database()")
    print(" db", cur.fetchone()[0])
    for r in q(
        cur,
        """
        SELECT count(*) AS n,
               count(*) FILTER (WHERE court_name IS NOT NULL AND btrim(court_name) <> '') AS court_name
        FROM chargesheets
        """,
    ):
        print(" chargesheets", r)
    for r in q(
        cur,
        """
        SELECT count(*) AS n,
               count(*) FILTER (
                 WHERE taken_on_file_court_case_no IS NOT NULL
                   AND btrim(taken_on_file_court_case_no) <> ''
               ) AS with_case_no,
               count(*) FILTER (
                 WHERE taken_on_file_case_type IS NOT NULL
                   AND btrim(taken_on_file_case_type) <> ''
               ) AS with_case_type,
               count(*) FILTER (
                 WHERE charge_sheet_status ILIKE 'taken on file'
               ) AS status_taken_on_file
        FROM charge_sheet_updates
        """,
    ):
        print(" charge_sheet_updates", r)
    # crimes that have a chargesheet but no court case number on any update
    for r in q(
        cur,
        """
        SELECT
          (SELECT count(DISTINCT crime_id) FROM chargesheets) AS crimes_with_cs,
          (SELECT count(DISTINCT crime_id) FROM charge_sheet_updates) AS crimes_with_update,
          (SELECT count(DISTINCT crime_id) FROM charge_sheet_updates
            WHERE taken_on_file_court_case_no IS NOT NULL
              AND btrim(taken_on_file_court_case_no) <> '') AS crimes_with_case_no
        """,
    ):
        print(" crime_coverage", r)
    conn.close()


if __name__ == "__main__":
    unified()
    v2_source()
