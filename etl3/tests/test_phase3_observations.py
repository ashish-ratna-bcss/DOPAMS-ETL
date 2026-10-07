"""
Phase 3 tests: source-observation layer. Run with:
    python etl3/tests/test_phase3_observations.py

Uses a small, fast module (V2 hierarchy) for the idempotency
and replay simulations so this runs quickly; correctness against the full
dataset was already established via etl3/run_phase3_initial_load.py and
cross-checked against live counts (see PHASE3_SOURCE_OBSERVATION_STATUS.md).
"""
import ast
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.db import connections
from etl3.loaders import common, v1_observations as v1obs, v2_observations as v2obs


def check(label, fn):
    try:
        fn()
        print(f"[PASS] {label}")
    except Exception as e:
        print(f"[FAIL] {label}: {type(e).__name__}: {e}")
        raise


def test_observation_identity_is_deterministic():
    """Same (source_system, source_record_id, source_run_id) must always
    resolve to the same observation -- enforced by the UNIQUE constraint in
    ETL3_UNIFIED_SCHEMA.sql, proven here by calling write_source_observation
    twice with identical identity and different payload, and checking only
    one row exists (the constraint wins, no duplicate, no silent overwrite
    of history since the second write doesn't corrupt the first)."""
    conn = connections.get_unified_connection()
    try:
        inserted_1 = common.write_source_observation(
            conn, "hierarchy_source", source_system="V2", source_table="hierarchy",
            source_record_id="__test_identity_probe__", source_run_id="__test_run__",
            source_created_at=None, source_modified_at=None, source_fetched_at=None,
            payload={"version": 1}, consolidation_run_id="00000000-0000-0000-0000-000000000000",
        )
        inserted_2 = common.write_source_observation(
            conn, "hierarchy_source", source_system="V2", source_table="hierarchy",
            source_record_id="__test_identity_probe__", source_run_id="__test_run__",
            source_created_at=None, source_modified_at=None, source_fetched_at=None,
            payload={"version": 2}, consolidation_run_id="00000000-0000-0000-0000-000000000000",
        )
        assert inserted_1 is True, "first write should insert"
        assert inserted_2 is False, "second write with same identity should be a no-op"
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*), (array_agg(payload))[1] FROM hierarchy_source "
                "WHERE source_record_id = '__test_identity_probe__'"
            )
            count, payload = cur.fetchone()
        assert count == 1, count
        assert payload["version"] == 1, "the ORIGINAL observation must survive, not be silently overwritten"
        conn.commit()
    finally:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM hierarchy_source WHERE source_record_id = '__test_identity_probe__'")
        conn.commit()
        conn.close()


def test_idempotent_rerun_v2():
    """Not asserting a clean-slate 'first run inserts 816' -- this test may
    run against a database where the real Phase 3 initial load (816 rows)
    already happened. What idempotency actually requires: running twice in
    a row never changes the total, and the second of those two runs always
    reports 0 new inserts against whatever the first left behind."""
    conn = connections.get_unified_connection()
    run_id = None
    try:
        run_id = common.start_consolidation_run(conn)
        conn.commit()
        r1 = v2obs.capture_initial(conn, "hierarchy", run_id)
        conn.commit()
        r2 = v2obs.capture_initial(conn, "hierarchy", run_id)
        conn.commit()
        assert r2["inserted"] == 0, r2
        assert r2["already_present"] == r1["inserted"] + r1["already_present"], (r1, r2)
    finally:
        if run_id is not None:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM consolidation_run_log WHERE run_id = %s AND status = 'running'", (run_id,))
            conn.commit()
        conn.close()


def test_idempotent_rerun_v1():
    conn = connections.get_unified_connection()
    run_id = None
    try:
        run_id = common.start_consolidation_run(conn)
        conn.commit()
        r1 = v1obs.capture_initial(conn, "fir", run_id)
        conn.commit()
        r2 = v1obs.capture_initial(conn, "fir", run_id)
        conn.commit()
        # r1 may be 0 inserted if fir was already captured by an earlier run --
        # what matters is r2 always reports 0 new inserts against whatever r1 left behind
        assert r2["inserted"] == 0, r2
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM crimes_source WHERE source_system='V1' AND source_run_id='__initial__'")
            total = cur.fetchone()[0]
        assert total == 7305, total
    finally:
        if run_id is not None:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM consolidation_run_log WHERE run_id = %s AND status = 'running'", (run_id,))
            conn.commit()
        conn.close()


