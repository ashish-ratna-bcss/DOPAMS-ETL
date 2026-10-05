"""
Phase 2G live verification for the V2 adapter. Run with:
    python etl3/tests/test_v2_adapter.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.db import connections
from etl3.sources.v2.adapter import V2Adapter, MODULE_PK


def check(label, fn):
    try:
        result = fn()
        print(f"[PASS] {label}")
        return result
    except Exception as e:
        print(f"[FAIL] {label}: {type(e).__name__}: {e}")
        raise


def _table_count(table):
    conn = connections.get_v2_source_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM {table}")
            return cur.fetchone()[0]
    finally:
        conn.rollback()
        conn.close()


def test_all_modules_sum_to_table_count():
    """For every supported module, the rows discovered across all runs must
    equal the table's own row count, UNLESS there are rows with a NULL
    etl_run_id -- in which case the gap must be fully explained, not just
    silently smaller."""
    a = V2Adapter()
    discrepancies = {}
    for module in a.supported_modules():
        runs = a.discover_new_runs(module)
        discovered = sum(r.row_count for r in runs)
        actual = _table_count(module)
        if discovered != actual:
            discrepancies[module] = (discovered, actual)
        print(f"       {module:25s} discovered={discovered:6d}  table_count={actual:6d}"
              + ("  <-- gap" if discovered != actual else ""))
    # The one known, explained exception is 'persons' (NULL etl_run_id rows
    # -- see Phase 2/3 docs). Not pinned to a historical absolute count:
    # V2's production ETL keeps running independently of this project (as
    # it must -- confirmed across this whole session), so table counts grow
    # over time. What must hold is the SHAPE of the discrepancy, re-derived
    # live each run, not a frozen snapshot value.
    assert set(discrepancies.keys()) == {"persons"}, discrepancies
    conn = connections.get_v2_source_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM persons WHERE etl_run_id IS NULL")
            null_run_id = cur.fetchone()[0]
    finally:
        conn.rollback()
        conn.close()
    discovered, actual = discrepancies["persons"]
    assert null_run_id == actual - discovered, (null_run_id, actual, discovered)
    print(f"       persons gap of {null_run_id} fully explained: {null_run_id} rows have etl_run_id IS NULL "
          "(these rows also have no full_name/date_created/fetched_at -- a distinct, pre-existing "
          "data-quality gap, not an adapter bug)")


def test_changed_records_match_direct_query():
    a = V2Adapter()
    runs = a.discover_new_runs("crimes")
    latest = runs[-1]
    recs = a.get_changed_records("crimes", latest.source_run_id)
    conn = connections.get_v2_source_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM crimes WHERE etl_run_id = %s", (latest.source_run_id,))
            direct = cur.fetchone()[0]
    finally:
        conn.rollback()
        conn.close()
    assert len(recs) == direct, (len(recs), direct)
    print(f"       adapter returned {len(recs)} records for run {latest.source_run_id}, matches direct query exactly")
    return recs[0] if recs else None


def test_source_record_resolves(sample_rec):
    a = V2Adapter()
    row = a.get_source_record("crimes", sample_rec.source_record_id)
    assert row is not None and row["crime_id"] == sample_rec.source_record_id
    print(f"       resolved crime_id={sample_rec.source_record_id} -> {len(row)} columns, "
          f"fir_reg_num={row.get('fir_reg_num')}")


def test_gap_state_matches_known_bookkeeping():
    a = V2Adapter()
    gaps = {g.gap_key: g for g in a.get_source_gap_state()}
    expected_keys = {
        "fk_retry:arrests", "fk_retry:fsl_case_property", "fk_retry:chargesheets",
        "fk_retry:updated_chargesheet", "failure:etl-address", "accused.person_id IS NULL",
    }
    assert set(gaps.keys()) == expected_keys, gaps.keys()
    assert gaps["fk_retry:arrests"].status == "RESOLVED"  # fully resolved, confirmed earlier this session
    conn = connections.get_v2_source_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM accused WHERE person_id IS NULL")
            live_unlinked = cur.fetchone()[0]
    finally:
        conn.rollback()
        conn.close()
    assert gaps["accused.person_id IS NULL"].detail["count"] == live_unlinked, (
        gaps["accused.person_id IS NULL"].detail["count"],
        live_unlinked,
    )
    print(f"       all {len(gaps)} known gap categories visible, unlinked accused={live_unlinked} matches live accused")


def test_no_source_mutation():
    before = _table_count("crimes")
    V2Adapter().discover_new_runs("crimes")
    V2Adapter().get_source_gap_state()
    after = _table_count("crimes")
    assert before == after, (before, after)
    print(f"       crimes row count unchanged: {before}")


if __name__ == "__main__":
    check("Every module's discovered rows match the table's own count (persons gap explained)", test_all_modules_sum_to_table_count)
    sample = check("Changed-records for latest crimes run match a direct query", test_changed_records_match_direct_query)
    check("Source record resolves via get_source_record", lambda: test_source_record_resolves(sample))
    check("Gap state matches live etl_bookkeeping exactly", test_gap_state_matches_known_bookkeeping)
    check("No source-side mutation occurred", test_no_source_mutation)
    print("\nAll Phase 2G V2 live verification checks passed.")
