"""
Phase 2H safety tests. Run with: python etl3/tests/test_adapter_safety.py

These prove, don't just assert by code review, the invariants Phase 2's
validation gate requires:
  - a V1 connection configured with the wrong expected database name fails
  - a write attempted through a source connection fails (Postgres-enforced)
  - the unified connection still resolves to dopams_cctns
  - neither adapter module references a write-capable unified connection
    anywhere in its source code
  - V1 and V2 adapters do not import or depend on each other
"""
import ast
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.db import connections
from etl3.sources.v1 import adapter as v1_adapter_module
from etl3.sources.v2 import adapter as v2_adapter_module
from etl3.sources.v1.adapter import V1Adapter
from etl3.sources.v2.adapter import V2Adapter


def check(label, fn):
    try:
        fn()
        print(f"[PASS] {label}")
    except Exception as e:
        print(f"[FAIL] {label}: {type(e).__name__}: {e}")
        raise


def test_wrong_database_name_fails():
    import psycopg2
    from dotenv import dotenv_values

    env = dotenv_values(connections.settings.V1_SOURCE_ENV_PATH)
    conn = psycopg2.connect(
        host=env["PG_HOST"], port=env["PG_PORT"], dbname=env["PG_DATABASE"],
        user=env["PG_USER"], password=env["PG_PASSWORD"], connect_timeout=15,
    )
    try:
        connections._assert_database(conn, "cctns-v2")  # deliberately wrong
        raise AssertionError("expected WrongDatabaseError, none was raised")
    except connections.WrongDatabaseError:
        pass  # expected; connection already closed by _assert_database


def test_v1_write_attempt_fails():
    conn = connections.get_v1_source_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE etl3_write_probe_v1 (x int)")
        raise AssertionError("write succeeded against V1 -- READ-ONLY ENFORCEMENT FAILED")
    except Exception as e:
        if "read-only" not in str(e).lower():
            raise
    finally:
        conn.rollback()
        conn.close()


def test_v2_write_attempt_fails():
    conn = connections.get_v2_source_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE etl3_write_probe_v2 (x int)")
        raise AssertionError("write succeeded against V2 -- READ-ONLY ENFORCEMENT FAILED")
    except Exception as e:
        if "read-only" not in str(e).lower():
            raise
    finally:
        conn.rollback()
        conn.close()


def test_unified_connection_resolves_correctly():
    conn = connections.get_unified_connection(readonly=True)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database()")
            db = cur.fetchone()[0]
            assert db == "dopams_cctns", db
    finally:
        conn.rollback()
        conn.close()


def test_adapters_never_reference_unified_connection():
    """Static source-code check: neither adapter module should ever call
    get_unified_connection -- a source adapter has no legitimate reason to
    touch the destination database at all."""
    for mod, name in ((v1_adapter_module, "V1"), (v2_adapter_module, "V2")):
        src = inspect.getsource(mod)
        tree = ast.parse(src)
        calls = [
            n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", None)
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
        ]
        assert "get_unified_connection" not in calls, (
            f"{name} adapter source references get_unified_connection() -- "
            "a source adapter must never be able to open a write connection"
        )


def _imported_module_names(mod):
    tree = ast.parse(inspect.getsource(mod))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_adapters_do_not_depend_on_each_other():
    """V1 and V2 adapters must not IMPORT each other or share mutable state --
    ETL-3 must not make the two source systems operationally dependent. This
    checks actual import statements via the AST, not raw text, since the
    adapters' own docstrings legitimately mention the other source by name
    for comparison (e.g. 'unlike V1, V2 carries etl_run_id directly')."""
    v1_imports = _imported_module_names(v1_adapter_module)
    v2_imports = _imported_module_names(v2_adapter_module)
    assert not any("sources.v2" in m or m.endswith(".v2") for m in v1_imports), v1_imports
    assert not any("sources.v1" in m or m.endswith(".v1") for m in v2_imports), v2_imports
    # Independently instantiable, no shared constructor state
    a1 = V1Adapter()
    a2 = V2Adapter()
    assert a1.source_system == "V1" and a2.source_system == "V2"


if __name__ == "__main__":
    check("Wrong expected database name is rejected", test_wrong_database_name_fails)
    check("Write attempt through V1 adapter connection fails", test_v1_write_attempt_fails)
    check("Write attempt through V2 adapter connection fails", test_v2_write_attempt_fails)
    check("Unified connection still resolves to dopams_cctns", test_unified_connection_resolves_correctly)
    check("Neither adapter can reach a write-capable unified connection", test_adapters_never_reference_unified_connection)
    check("V1 and V2 adapters have no cross-dependency", test_adapters_do_not_depend_on_each_other)
    print("\nAll Phase 2H safety checks passed.")
