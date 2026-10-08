"""Reset dopams_cctns_v2 public schema to empty ETL-3 DDL; keep kb.* intact.

Fail-closed: refuses any database other than dopams_cctns_v2.
Does not touch cctns_v1, cctns-v2, dev-2, or dopams_cctns.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from etl3.config import settings
from etl3.db import connections
from etl3.migrations.run_migration import apply_migration, already_applied, ensure_migrations_table

TARGET = "dopams_cctns_v2"
MIGRATIONS = [
    "001_initial_schema.sql",
    "002_arrests_accused_id_nullable.sql",
    "003_current_as_of_nullable.sql",
    "004_reconciliation_and_chargesheet_keys.sql",
    "005_chargesheet_update_module.sql",
    "006_backend_read_contract.sql",
    "007_enrichment.sql",
    "008_enrichment_cascade.sql",
    "009_v1_station_unit_code.sql",
    "010_person_address_resolution.sql",
    "011_person_name_cleanup.sql",
    "012_accused_brief_facts_enrichment.sql",
    "013_kb_schema.sql",
    "014_accused_status_width.sql",
]


def main():
    if settings.EXPECTED_UNIFIED_DBNAME != TARGET:
        raise SystemExit(f"Refusing: UNIFIED_PG_DATABASE must be {TARGET!r}")
    conn = connections.get_unified_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database()")
            db = cur.fetchone()[0]
            if db != TARGET:
                raise SystemExit(f"Refusing: landed on {db!r}")
            cur.execute(
                """
                SELECT nspname FROM pg_namespace
                WHERE nspname NOT IN ('pg_catalog','information_schema','pg_toast')
                  AND nspname NOT LIKE 'pg_temp%'
                  AND nspname NOT LIKE 'pg_toast_temp%'
                ORDER BY 1
                """
            )
            schemas = [r[0] for r in cur.fetchall()]
            print("schemas before:", schemas)
            # Preserve kb only.
            for schema in schemas:
                if schema == "kb":
                    continue
                cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
                print(f"dropped schema {schema}")
            cur.execute("CREATE SCHEMA IF NOT EXISTS public")
            cur.execute("GRANT ALL ON SCHEMA public TO public")
            cur.execute("SELECT COUNT(*) FROM kb.drug_categories")
            drugs = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM kb.geo_reference")
            geo = cur.fetchone()[0]
        conn.commit()
        print(f"kb preserved: drug_categories={drugs}, geo_reference={geo}")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    # Re-apply empty public DDL. 013 is idempotent (IF NOT EXISTS) and leaves kb data.
    for name in MIGRATIONS:
        apply_migration(name)

    conn = connections.get_unified_connection(readonly=True)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_schema, COUNT(*)
                FROM information_schema.tables
                WHERE table_schema IN ('public','kb') AND table_type='BASE TABLE'
                GROUP BY 1 ORDER BY 1
                """
            )
            print("tables after:", cur.fetchall())
            cur.execute("SELECT COUNT(*) FROM public.crimes_unified")
            print("crimes_unified rows:", cur.fetchone()[0])
            cur.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema='public'
                  AND table_name IN (
                    'drug_categories','drug_ignore_list','geo_reference','geo_countries'
                  )
                """
            )
            assert cur.fetchone() is None, "KB leaked into public"
    finally:
        conn.close()
    print("[OK] public reset; kb kept")


if __name__ == "__main__":
    main()
