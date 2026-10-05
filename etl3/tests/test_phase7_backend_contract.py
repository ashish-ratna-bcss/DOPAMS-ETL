"""Phase 7 read contract. The DOPAMS backend must use these views, not firs_mv.

Run with: python etl3/tests/test_phase7_backend_contract.py
"""
import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.db import connections


def check(label, fn):
    try:
        fn()
        print(f"[PASS] {label}")
    except Exception as e:
        print(f"[FAIL] {label}: {type(e).__name__}: {e}")
        raise


def _one(cur, sql, params=None):
    cur.execute(sql, params or ())
    return cur.fetchone()[0]


def test_crimes_with_no_ps_code_stay_visible():
    conn = connections.get_unified_connection(readonly=True)
    try:
        with conn.cursor() as cur:
            missing = _one(cur, "SELECT count(*) FROM crimes_unified WHERE ps_code IS NULL")
            visible = _one(cur, "SELECT count(*) FROM be_read.crime WHERE ps_code IS NULL")
            assert missing > 0
            assert visible == missing
            assert _one(cur, "SELECT count(*) FROM be_read.crime") == _one(
                cur, "SELECT count(*) FROM crimes_unified"
            )
    finally:
        conn.close()


def test_views_do_not_drop_or_duplicate_rows():
    conn = connections.get_unified_connection(readonly=True)
    try:
        with conn.cursor() as cur:
            pairs = (
                ("crime", "crimes_unified", "crime_id"),
                ("accused", "accused_unified", "accused_id"),
                ("person", "persons_unified", "person_id"),
                ("arrest", "arrests_unified", "arrest_id"),
                ("chargesheet", "chargesheets_unified", "charge_sheet_id"),
                ("fsl_historical", "fsl_unified", "case_property_id"),
            )
            for view, table, pk in pairs:
                assert _one(cur, f"SELECT count(*) FROM be_read.{view}") == _one(
                    cur, f"SELECT count(*) FROM {table}"
                )
                assert _one(cur, f"SELECT count(*) FROM be_read.{view}") == _one(
                    cur, f"SELECT count(DISTINCT {pk}) FROM be_read.{view}"
                )
            unmatched_sql = """
                SELECT count(*) FROM {table} c
                WHERE c.ps_code IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM be_read.hierarchy h WHERE h.ps_code = c.ps_code
                  )
            """
            assert _one(cur, unmatched_sql.format(table="crimes_unified")) == _one(
                cur, unmatched_sql.format(table="be_read.crime")
            )
            assert _one(cur, "SELECT count(*) FROM be_read.arrest WHERE accused_id IS NULL") == _one(
                cur, "SELECT count(*) FROM arrests_unified WHERE accused_id IS NULL"
            )
            assert _one(cur, "SELECT count(*) FROM be_read.arrest WHERE accused_id IS NULL") > 0
    finally:
        conn.close()


