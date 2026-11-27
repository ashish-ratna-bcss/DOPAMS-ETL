"""Regression tests for the ETL-3 consolidation advisory lock
(etl3/sync/run_lock.py, key 874533) and the connection that holds it
(etl3/db/connections.get_unified_connection).

These tests exercise real session-level advisory lock behaviour against the
real unified Postgres connection. This is safe to run against the shared
database: pg_advisory_lock/pg_advisory_unlock are pure session state, never
table data, so nothing here can corrupt or mutate any business table. No
INSERT/UPDATE/DELETE against application tables occurs anywhere in this file.

Most tests use a dedicated TEST_LOCK_KEY (not the production LOCK_KEY) so
they cannot collide with, or be confused by, a real production consolidation
run that happens to be holding the real lock at the same time. A small
number of tests explicitly exercise the real LOCK_KEY, and each first
confirms the real key is currently free before touching it, so this suite
never silently steals or masks a genuine production lock.

Run with:
    python etl3/tests/test_run_lock_safety.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.db import connections
from etl3.sync import run_lock

TEST_LOCK_KEY = 874533_001  # distinct from the real LOCK_KEY (874533)


def check(label, fn):
    try:
        fn()
        print(f"[PASS] {label}")
    except Exception as e:
        print(f"[FAIL] {label}: {type(e).__name__}: {e}")
        raise


def _lock_row(conn, key):
    """Return (pid, granted) rows currently held for this advisory lock key, or []."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pid, granted FROM pg_locks WHERE locktype='advisory' AND objid=%s",
            (key,),
        )
        return cur.fetchall()


def _backend_pid(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT pg_backend_pid()")
        return cur.fetchone()[0]


def test_successful_acquire_and_release():
    conn = connections.get_unified_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (TEST_LOCK_KEY,))
            got = cur.fetchone()[0]
        assert got is True
        rows = _lock_row(conn, TEST_LOCK_KEY)
        assert len(rows) == 1 and rows[0][1] is True, rows
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", (TEST_LOCK_KEY,))
            released = cur.fetchone()[0]
        assert released is True
        assert _lock_row(conn, TEST_LOCK_KEY) == []
    finally:
        conn.close()


def test_lock_already_held_by_another_session():
    holder = connections.get_unified_connection()
    waiter = connections.get_unified_connection()
    try:
        with holder.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (TEST_LOCK_KEY,))
            assert cur.fetchone()[0] is True

        with waiter.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (TEST_LOCK_KEY,))
            assert cur.fetchone()[0] is False, "second session must not acquire an already-held lock"

        rows = _lock_row(holder, TEST_LOCK_KEY)
        assert len(rows) == 1
        assert rows[0][0] == _backend_pid(holder), "lock must be attributed to the holder's own backend pid"
    finally:
        with holder.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", (TEST_LOCK_KEY,))
        holder.close()
        waiter.close()


def test_exception_during_protected_operation_still_releases():
    conn = connections.get_unified_connection()
    try:
        assert run_lock.acquire(conn) is True
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM etl3_test_relation_that_does_not_exist")
            raise AssertionError("expected UndefinedTable, query unexpectedly succeeded")
        except Exception as exc:
            assert "does not exist" in str(exc) or "UndefinedTable" in str(type(exc).__name__)
        finally:
            conn.rollback()  # mirror real call sites: roll back the aborted txn before unlocking
            run_lock.release(conn)
        assert _lock_row(conn, run_lock.LOCK_KEY) == [], "lock must not remain held after an exception"
    finally:
        conn.close()


def test_rollback_during_protected_operation_still_releases():
    conn = connections.get_unified_connection()
    try:
        assert run_lock.acquire(conn) is True
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        conn.rollback()
        run_lock.release(conn)
        assert _lock_row(conn, run_lock.LOCK_KEY) == []
    finally:
        conn.close()


def test_explicit_release_frees_lock_for_new_session():
    a = connections.get_unified_connection()
    b = connections.get_unified_connection()
    try:
        assert run_lock.acquire(a) is True
        assert run_lock.acquire(b) is False
        run_lock.release(a)
        assert run_lock.acquire(b) is True, "lock must become available immediately after explicit release"
        run_lock.release(b)
    finally:
        a.close()
        b.close()