def test_replay_after_simulated_partial_processing():
    """Simulate 'process half, crash, restart': manually write observations
    for half of hierarchy's rows in one transaction and commit (simulating a
    completed partial run), then call capture_initial() again (simulating
    the restart) and confirm it safely completes the rest with no duplicates
    and no errors -- the database, not in-memory state, is what determines
    what's already been observed."""
    conn = connections.get_unified_connection()
    try:
        # Start clean for this specific test run-id to make the simulation unambiguous
        test_run_id = "__replay_test_run__"
        with conn.cursor() as cur:
            cur.execute("DELETE FROM hierarchy_source WHERE source_run_id = %s", (test_run_id,))
        conn.commit()

        from etl3.sources.v2.adapter import V2Adapter
        adapter = V2Adapter()
        all_rows = list(adapter.get_all_current_records("hierarchy"))
        half = all_rows[: len(all_rows) // 2]

        # "crash after 50%"
        for row in half:
            common.write_source_observation(
                conn, "hierarchy_source", source_system="V2", source_table="hierarchy",
                source_record_id=str(row["ps_code"]), source_run_id=test_run_id,
                source_created_at=row.get("date_created"), source_modified_at=row.get("date_modified"),
                source_fetched_at=row.get("fetched_at"), payload=row,
                consolidation_run_id="00000000-0000-0000-0000-000000000000",
            )
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM hierarchy_source WHERE source_run_id = %s", (test_run_id,))
            after_crash = cur.fetchone()[0]
        assert after_crash == len(half), (after_crash, len(half))

        # "restart": process the full set again under the same run id
        inserted_on_restart = 0
        for row in all_rows:
            if common.write_source_observation(
                conn, "hierarchy_source", source_system="V2", source_table="hierarchy",
                source_record_id=str(row["ps_code"]), source_run_id=test_run_id,
                source_created_at=row.get("date_created"), source_modified_at=row.get("date_modified"),
                source_fetched_at=row.get("fetched_at"), payload=row,
                consolidation_run_id="00000000-0000-0000-0000-000000000000",
            ):
                inserted_on_restart += 1
        conn.commit()

        assert inserted_on_restart == len(all_rows) - len(half), inserted_on_restart
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM hierarchy_source WHERE source_run_id = %s", (test_run_id,))
            final_count = cur.fetchone()[0]
        assert final_count == len(all_rows), (final_count, len(all_rows))

        # cleanup
        with conn.cursor() as cur:
            cur.execute("DELETE FROM hierarchy_source WHERE source_run_id = %s", (test_run_id,))
        conn.commit()
    finally:
        conn.close()


def test_observation_writer_never_touches_v1_v2():
    """Static check: etl3/loaders/*.py must never call get_v1_source_connection
    or get_v2_source_connection for writing -- all source reads happen through
    the adapters, and loaders only ever write via get_unified_connection."""
    import etl3.loaders.common as common_mod
    import etl3.loaders.v1_observations as v1_mod
    import etl3.loaders.v2_observations as v2_mod

    for mod in (common_mod, v1_mod, v2_mod):
        src = inspect.getsource(mod)
        tree = ast.parse(src)
        calls = {
            n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", None)
            for n in ast.walk(tree) if isinstance(n, ast.Call)
        }
        assert "get_v1_source_connection" not in calls, f"{mod.__name__} calls get_v1_source_connection directly"
        assert "get_v2_source_connection" not in calls, f"{mod.__name__} calls get_v2_source_connection directly"


def test_v1_v2_loaders_have_no_cross_dependency():
    """V1 and V2 observation loaders must not import each other -- mirrors
    the same check Phase 2 applied to the adapters."""
    import etl3.loaders.v1_observations as v1_mod
    import etl3.loaders.v2_observations as v2_mod

    def imported_names(mod):
        tree = ast.parse(inspect.getsource(mod))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
        return names

    v1_imports = imported_names(v1_mod)
    v2_imports = imported_names(v2_mod)
    assert not any("v2" in m for m in v1_imports), v1_imports
    assert not any(m.endswith(".v1") or "sources.v1" in m for m in v2_imports), v2_imports


if __name__ == "__main__":
    check("Observation identity is deterministic (same identity never duplicates or silently overwrites)", test_observation_identity_is_deterministic)
    check("Idempotent re-run: V2 hierarchy", test_idempotent_rerun_v2)
    check("Idempotent re-run: V1 fir", test_idempotent_rerun_v1)
    check("Replay after simulated partial processing is safe", test_replay_after_simulated_partial_processing)
    check("Loaders never open a V1/V2 connection directly (only via adapters)", test_observation_writer_never_touches_v1_v2)
    check("V1 and V2 loaders have no cross-dependency", test_v1_v2_loaders_have_no_cross_dependency)
    print("\nAll Phase 3 observation-layer tests passed.")
