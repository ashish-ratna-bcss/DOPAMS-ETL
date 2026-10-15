"""Disposable-database tests for the daily-pipeline activation gate
(etl3/run_daily_pipeline.py: check_activation_gate and its sub-checks).
Never touches dopams_cctns/dopams_cctns_v2.

Run: python etl3/tests/test_daily_pipeline_gate.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import psycopg2
from dotenv import dotenv_values

ENV_PATH = Path(__file__).resolve().parent.parent / "config" / ".env"
_e = dotenv_values(ENV_PATH)
HOST, PORT, USER, PASSWORD = _e["UNIFIED_PG_HOST"], _e["UNIFIED_PG_PORT"], _e["UNIFIED_PG_USER"], _e["UNIFIED_PG_PASSWORD"]

TEST_DB = "etl3_daily_gate_validation"
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


def raw_conn():
    return psycopg2.connect(host=HOST, port=PORT, dbname=TEST_DB, user=USER, password=PASSWORD,
                            connect_timeout=15, application_name="etl3_unified")


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

    conn = raw_conn()
    with conn.cursor() as cur:
        cur.execute(SCHEMA_FILE.read_text(encoding="utf-8"))
    for mig in MIGRATIONS:
        with conn.cursor() as cur:
            cur.execute(mig.read_text(encoding="utf-8"))
    conn.commit()
    print(f"applied schema + {len(MIGRATIONS)} migrations\n")

    import etl3.run_daily_pipeline as daily

    # --- Empty database: no eligible AI work at all -> backlog is vacuously complete ---
    with conn.cursor() as cur:
        ai_status = daily.check_ai_backfill_status(conn)
    check("empty DB: zero eligible AI work reports complete=True (vacuous, not fabricated)", ai_status["complete"] is True, ai_status)

    conflict = daily.check_no_conflicting_process(conn)
    check("no other etl3_unified connection present -> clear=True", conflict["clear"] is True, conflict)

    # --- Seed one eligible-but-unsettled crime for both drug and accused AI.
    # _brief_facts()/_load_accused_by_crime() read crimes_source/accused_source
    # (the raw observation log), not crimes_unified/accused_unified directly --
    # both must be seeded for this crime to register as AI-eligible at all.
    import json as _json
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO crimes_unified (crime_id, source_system, source_record_id, fir_reg_num, "
            "brief_facts, current_source_run_id, current_as_of) "
            "VALUES ('C1','V1','C1','C1','Some brief facts long enough to count as real text here.','r1', now())",
        )
        cur.execute(
            "INSERT INTO crimes_source (source_system, source_table, source_record_id, source_run_id, "
            "source_created_at, source_modified_at, payload, consolidation_run_id) "
            "VALUES ('V1','cctns_fir','C1','r1', now(), now(), %s, '11111111-1111-1111-1111-111111111111')",
            (_json.dumps({"fir_reg_num": "C1", "fir_contents": "Some brief facts long enough to count as real text here."}),),
        )
        cur.execute(
            "INSERT INTO persons_unified (person_id, source_system, source_record_id, full_name, "
            "current_source_run_id, current_as_of) VALUES ('P1','V1','P1','Ravi Kumar','r1', now())",
        )
        cur.execute(
            "INSERT INTO accused_unified (accused_id, source_system, source_record_id, crime_id, "
            "person_id, unlinked_person_flag, current_source_run_id, current_as_of) "
            "VALUES ('A1','V1','A1','C1','P1', FALSE, 'r1', now())",
        )
        cur.execute(
            "INSERT INTO accused_source (source_system, source_table, source_record_id, source_run_id, "
            "source_created_at, source_modified_at, payload, consolidation_run_id) "
            "VALUES ('V1','cctns_accused','A1','r1', now(), now(), %s, '11111111-1111-1111-1111-111111111111')",
            (_json.dumps({"fir_reg_num": "C1", "accused_status": "UI"}),),
        )
    conn.commit()

    ai_status2 = daily.check_ai_backfill_status(conn)
    check("one eligible, unsettled crime -> complete=False with pending>0", ai_status2["complete"] is False and ai_status2["accused_pending"] >= 1, ai_status2)

    gate = daily.check_activation_gate_using(conn)
    check("gate is NOT ready while AI backlog is pending", gate["ready"] is False, gate)
    check("gate names the AI backlog as a blocker", any("AI backfill incomplete" in b for b in gate["blockers"]), gate["blockers"])

    # --- Simulate settlement: record a successful AI attempt matching the digest ---
    from etl3.enrichment.runner import _accused_input_digest, _brief_facts, _load_accused_by_crime
    from etl3.enrichment.accused_facts import build_roster_for_prompt
    from etl3.enrichment.persist import record_ai_attempt

    facts = _brief_facts(conn)
    by_crime = _load_accused_by_crime(conn)
    roster = build_roster_for_prompt(by_crime["C1"])
    digest = _accused_input_digest(facts["C1"][1], roster)
    record_ai_attempt(conn, "C1", digest, "test-model", "empty", 1, None, source_system="V1", source_module="fir")
    conn.commit()

    ai_status3 = daily.check_ai_backfill_status(conn)
    check("after settlement, accused_pending drops to 0 for this crime", ai_status3["accused_pending"] == 0, ai_status3)

    # --- Simulate a conflicting live connection ---
    other = raw_conn()
    try:
        with other.cursor() as cur:
            cur.execute("SELECT pg_sleep(0)")  # ensure it's registered in pg_stat_activity
        conflict2 = daily.check_no_conflicting_process(conn)
        check("a second etl3_unified connection is detected as a conflict", conflict2["clear"] is False, conflict2)
    finally:
        other.close()

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
