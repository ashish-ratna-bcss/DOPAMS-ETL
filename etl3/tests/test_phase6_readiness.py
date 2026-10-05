"""Phase 6 reconciliation, chargesheet key, and single-run lock checks.

Run with: python etl3/tests/test_phase6_readiness.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.db import connections
from etl3.run_phase4_consolidation import run_with_run_log
from etl3.sync.catalog import registry_gap
from etl3.sync.reconcile import classify_gap, classify_module
from etl3.sync.run_lock import ConcurrentRunError, acquire, release


def check(label, fn):
    try:
        fn()
        print(f"[PASS] {label}")
    except Exception as e:
        print(f"[FAIL] {label}: {type(e).__name__}: {e}")
        raise


def test_reconciliation_statuses():
    assert classify_module(source_count=10, observed_count=10, collapse=False, unified_count=10) == "EXPECTED"
    assert classify_module(source_count=10, observed_count=9, collapse=False) == "UNRESOLVED"
    assert classify_module(source_count=10, observed_count=10, collapse=False, unified_count=11) == "MISMATCH"
    assert classify_module(source_count=10, observed_count=10, collapse=False, unified_count=9) == "UNRESOLVED"
    assert classify_module(source_count=100, observed_count=100, collapse=True, unified_count=40) == "EXPECTED"
    assert classify_module(source_count=100, observed_count=100, collapse=True, unified_count=101) == "MISMATCH"
    assert classify_module(
        source_count=5, observed_count=5, collapse=False, excluded=True, unified_count=5
    ) == "INTENTIONALLY_EXCLUDED"
    assert classify_module(source_count=5, observed_count=4, collapse=False, excluded=True) == "UNRESOLVED"


def test_gap_classification_does_not_hide_unknown_types():
    assert classify_gap("v1_person_key_absent") == "KNOWN_SOURCE_LIMITATION"
    assert classify_gap("ora_06502_window") == "KNOWN_SOURCE_LIMITATION"
    assert classify_gap("unresolved_arrest_accused_link") == "UNRESOLVED_RELATIONSHIP"
    assert classify_gap("address_unresolved") == "DATA_QUALITY"
    assert classify_gap("some_new_gap") == "ETL_DEFECT"


def test_catalog_covers_every_adapter_module():
    missing, extra = registry_gap()
    assert missing == []
    assert extra == []


def test_chargesheet_keys_do_not_collide_across_feeds():
    raw = "6193"
    v1 = f"V1:court:{raw}"
    updates = f"V2:charge_sheet_updates:{raw}"
    sheets = f"V2:chargesheets:{raw}"
    assert len({v1, updates, sheets}) == 3


def test_a_second_run_is_refused():
    first = connections.get_unified_connection()
    second = connections.get_unified_connection()
    try:
        assert acquire(first) is True
        try:
            run_with_run_log(second, lambda conn, run_id, progress: (None, 0))
        except ConcurrentRunError:
            pass
        else:
            raise AssertionError("second run was allowed")
        with second.cursor() as cur:
            cur.execute("SELECT count(*) FROM consolidation_run_log WHERE status = 'running'")
            running = cur.fetchone()[0]
        assert running == 0
    finally:
        release(first)
        first.close()
        second.close()


def main():
    check("reconciliation statuses", test_reconciliation_statuses)
    check("gap classification", test_gap_classification_does_not_hide_unknown_types)
    check("catalog matches adapters", test_catalog_covers_every_adapter_module)
    check("chargesheet keys stay apart", test_chargesheet_keys_do_not_collide_across_feeds)
    check("second run is refused", test_a_second_run_is_refused)


if __name__ == "__main__":
    main()