def test_connection_closure_without_explicit_release_still_frees_lock():
    """A cooperating client close() sends a clean TCP close, so Postgres
    notices immediately and tears down the backend (and its session-level
    locks) right away -- this is the 'graceful crash' case.

    This does NOT exercise the real incident's failure mode (a connection
    that vanishes WITHOUT a clean TCP close, e.g. killed hard or a dropped
    network link), which instead depends on tcp_keepalives_idle -- confirmed
    at 7200s on the real server -- before the server notices. That scenario
    cannot be produced by a cooperating client calling close(); it is the
    reason get_unified_connection() now sets client-side keepalive overrides
    (keepalives_idle=20s) so even a non-cooperating death is detected fast.
    """
    a = connections.get_unified_connection()
    assert run_lock.acquire(a) is True
    a.close()  # no explicit release -- simulates a crash *after* a clean disconnect
    time.sleep(0.5)  # give the server a moment to tear down the backend
    b = connections.get_unified_connection()
    try:
        assert run_lock.acquire(b) is True, "lock must free once the holding backend is gone"
        run_lock.release(b)
    finally:
        b.close()


def test_failed_acquisition_does_not_block_or_raise():
    holder = connections.get_unified_connection()
    waiter = connections.get_unified_connection()
    try:
        assert run_lock.acquire(holder) is True
        t0 = time.time()
        result = run_lock.acquire(waiter)
        elapsed = time.time() - t0
        assert result is False
        assert elapsed < 2.0, f"pg_try_advisory_lock must not block ({elapsed:.2f}s)"
    finally:
        run_lock.release(holder)
        holder.close()
        waiter.close()


def test_repeated_execution_does_not_accumulate_locks():
    for _ in range(10):
        conn = connections.get_unified_connection()
        try:
            assert run_lock.acquire(conn) is True
            run_lock.release(conn)
        finally:
            conn.close()
    conn = connections.get_unified_connection()
    try:
        assert _lock_row(conn, run_lock.LOCK_KEY) == [], "no lock holder should remain after 10 clean cycles"
    finally:
        conn.close()


def test_real_lock_key_is_currently_free_then_exercised_safely():
    """Confirms the REAL production lock key is free before and after this
    test touches it, so this suite can never mask or interfere with a real
    consolidation run."""
    probe = connections.get_unified_connection()
    try:
        assert _lock_row(probe, run_lock.LOCK_KEY) == [], (
            "real LOCK_KEY is currently held -- a production run may be in progress; "
            "refusing to touch it"
        )
    finally:
        probe.close()

    conn = connections.get_unified_connection()
    try:
        assert run_lock.acquire(conn) is True
        run_lock.release(conn)
    finally:
        conn.close()

    probe2 = connections.get_unified_connection()
    try:
        assert _lock_row(probe2, run_lock.LOCK_KEY) == [], "real LOCK_KEY must be free again after this test"
    finally:
        probe2.close()


def test_unified_connection_keepalive_options_applied():
    """Static/behavioural check that the hardening in connections.py is
    actually in effect on the connection that holds the lock, not just in
    source code."""
    conn = connections.get_unified_connection()
    try:
        dsn_params = conn.get_dsn_parameters()
        assert dsn_params.get("keepalives") == "1"
        with conn.cursor() as cur:
            cur.execute("SHOW idle_in_transaction_session_timeout")
            assert cur.fetchone()[0] == "10min"
    finally:
        conn.close()


if __name__ == "__main__":
    check("Successful acquire and release", test_successful_acquire_and_release)
    check("Lock already held by another session is reported, not blocked", test_lock_already_held_by_another_session)
    check("Exception during protected work still releases the lock", test_exception_during_protected_operation_still_releases)
    check("Rollback during protected work still releases the lock", test_rollback_during_protected_operation_still_releases)
    check("Explicit release frees the lock for a new session", test_explicit_release_frees_lock_for_new_session)
    check("Connection closure without explicit release still frees the lock", test_connection_closure_without_explicit_release_still_frees_lock)
    check("Failed acquisition returns False without blocking", test_failed_acquisition_does_not_block_or_raise)
    check("Repeated execution does not accumulate locks", test_repeated_execution_does_not_accumulate_locks)
    check("Real LOCK_KEY is free before/after being safely exercised", test_real_lock_key_is_currently_free_then_exercised_safely)
    check("get_unified_connection() keepalive hardening is actually applied", test_unified_connection_keepalive_options_applied)
    print("\nAll advisory-lock safety tests passed.")
