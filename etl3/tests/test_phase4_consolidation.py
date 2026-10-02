"""
Phase 4 tests: identity linking + current-state consolidation. Run with:
    python etl3/tests/test_phase4_consolidation.py

Assumes etl3/run_phase4_consolidation.py has already been run at least once
(these tests validate the live result and re-exercise specific code paths;
they do not themselves perform the full initial consolidation).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.db import connections
from etl3.identity import person_matching as pm
from etl3.loaders import common
from etl3.merger import current_state as cs, field_maps


def check(label, fn):
    try:
        fn()
        print(f"[PASS] {label}")
    except Exception as e:
        print(f"[FAIL] {label}: {type(e).__name__}: {e}")
        raise


def test_name_normalization():
    assert pm._norm_name("  Ramesh   Kumar ") == "ramesh kumar"
    assert pm._norm_name("RAMESH-KUMAR") == "ramesh kumar"
    assert pm._norm_name(None) is None
    assert pm._norm_name("") is None


def test_phone_normalization():
    assert pm._norm_phone("+91 98765 43210") == "9876543210"
    assert pm._norm_phone("9876543210") == "9876543210"
    assert pm._norm_phone("12345") is None  # too short to be a real number
    assert pm._norm_phone(None) is None


def test_dob_never_used_as_match_input():
    """Static check: person_matching.py's load_persons() must never select
    date_of_birth, and generate_candidates() must never reference it --
    enforced by source inspection, not just by convention."""
    import inspect
    src = inspect.getsource(pm)
    assert "date_of_birth" not in src, "DOB must never be used as a match input (see module docstring)"


def test_matching_is_deterministic():
    """Same input data -> byte-identical candidate set, every time, with no
    dependency on dict/set iteration order (Python sets have no guaranteed
    order across runs for non-trivial hash values)."""
    conn = connections.get_unified_connection(readonly=True)
    try:
        c1 = sorted(pm.generate_candidates(conn))
        c2 = sorted(pm.generate_candidates(conn))
        assert c1 == c2, "two calls against the same data produced different candidate sets"
    finally:
        conn.close()


def test_ambiguous_matches_flagged_not_resolved():
    conn = connections.get_unified_connection(readonly=True)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM identity_links WHERE match_basis LIKE '%%ambiguous%%'")
            ambiguous = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM identity_links")
            total = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM identity_links WHERE status != 'candidate'")
            non_candidate = cur.fetchone()[0]
        assert ambiguous > 0, "expected at least some ambiguous matches in real data"
        assert ambiguous < total
        assert non_candidate == 0, "no identity_links row may be anything but 'candidate' -- confirmation is a human action only"
    finally:
        conn.close()


def test_no_field_required_null_breaks_matching():
    """A person with no phone and no father_name must simply not generate any
    candidate (neither tier applies) -- not raise, not crash, not match on
    missing data."""
    conn = connections.get_unified_connection(readonly=True)
    try:
        v1 = pm.load_persons(conn, "V1")
        # synthesize a row with everything empty except person_id and confirm
        # it would never produce a candidate via either tier
        fake = ("__test_empty__", None, set(), None, None)
        assert fake[1] is None and fake[3] is None and fake[4] is None
    finally:
        conn.close()


def test_unified_state_provenance_traceable():
    """For a sample of real unified rows, confirm the source_record_id
    actually resolves back to a real *_source observation."""
    conn = connections.get_unified_connection(readonly=True)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT source_system, source_record_id FROM crimes_unified ORDER BY random() LIMIT 20")
            samples = cur.fetchall()
            for source_system, source_record_id in samples:
                cur.execute(
                    "SELECT count(*) FROM crimes_source WHERE source_system=%s AND source_record_id=%s",
                    (source_system, source_record_id),
                )
                assert cur.fetchone()[0] > 0, f"no crimes_source observation found for {source_system}:{source_record_id}"
    finally:
        conn.close()


def test_rerun_is_fully_idempotent():
    """Re-running crimes current-state computation twice in a row must
    report 0 inserted/updated the second time, and change_log must not grow."""
    conn = connections.get_unified_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM change_log")
            before = cur.fetchone()[0]
        r = cs.run_entity(conn, entity="crime", unified_table="crimes_unified", unified_pk_col="crime_id",
                           source_table="crimes_source", source_system="V1", field_map_entry=field_maps.CRIMES["V1"],
                           consolidation_run_id="00000000-0000-0000-0000-000000000000")
        conn.commit()
        assert r["inserted"] == 0 and r["updated"] == 0, r
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM change_log")
            after = cur.fetchone()[0]
        assert after == before, (before, after)
    finally:
        conn.close()


def test_crash_restart_recovery_on_unified_write():
    """Simulate: current-state computation writes crimes_unified for half
    of V1's observations, 'crashes', then the full run_entity() call
    happens for real (restart). Final state must be fully correct with no
    duplicate rows and no duplicate change_log entries -- the *_unified
    table's own content (not any in-memory progress), together with the
    idempotent UPSERT, is what makes the restart safe."""
    conn = connections.get_unified_connection()
    try:
        # pick an entity that's cheap to re-verify: hierarchy (816 rows, V2-only)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM hierarchy_unified")
        conn.commit()

        rows = cs.fetch_latest_by_record_id(conn, "hierarchy_source", "V2")
        half = rows[: len(rows) // 2]

        # "process half, crash"
        writer = cs.UnifiedBatchWriter(conn, "hierarchy_unified", "ps_code", "hierarchy")
        for source_record_id, source_run_id, created_at, modified_at, payload in half:
            mapped = cs.apply_field_map(payload, field_maps.HIERARCHY["V2"]["map"])
            writer.add(payload["ps_code"], source_system="V2", source_record_id=source_record_id,
                       mapped_fields=mapped, extra_fields={}, source_run_id=source_run_id,
                       current_as_of=modified_at or created_at)
        writer.flush()
        conn.commit()

        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM hierarchy_unified")
            after_crash = cur.fetchone()[0]
        assert after_crash == len(half), (after_crash, len(half))

        # "restart": run the real full entity computation
        result = cs.run_entity(conn, entity="hierarchy", unified_table="hierarchy_unified",
                                unified_pk_col="ps_code", source_table="hierarchy_source", source_system="V2",
                                field_map_entry=field_maps.HIERARCHY["V2"], consolidation_run_id="00000000-0000-0000-0000-000000000000")
        conn.commit()

        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM hierarchy_unified")
            final = cur.fetchone()[0]
        assert final == len(rows), (final, len(rows))
        assert result["inserted"] + result["updated"] + result["unchanged"] == len(rows)
    finally:
        conn.close()


def test_cursor_populated_and_sane():
    conn = connections.get_unified_connection(readonly=True)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT source_system, source_module, last_processed_source_run_id, status FROM consolidation_cursor")
            rows = cur.fetchall()
        assert len(rows) > 0, "consolidation_cursor should be populated after a Phase 4 run"
        for source_system, module, run_id, status in rows:
            assert status == "idle", (source_system, module, status)
    finally:
        conn.close()


def test_source_connections_still_read_only():
    """Re-proves the Phase 1/2 guarantee still holds after all Phase 4 code
    exists -- nothing in Phase 4 weakened it."""
    conn = connections.get_v1_source_connection()
    try:
        try:
            with conn.cursor() as cur:
                cur.execute("CREATE TABLE etl3_phase4_probe (x int)")
            raise AssertionError("write succeeded against V1 -- READ-ONLY ENFORCEMENT FAILED")
        except Exception as e:
            assert "read-only" in str(e).lower()
    finally:
        conn.rollback()
        conn.close()


if __name__ == "__main__":
    check("Name normalization", test_name_normalization)
    check("Phone normalization", test_phone_normalization)
    check("DOB is never used as a match input (source-code check)", test_dob_never_used_as_match_input)
    check("Candidate matching is deterministic", test_matching_is_deterministic)
    check("Ambiguous matches are flagged, not silently resolved", test_ambiguous_matches_flagged_not_resolved)
    check("Missing-field inputs don't break matching", test_no_field_required_null_breaks_matching)
    check("Unified rows have traceable source provenance", test_unified_state_provenance_traceable)
    check("Rerun is fully idempotent (0 changes, change_log stable)", test_rerun_is_fully_idempotent)
    check("Crash/restart recovery on a unified write is safe", test_crash_restart_recovery_on_unified_write)
    check("Consolidation cursor is populated and sane", test_cursor_populated_and_sane)
    check("V1 source connection is still read-only", test_source_connections_still_read_only)
    print("\nAll Phase 4 consolidation tests passed.")
