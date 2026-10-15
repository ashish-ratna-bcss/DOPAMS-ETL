"""Non-production validation of migration 019 (be_read.person/accused
cleaned-name columns) against a disposable database. Never touches
dopams_cctns/dopams_cctns_v2. Run: python etl3/diagnostics/_validate_backend_contract_fix.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import psycopg2
from dotenv import dotenv_values

ENV_PATH = Path(__file__).resolve().parent.parent / "config" / ".env"
_e = dotenv_values(ENV_PATH)
HOST, PORT, USER, PASSWORD = _e["UNIFIED_PG_HOST"], _e["UNIFIED_PG_PORT"], _e["UNIFIED_PG_USER"], _e["UNIFIED_PG_PASSWORD"]

TEST_DB = "etl3_backend_contract_validation"
FORBIDDEN = {"dopams_cctns", "dopams_cctns_v2", "cctns_v1", "cctns-v2"}
if TEST_DB in FORBIDDEN:
    raise SystemExit("refusing: forbidden db name")

SCHEMA_FILE = Path(__file__).resolve().parent.parent.parent / "dopams_cctns" / "schema" / "ETL3_UNIFIED_SCHEMA.sql"
MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
MIGRATIONS = sorted(
    (p for p in MIGRATIONS_DIR.glob("0*.sql") if not p.name.startswith("001_")),
    key=lambda p: p.name,
)

PASS, FAIL = [], []


def check(label, condition, detail=""):
    if condition:
        PASS.append(label)
        print(f"[PASS] {label}")
    else:
        FAIL.append((label, detail))
        print(f"[FAIL] {label} -- {detail}")


def admin_conn(dbname):
    c = psycopg2.connect(host=HOST, port=PORT, dbname=dbname, user=USER, password=PASSWORD, connect_timeout=15)
    c.autocommit = True
    return c


def main():
    ac = admin_conn("postgres")
    with ac.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB,))
        if cur.fetchone():
            cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s", (TEST_DB,))
            cur.execute(f'DROP DATABASE "{TEST_DB}"')
        cur.execute(f'CREATE DATABASE "{TEST_DB}"')
    ac.close()
    print(f"created disposable {TEST_DB!r}")

    conn = psycopg2.connect(host=HOST, port=PORT, dbname=TEST_DB, user=USER, password=PASSWORD, connect_timeout=15)
    with conn.cursor() as cur:
        cur.execute(SCHEMA_FILE.read_text(encoding="utf-8"))
    for mig in MIGRATIONS:
        with conn.cursor() as cur:
            cur.execute(mig.read_text(encoding="utf-8"))
    conn.commit()
    print(f"applied schema + {len(MIGRATIONS)} migrations (through {MIGRATIONS[-1].name})")

    with conn.cursor() as cur:
        # Person WITH enrichment.
        cur.execute(
            "INSERT INTO persons_unified (person_id, source_system, source_record_id, full_name, "
            "current_source_run_id, current_as_of) VALUES ('P1','V1','P1','China China', 'r1', now())",
        )
        cur.execute(
            "INSERT INTO person_enrichment (person_id, source_system, domicile_method, "
            "cleaned_full_name, input_hash) VALUES ('P1','V1','non_recoverable','China','h1')",
        )
        # Person WITHOUT any enrichment row yet.
        cur.execute(
            "INSERT INTO persons_unified (person_id, source_system, source_record_id, full_name, "
            "current_source_run_id, current_as_of) VALUES ('P2','V1','P2','Ravi Kumar', 'r1', now())",
        )
        cur.execute(
            "INSERT INTO crimes_unified (crime_id, source_system, source_record_id, fir_reg_num, "
            "current_source_run_id, current_as_of) VALUES ('C1','V1','C1','C1','r1', now())",
        )
        cur.execute(
            "INSERT INTO accused_unified (accused_id, source_system, source_record_id, crime_id, "
            "person_id, unlinked_person_flag, current_source_run_id, current_as_of) "
            "VALUES ('A1','V1','A1','C1','P1', FALSE, 'r1', now())",
        )
        cur.execute(
            "INSERT INTO accused_unified (accused_id, source_system, source_record_id, crime_id, "
            "person_id, unlinked_person_flag, current_source_run_id, current_as_of) "
            "VALUES ('A2','V1','A2','C1','P2', FALSE, 'r1', now())",
        )
    conn.commit()

    with conn.cursor() as cur:
        cur.execute("SELECT * FROM be_read.person ORDER BY person_id")
        cols = [d[0] for d in cur.description]
        person_rows = {row[0]: dict(zip(cols, row)) for row in cur.fetchall()}
    print(f"\nbe_read.person columns: {cols}")

    existing_cols = [
        "person_id", "source_system", "source_record_id", "full_name", "alias",
        "relative_name", "gender", "date_of_birth", "age", "occupation", "caste",
        "nationality", "present_address_text", "permanent_address_text",
        "phone_number", "email_id", "current_source_run_id", "current_as_of",
    ]
    check(
        "all pre-existing be_read.person columns preserved, same order, nothing removed",
        cols[:len(existing_cols)] == existing_cols,
        cols,
    )
    check("new cleaned_full_name column present", "cleaned_full_name" in cols, cols)
    check("P1 (has enrichment) shows raw full_name unchanged", person_rows["P1"]["full_name"] == "China China", person_rows["P1"])
    check("P1 now also exposes the cleaned name via the view", person_rows["P1"]["cleaned_full_name"] == "China", person_rows["P1"])
    check(
        "P2 (NO enrichment row yet) still appears via LEFT JOIN, not dropped",
        "P2" in person_rows and person_rows["P2"]["full_name"] == "Ravi Kumar",
        person_rows.get("P2"),
    )
    check("P2's cleaned_full_name is NULL (no enrichment yet), not fabricated", person_rows["P2"]["cleaned_full_name"] is None, person_rows["P2"])

    with conn.cursor() as cur:
        cur.execute("SELECT * FROM be_read.accused ORDER BY accused_id")
        acols = [d[0] for d in cur.description]
        accused_rows = {row[0]: dict(zip(acols, row)) for row in cur.fetchall()}
    print(f"\nbe_read.accused columns: {acols}")
    existing_acols = [
        "accused_id", "source_system", "source_record_id", "crime_id", "person_id",
        "unlinked_person_flag", "accused_code", "accused_status", "is_ccl",
        "full_name", "relative_name", "phone_number", "gender",
        "current_source_run_id", "current_as_of",
    ]
    check(
        "all pre-existing be_read.accused columns preserved, same order",
        acols[:len(existing_acols)] == existing_acols,
        acols,
    )
    check("A1's accused view exposes the cleaned person name", accused_rows["A1"]["cleaned_full_name"] == "China", accused_rows["A1"])
    check("A2 (unenriched person) still appears, cleaned fields NULL", accused_rows["A2"]["full_name"] == "Ravi Kumar" and accused_rows["A2"]["cleaned_full_name"] is None, accused_rows["A2"])

    # Confirm the views are still genuinely read-only (be_read's own trigger).
    with conn.cursor() as cur:
        try:
            cur.execute("UPDATE be_read.person SET full_name = 'x' WHERE person_id = 'P1'")
            write_blocked = False
        except psycopg2.Error:
            write_blocked = True
            conn.rollback()
    check("be_read.person is still write-rejected after CREATE OR REPLACE", write_blocked, "")

    conn.close()
    ac2 = admin_conn("postgres")
    with ac2.cursor() as cur:
        cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s", (TEST_DB,))
        cur.execute(f'DROP DATABASE "{TEST_DB}"')
    ac2.close()
    print(f"\ndropped disposable {TEST_DB!r}")

    print(f"\n==== SUMMARY: PASS={len(PASS)} FAIL={len(FAIL)} ====")
    for label, detail in FAIL:
        print(f"  [FAIL] {label}: {detail}")
    if FAIL:
        raise SystemExit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