def test_chargesheet_ids_stay_source_scoped():
    conn = connections.get_unified_connection(readonly=True)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT source_system, source_module, count(*)
                FROM be_read.chargesheet
                GROUP BY 1, 2
                ORDER BY 1, 2
                """
            )
            counts = dict(((a, b), n) for a, b, n in cur.fetchall())
            assert counts[("V1", "court")] == 7534
            assert counts[("V2", "chargesheets")] == 7086
            assert counts[("V2", "charge_sheet_updates")] == 6193
            shared = _one(
                cur,
                """
                SELECT count(*) FROM (
                    SELECT source_record_id FROM be_read.chargesheet WHERE source_module = 'court'
                    INTERSECT
                    SELECT source_record_id FROM be_read.chargesheet WHERE source_module = 'charge_sheet_updates'
                ) s
                """,
            )
            assert shared == 6193
            distinct_ids = _one(cur, "SELECT count(DISTINCT charge_sheet_id) FROM be_read.chargesheet")
            total = _one(cur, "SELECT count(*) FROM be_read.chargesheet")
            assert distinct_ids == total
            cur.execute(
                """
                SELECT charge_sheet_id, source_module, crime_id
                FROM be_read.chargesheet
                WHERE source_record_id = '1'
                ORDER BY charge_sheet_id
                """
            )
            raw_one = cur.fetchall()
            assert [row[0] for row in raw_one] == ["V1:court:1", "V2:charge_sheet_updates:1"]
            assert raw_one[0][2] != raw_one[1][2]
    finally:
        conn.close()


def test_null_person_links_remain_and_people_are_not_merged():
    conn = connections.get_unified_connection(readonly=True)
    try:
        with conn.cursor() as cur:
            assert _one(cur, "SELECT count(*) FROM be_read.accused WHERE person_id IS NULL") == _one(
                cur, "SELECT count(*) FROM accused_unified WHERE person_id IS NULL"
            )
            assert _one(cur, "SELECT count(*) FROM be_read.accused WHERE person_id IS NULL") > 0
            assert _one(cur, "SELECT count(*) FROM be_read.person") == _one(
                cur, "SELECT count(*) FROM persons_unified"
            )
            assert _one(cur, "SELECT count(*) FROM be_read.identity_candidate WHERE status <> 'candidate'") == 0
            assert _one(cur, "SELECT count(*) FROM identity_links WHERE status = 'confirmed'") == 0
    finally:
        conn.close()


def test_one_person_can_have_many_accused_and_pages_do_not_overlap():
    conn = connections.get_unified_connection(readonly=True)
    try:
        with conn.cursor() as cur:
            view_multi = _one(
                cur,
                """
                SELECT count(*) FROM (
                    SELECT person_id FROM be_read.accused
                    WHERE person_id IS NOT NULL
                    GROUP BY person_id HAVING count(*) > 1
                ) s
                """,
            )
            table_multi = _one(
                cur,
                """
                SELECT count(*) FROM (
                    SELECT person_id FROM accused_unified
                    WHERE person_id IS NOT NULL
                    GROUP BY person_id HAVING count(*) > 1
                ) s
                """,
            )
            assert view_multi == table_multi
            pages = []
            for offset in (0, 50, 100):
                cur.execute(
                    """
                    SELECT crime_id FROM be_read.crime
                    ORDER BY crime_id
                    LIMIT 50 OFFSET %s
                    """,
                    (offset,),
                )
                pages.append([row[0] for row in cur.fetchall()])
            assert len(pages[0]) == 50
            assert set(pages[0]).isdisjoint(pages[1])
            assert set(pages[1]).isdisjoint(pages[2])
            cur.execute(
                "SELECT crime_id FROM be_read.crime ORDER BY crime_id LIMIT 50 OFFSET 0"
            )
            again = [row[0] for row in cur.fetchall()]
            assert again == pages[0]
    finally:
        conn.close()


def test_fsl_view_is_historical_only():
    conn = connections.get_unified_connection(readonly=True)
    try:
        with conn.cursor() as cur:
            assert _one(cur, "SELECT count(*) FROM be_read.fsl_historical") == _one(
                cur, "SELECT count(*) FROM fsl_unified"
            )
            assert _one(
                cur, "SELECT count(*) FROM be_read.fsl_historical WHERE inclusion <> 'historical_not_merged'"
            ) == 0
    finally:
        conn.close()


def test_contract_rejects_writes_and_sources_stay_read_only():
    unified = connections.get_unified_connection()
    try:
        with unified.cursor() as cur:
            statements = (
                "UPDATE be_read.chargesheet SET court_name = court_name",
                "INSERT INTO be_read.crime (crime_id) VALUES ('etl3-p7-probe')",
                "DELETE FROM be_read.crime WHERE crime_id = (SELECT crime_id FROM be_read.crime LIMIT 1)",
            )
            for sql in statements:
                try:
                    cur.execute(sql)
                except psycopg2.Error as exc:
                    unified.rollback()
                    assert "ETL-3 owns" in str(exc)
                else:
                    raise AssertionError("view accepted a write: " + sql)
    finally:
        unified.close()

    readonly = connections.get_unified_connection(readonly=True)
    try:
        with readonly.cursor() as cur:
            try:
                cur.execute("UPDATE crimes_unified SET fir_num = fir_num")
            except psycopg2.errors.ReadOnlySqlTransaction:
                readonly.rollback()
            else:
                raise AssertionError("read-only session updated crimes_unified")
    finally:
        readonly.close()

    for label, getter, sql in (
        ("V1", connections.get_v1_source_connection, "CREATE TABLE etl3_p7_probe (x int)"),
        ("V2", connections.get_v2_source_connection, "CREATE TABLE etl3_p7_probe (x int)"),
    ):
        conn = getter()
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
            raise AssertionError(label + " accepted a write")
        except psycopg2.errors.ReadOnlySqlTransaction:
            conn.rollback()
        finally:
            conn.close()


def main():
    check("null police-station codes stay visible", test_crimes_with_no_ps_code_stay_visible)
    check("views do not drop or duplicate rows", test_views_do_not_drop_or_duplicate_rows)
    check("chargesheet ids stay source scoped", test_chargesheet_ids_stay_source_scoped)
    check("null persons stay unmerged", test_null_person_links_remain_and_people_are_not_merged)
    check("pagination does not overlap", test_one_person_can_have_many_accused_and_pages_do_not_overlap)
    check("FSL view is historical only", test_fsl_view_is_historical_only)
    check("writes are rejected", test_contract_rejects_writes_and_sources_stay_read_only)


if __name__ == "__main__":
    main()
