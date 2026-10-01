"""
Phase 0/1 smoke test for the connection layer. Not pytest -- a standalone
script, consistent with how the rest of this repo runs its own checks.
Run with: python etl3/tests/test_connections.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from etl3.db import connections


def check(label, fn):
    try:
        fn()
        print(f"[PASS] {label}")
    except Exception as e:
        print(f"[FAIL] {label}: {type(e).__name__}: {e}")
        raise


def test_v1_readonly():
    conn = connections.get_v1_source_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database(), current_user")
            db, user = cur.fetchone()
            assert db == "cctns_v1", db
            print(f"       connected to {db!r} as {user!r}")
        # confirm the session actually refuses writes at the Postgres level
        try:
            with conn.cursor() as cur:
                cur.execute("CREATE TABLE etl3_write_probe (x int)")
            raise AssertionError("write did not raise -- READ-ONLY ENFORCEMENT FAILED")
        except Exception as e:
            if "read-only" not in str(e).lower():
                raise
            print("       write correctly rejected by Postgres read-only session")
            conn.rollback()
    finally:
        conn.rollback()
        conn.close()


def test_v2_readonly():
    conn = connections.get_v2_source_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database(), current_user")
            db, user = cur.fetchone()
            assert db == "cctns-v2", db
            print(f"       connected to {db!r} as {user!r}")
        try:
            with conn.cursor() as cur:
                cur.execute("CREATE TABLE etl3_write_probe (x int)")
            raise AssertionError("write did not raise -- READ-ONLY ENFORCEMENT FAILED")
        except Exception as e:
            if "read-only" not in str(e).lower():
                raise
            print("       write correctly rejected by Postgres read-only session")
            conn.rollback()
    finally:
        conn.rollback()
        conn.close()


def test_unified_connection():
    conn = connections.get_unified_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database(), current_user, version()")
            db, user, ver = cur.fetchone()
            assert db == "dopams_cctns", db
            print(f"       connected to {db!r} as {user!r}")
            print(f"       {ver.split(',')[0]}")
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' ORDER BY 1"
            )
            tables = [r[0] for r in cur.fetchall()]
            print(f"       existing tables in public schema: {tables or '(none -- empty, as expected)'}")
    finally:
        conn.rollback()
        conn.close()


def test_safety_check_fires_on_mismatch():
    # Deliberately call the internal assert with the wrong expected name
    # against a real V1 connection, to prove the guard actually raises
    # rather than trusting it by inspection alone.
    e = None
    from dotenv import dotenv_values
    import psycopg2

    env = dotenv_values(connections.settings.V1_SOURCE_ENV_PATH)
    conn = psycopg2.connect(
        host=env["PG_HOST"], port=env["PG_PORT"], dbname=env["PG_DATABASE"],
        user=env["PG_USER"], password=env["PG_PASSWORD"], connect_timeout=15,
    )
    try:
        connections._assert_database(conn, "dopams_cctns")  # intentionally wrong
        raise AssertionError("WrongDatabaseError was not raised on mismatch")
    except connections.WrongDatabaseError as exc:
        print(f"       correctly raised: {exc}")
    finally:
        pass  # _assert_database already closes conn on mismatch


if __name__ == "__main__":
    check("V1 source connection is read-only and points at cctns_v1", test_v1_readonly)
    check("V2 source connection is read-only and points at cctns-v2", test_v2_readonly)
    check("Unified connection points at dopams_cctns", test_unified_connection)
    check("Database-mismatch safety check fires correctly", test_safety_check_fires_on_mismatch)
    print("\nAll Phase 0/1 connection checks passed.")
