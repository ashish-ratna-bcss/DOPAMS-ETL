"""
Phase 2G live verification for the V1 adapter. Run with:
    python etl3/tests/test_v1_adapter.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.db import connections
from etl3.sources.v1.adapter import V1Adapter, SUCCESS_STATUSES


def check(label, fn):
    try:
        result = fn()
        print(f"[PASS] {label}")
        return result
    except Exception as e:
        print(f"[FAIL] {label}: {type(e).__name__}: {e}")
        raise


def _source_side_counts():
    """Raw counts queried independently of the adapter, to cross-check against."""
    conn = connections.get_v1_source_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM cctns.cctns_v1_etl_run_log")
            run_log_count = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM cctns.cctns_v1_etl_row_action")
            row_action_count = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM cctns.cctns_v1_failed_fetch_window")
            gap_count = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM cctns.cctns_v1_etl_run_log WHERE status='running'")
            running_count = cur.fetchone()[0]
        return run_log_count, row_action_count, gap_count, running_count
    finally:
        conn.rollback()
        conn.close()


def test_discover_latest_run_and_metadata():
    a = V1Adapter()
    runs = a.discover_new_runs("accused")
    assert len(runs) > 0, "expected at least one successful accused run"
    latest = runs[-1]
    assert latest.source_system == "V1"
    assert latest.status in SUCCESS_STATUSES
    print(f"       latest successful accused run: {latest.source_run_id} "
          f"status={latest.status} row_count={latest.row_count}")
    return latest


def test_changed_records_match_row_action(latest_run):
    a = V1Adapter()
    recs = a.get_changed_records("accused", latest_run.source_run_id)
    conn = connections.get_v1_source_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM cctns.cctns_v1_etl_row_action "
                "WHERE run_id = %s AND table_name = 'cctns_accused'",
                (latest_run.source_run_id,),
            )
            direct_count = cur.fetchone()[0]
    finally:
        conn.rollback()
        conn.close()
    assert len(recs) == direct_count, (len(recs), direct_count)
    print(f"       adapter returned {len(recs)} records, matches direct row_action count exactly")


def test_fir_record_resolves():
    a = V1Adapter()
    conn = connections.get_v1_source_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT fir_reg_num FROM cctns.cctns_fir LIMIT 1")
            sample = cur.fetchone()[0]
    finally:
        conn.rollback()
        conn.close()
    row = a.get_source_record("fir", sample)
    assert row is not None and row["fir_reg_num"] == sample
    print(f"       resolved fir_reg_num={sample} -> {len(row)} columns")


def test_non_fir_record_resolution_explicitly_unsupported():
    a = V1Adapter()
    try:
        a.get_source_record("accused", "anything")
        raise AssertionError("expected NotImplementedError for module='accused'")
    except NotImplementedError:
        print("       correctly raises NotImplementedError (record_key is a composite key, not a PK, for this module)")


def test_gap_state_matches_known_ledger():
    a = V1Adapter()
    gaps = a.get_source_gap_state()
    _, _, gap_count, _ = _source_side_counts()
    assert len(gaps) == gap_count, (len(gaps), gap_count)
    open_count = sum(1 for g in gaps if g.status == "OPEN")
    print(f"       {len(gaps)} gap entries visible, {open_count} OPEN (matches cctns_v1_failed_fetch_window exactly)")


def test_no_source_mutation():
    before = _source_side_counts()
    V1Adapter().discover_new_runs("accused")
    V1Adapter().get_source_gap_state()
    after = _source_side_counts()
    assert before == after, (before, after)
    print(f"       run_log/row_action/gap/running counts unchanged: {before}")


if __name__ == "__main__":
    latest = check("Discover latest successful accused run + read metadata", test_discover_latest_run_and_metadata)
    check("Changed-records count matches direct row_action query", lambda: test_changed_records_match_row_action(latest))
    check("FIR record resolves via get_source_record", test_fir_record_resolves)
    check("Non-FIR module resolution correctly unsupported, not silently wrong", test_non_fir_record_resolution_explicitly_unsupported)
    check("Gap state matches the known 180-row ledger exactly", test_gap_state_matches_known_ledger)
    check("No source-side mutation occurred", test_no_source_mutation)
    print("\nAll Phase 2G V1 live verification checks passed.")
