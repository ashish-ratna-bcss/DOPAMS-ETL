"""One-time read-only KB copy from dev-2 into dopams_cctns_v2.kb.

Fail-closed:
  - source session is read-only and must land on 'dev-2'
  - destination session must land on 'dopams_cctns_v2'
  - never writes to cctns_v1, cctns-v2, dopams_cctns, or dev-2

Usage (from repo root):
  python etl3/migrations/migrate_kb_from_dev2.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import psycopg2
from dotenv import dotenv_values
from psycopg2.extras import execute_values

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from etl3.config import settings  # noqa: E402
from etl3.db import connections  # noqa: E402
from etl3.migrations.run_migration import already_applied, apply_migration, ensure_migrations_table

SOURCE_DB = "dev-2"
TARGET_DB = "dopams_cctns_v2"
SCHEMA_MIGRATION = "013_kb_schema.sql"

TABLES = (
    {
        "name": "drug_categories",
        "columns": (
            "id", "raw_name", "standard_name", "category_group",
            "is_verified", "created_at", "raw_name_clean",
        ),
        "identity": True,
    },
    {
        "name": "drug_ignore_list",
        "columns": ("id", "term", "reason", "created_at"),
        "identity": True,
    },
    {
        "name": "geo_reference",
        "columns": (
            "id", "state_code", "state_name", "district_code", "district_name",
            "sub_district_code", "sub_district_name", "village_code",
            "village_version", "village_name_english", "village_name_local",
            "village_category", "village_status", "created_at",
        ),
        "identity": True,
    },
    {
        "name": "geo_countries",
        "columns": ("country_name", "state_name", "timezone"),
        "identity": False,
    },
)


class SafetyError(RuntimeError):
    pass


def _source_conn():
    """Read-only connection to dev-2 using the same host credentials as V2."""
    e = dotenv_values(settings.V2_SOURCE_ENV_PATH)
    conn = psycopg2.connect(
        host=e["POSTGRES_HOST"],
        port=e["POSTGRES_PORT"],
        dbname=SOURCE_DB,
        user=e["POSTGRES_USER"],
        password=e["POSTGRES_PASSWORD"],
        connect_timeout=30,
        application_name="etl3_kb_migrate_source",
        options="-c default_transaction_read_only=on -c statement_timeout=0",
    )
    conn.set_session(readonly=True, autocommit=False)
    with conn.cursor() as cur:
        cur.execute("SELECT current_database(), current_setting('transaction_read_only')")
        db, ro = cur.fetchone()
    if db != SOURCE_DB:
        conn.close()
        raise SafetyError(f"Source expected {SOURCE_DB!r}, landed on {db!r}")
    if ro not in ("on", "true", "True"):
        conn.close()
        raise SafetyError("Source session is not read-only")
    return conn


def _target_conn():
    if settings.EXPECTED_UNIFIED_DBNAME != TARGET_DB:
        raise SafetyError(
            f"UNIFIED_PG_DATABASE / EXPECTED_UNIFIED_DBNAME must be {TARGET_DB!r}, "
            f"got {settings.EXPECTED_UNIFIED_DBNAME!r}"
        )
    conn = connections.get_unified_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT current_database()")
        db = cur.fetchone()[0]
    if db != TARGET_DB:
        conn.close()
        raise SafetyError(f"Target expected {TARGET_DB!r}, landed on {db!r}")
    return conn


def _count(conn, schema, table):
    with conn.cursor() as cur:
        cur.execute(f'SELECT COUNT(*) FROM "{schema}"."{table}"')
        return cur.fetchone()[0]


def _ensure_schema(target):
    ensure_migrations_table(target)
    if not already_applied(target, SCHEMA_MIGRATION):
        target.close()
        apply_migration(SCHEMA_MIGRATION)
        return _target_conn()
    return target


def _copy_table(source, target, spec):
    name = spec["name"]
    cols = spec["columns"]
    col_sql = ", ".join(cols)
    with source.cursor() as scur:
        scur.execute(f"SELECT {col_sql} FROM public.{name}")
        rows = scur.fetchall()
    with target.cursor() as tcur:
        tcur.execute(f"SELECT COUNT(*) FROM kb.{name}")
        existing = tcur.fetchone()[0]
        if existing:
            raise SafetyError(
                f"kb.{name} already has {existing} rows; refusing to overwrite. "
                "Truncate kb.* manually only if a deliberate re-load is required."
            )
        execute_values(
            tcur,
            f"INSERT INTO kb.{name} ({col_sql}) VALUES %s",
            rows,
            page_size=1000,
        )
        if spec["identity"]:
            tcur.execute(
                f"""
                SELECT setval(
                    pg_get_serial_sequence('kb.{name}', 'id'),
                    COALESCE((SELECT MAX(id) FROM kb.{name}), 1),
                    true
                )
                """
            )
    return len(rows)


def validate(source, target):
    report = []
    for spec in TABLES:
        name = spec["name"]
        src = _count(source, "public", name)
        dst = _count(target, "kb", name)
        report.append((name, src, dst, dst - src))
    # isolation: no public KB tables
    with target.cursor() as cur:
        cur.execute(
            """
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_name IN (
                'drug_categories','drug_ignore_list','geo_reference','geo_countries'
            )
            ORDER BY 1,2
            """
        )
        locations = cur.fetchall()
    return report, locations


def main():
    source = _source_conn()
    target = _target_conn()
    try:
        target = _ensure_schema(target)
        copied = {}
        for spec in TABLES:
            n = _copy_table(source, target, spec)
            copied[spec["name"]] = n
            print(f"[OK] kb.{spec['name']}: inserted {n}")
        target.commit()
        report, locations = validate(source, target)
        print("\nValidation (dev-2 public vs dopams_cctns_v2.kb):")
        print(f"{'Table':22} {'dev-2':>10} {'v2.kb':>10} {'diff':>8}")
        ok = True
        for name, src, dst, diff in report:
            print(f"{name:22} {src:10d} {dst:10d} {diff:8d}")
            if diff != 0:
                ok = False
        print("\nKB table locations on target:")
        for schema, table in locations:
            print(f"  {schema}.{table}")
            if schema != "kb":
                ok = False
        if not ok:
            raise SafetyError("Validation failed")
        print("\n[OK] KB migration complete. Runtime must use kb.* only.")
        print("Copied:", copied)
    except Exception:
        target.rollback()
        raise
    finally:
        source.close()
        target.close()


if __name__ == "__main__":
    main()
