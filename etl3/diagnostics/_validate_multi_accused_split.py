"""Non-production, disposable-database end-to-end validation of the
multi-accused split (etl3/merger/accused_split.py + runner.py integration).

Creates a brand-new database on the SAME Postgres server as the project's
dev UNIFIED_PG_* credentials (etl3/config/.env) -- never the real
dopams_cctns / dopams_cctns_v2 database, and refuses to run if the target
name ever collides with one. Applies the unified schema + migration 018
there, seeds representative scenarios, exercises the real code paths
against the real database, prints before/after evidence, and drops the
database at the end.

Run: python etl3/diagnostics/_validate_multi_accused_split.py
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

# Force the AI path off before anything imports settings/ai.py, so no
# attempt is ever made to reach the real Ollama host during this run.
os.environ["ETL3_AI_ENABLED"] = "0"

import psycopg2
from dotenv import dotenv_values

ENV_PATH = Path(__file__).resolve().parent.parent / "config" / ".env"
_e = dotenv_values(ENV_PATH)
HOST, PORT, USER, PASSWORD = _e["UNIFIED_PG_HOST"], _e["UNIFIED_PG_PORT"], _e["UNIFIED_PG_USER"], _e["UNIFIED_PG_PASSWORD"]

TEST_DB = "etl3_multi_accused_validation"
FORBIDDEN = {"dopams_cctns", "dopams_cctns_v2", "cctns_v1", "cctns-v2"}
if TEST_DB in FORBIDDEN:
    raise SystemExit(f"Refusing to run: {TEST_DB!r} collides with a real project database name.")

SCHEMA_FILE = Path(__file__).resolve().parent.parent.parent / "dopams_cctns" / "schema" / "ETL3_UNIFIED_SCHEMA.sql"
MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
# ETL3_UNIFIED_SCHEMA.sql already reflects migration 001's base tables (its
# own header says it supersedes the original schema.sql the same way 001
# does) -- apply it once, then every INCREMENTAL migration 002..018 on top,
# in numeric order, exactly as a real deployment would. 017 is itself
# still-uncommitted WIP for an unrelated nationality feature, but the
# CURRENT runner.py/project.py code already requires its columns to run
# _person_enrichment at all, so it must be applied here too.
MIGRATIONS = sorted(
    (p for p in MIGRATIONS_DIR.glob("0*.sql") if not p.name.startswith("001_")),
    key=lambda p: p.name,
)

PASS = []
FAIL = []


def check(label, condition, detail=""):
    if condition:
        PASS.append(label)
        print(f"[PASS] {label}")
    else:
        FAIL.append((label, detail))
        print(f"[FAIL] {label} -- {detail}")


def admin_conn(dbname):
    conn = psycopg2.connect(host=HOST, port=PORT, dbname=dbname, user=USER, password=PASSWORD,
                            connect_timeout=15, application_name="etl3_split_validation_admin")
    conn.autocommit = True
    return conn


def test_conn():
    conn = psycopg2.connect(host=HOST, port=PORT, dbname=TEST_DB, user=USER, password=PASSWORD,
                            connect_timeout=15, application_name="etl3_split_validation")
    return conn


def row_counts(conn, tables):
    out = {}
    with conn.cursor() as cur:
        for t in tables:
            cur.execute(f"SELECT count(*) FROM {t}")
            out[t] = cur.fetchone()[0]
    return out


def main():
    print(f"== Connecting to {HOST}:{PORT} as {USER} to create disposable database {TEST_DB!r} ==")
    for maint_db in ("postgres", "dopams_cctns_v2"):
        try:
            ac = admin_conn(maint_db)
            break
        except Exception as exc:
            print(f"  (could not use {maint_db!r} as maintenance db: {exc})")
            ac = None
    if ac is None:
        raise SystemExit("Could not open any maintenance connection to create the test database.")

    with ac.cursor() as cur:
        cur.execute(f"SELECT current_database()")
        print(f"  connected via maintenance db: {cur.fetchone()[0]}")
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB,))
        existed_before = cur.fetchone() is not None
        if existed_before:
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s", (TEST_DB,),
            )
            cur.execute(f'DROP DATABASE "{TEST_DB}"')
            print(f"  dropped pre-existing {TEST_DB!r} from an earlier run")
        cur.execute(f'CREATE DATABASE "{TEST_DB}"')
        print(f"  created {TEST_DB!r}")
    ac.close()

    conn = test_conn()
    conn.autocommit = False
    with conn.cursor() as cur:
        cur.execute("SELECT current_database()")
        actual_db = cur.fetchone()[0]
    check("connected to the disposable test database, not a real one", actual_db == TEST_DB, actual_db)

    print("\n== Applying schema ==")
    with conn.cursor() as cur:
        cur.execute(SCHEMA_FILE.read_text(encoding="utf-8"))
    for mig in MIGRATIONS:
        with conn.cursor() as cur:
            cur.execute(mig.read_text(encoding="utf-8"))
        print(f"  applied {mig.name}")
    conn.commit()

    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name='accused_unified' "
            "AND column_name IN ('derived_from_accused_id','split_segment_index','split_parser_version','split_superseded_at')",
        )
        got = {r[0] for r in cur.fetchall()}
    check(
        "migration 018 added all four accused_unified columns",
        got == {"derived_from_accused_id", "split_segment_index", "split_parser_version", "split_superseded_at"},
        got,
    )
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('accused_split_audit')")
        audit_table_exists = cur.fetchone()[0] is not None
    check("accused_split_audit table created", audit_table_exists, audit_table_exists)

    # ------------------------------------------------------------------
    # Seed scenario 1: a V1-shaped FIR with a 2-person packed accused name,
    # exactly the shape field_maps.PERSONS['V1'] / run_v1_accused_grouped
    # would have produced from a real CCTNS accused_details row.
    # ------------------------------------------------------------------
    print("\n== Seeding scenario 1: 2-person packed V1 accused ==")
    RUN1 = "11111111-1111-1111-1111-111111111111"
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO crimes_unified (crime_id, source_system, source_record_id,
                fir_reg_num, brief_facts, current_source_run_id, current_as_of)
            VALUES ('924/2012', 'V1', '924/2012', '924/2012',
                    'Accused 1) Abhishek Kumar and 2) Golla Srinivasulu committed theft.',
                    %s, now())
            """, (RUN1,),
        )
        cur.execute(
            """
            INSERT INTO persons_unified (person_id, source_system, source_record_id,
                full_name, current_source_run_id, current_as_of)
            VALUES ('V1:P:924-2012-1', 'V1', 'P:924-2012-1',
                    '1) Abhishek Kumar, 2) Golla Srinivasulu', %s, now())
            """, (RUN1,),
        )
        cur.execute(
            """
            INSERT INTO accused_unified (accused_id, source_system, source_record_id,
                crime_id, person_id, unlinked_person_flag, current_source_run_id, current_as_of)
            VALUES ('V1:parent924', 'V1', 'A:924-2012-1', '924/2012',
                    'V1:P:924-2012-1', FALSE, %s, now())
            """, (RUN1,),
        )
        # Also seed the matching accused_source observation -- without this,
        # _load_accused_by_crime's normal (non-split) path would never have
        # surfaced this parent into the roster in the first place, which
        # would make the "parent excluded once split" check below vacuous.
        cur.execute(
            """
            INSERT INTO accused_source (source_system, source_table, source_record_id,
                source_run_id, source_created_at, source_modified_at, payload, consolidation_run_id)
            VALUES ('V1', 'cctns_accused', 'A:924-2012-1', %s, now(), now(),
                    %s, %s)
            """,
            (RUN1, json.dumps({"fir_reg_num": "924/2012", "accused_status": "UI"}), RUN1),
        )
    conn.commit()
    before = row_counts(conn, ["persons_unified", "accused_unified", "accused_split_audit"])
    print(f"  row counts before split: {before}")

    from etl3.enrichment import runner as runner_mod

    os.environ["ETL3_MULTI_ACCUSED_SPLIT_ENABLED"] = "1"

    parents = runner_mod._load_parent_accused_for_split(conn)
    parent924 = next(p for p in parents if p["accused_id"] == "V1:parent924")
    check(
        "parent lookup reads the real packed full_name via persons_unified join",
        parent924["full_name"] == "1) Abhishek Kumar, 2) Golla Srinivasulu",
        parent924["full_name"],
    )

    RUN2 = "22222222-2222-2222-2222-222222222222"
    result1 = runner_mod._multi_accused_split(conn, RUN2)
    conn.commit()
    print(f"  _multi_accused_split result: {json.dumps(result1, default=str)}")
    check("split stage ran (flag enabled)", result1["status"] == "ran", result1)
    check("exactly one high-confidence pack found", result1["high_confidence_packs"] == 1, result1)
    check("one parent newly marked split", result1["parents_newly_split"] == 1, result1)

    after = row_counts(conn, ["persons_unified", "accused_unified", "accused_split_audit"])
    print(f"  row counts after split: {after}")
    check("exactly 2 new persons_unified rows created", after["persons_unified"] - before["persons_unified"] == 2, after)
    check("exactly 2 new accused_unified rows created", after["accused_unified"] - before["accused_unified"] == 2, after)
    check("exactly 2 audit rows created", after["accused_split_audit"] - before["accused_split_audit"] == 2, after)

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT a.accused_id, a.person_id, a.crime_id, a.derived_from_accused_id,
                   a.split_segment_index, p.full_name, p.derived_from_person_id
            FROM accused_unified a JOIN persons_unified p ON p.person_id = a.person_id
            WHERE a.derived_from_accused_id = 'V1:parent924'
            ORDER BY a.split_segment_index
            """,
        )
        children = cur.fetchall()
    print("  children:")
    for row in children:
        print(f"    {row}")
    check("2 children found, linked to correct crime", len(children) == 2 and all(r[2] == "924/2012" for r in children), children)
    check("children have distinct person_id and accused_id", len({r[0] for r in children}) == 2 and len({r[1] for r in children}) == 2, children)
    check("child 1 full_name is the raw first segment", children[0][5] == "Abhishek Kumar", children)
    check("child 2 full_name is the raw second segment", children[1][5] == "Golla Srinivasulu", children)
    check("children's derived_from_person_id points at the parent person", all(r[6] == "V1:P:924-2012-1" for r in children), children)

    child_ids = [r[0] for r in children]
    abhishek_id = next(r[0] for r in children if r[5] == "Abhishek Kumar")
    golla_id = next(r[0] for r in children if r[5] == "Golla Srinivasulu")

    with conn.cursor() as cur:
        cur.execute("SELECT split_superseded_at FROM accused_unified WHERE accused_id = 'V1:parent924'")
        parent_superseded = cur.fetchone()[0]
    check("parent accused_unified row marked split_superseded_at (not deleted)", parent_superseded is not None, parent_superseded)
    with conn.cursor() as cur:
        cur.execute("SELECT full_name FROM persons_unified WHERE person_id = 'V1:P:924-2012-1'")
        parent_person_full_name = cur.fetchone()[0]
    check(
        "original packed full_name on the parent person is UNCHANGED",
        parent_person_full_name == "1) Abhishek Kumar, 2) Golla Srinivasulu",
        parent_person_full_name,
    )

    with conn.cursor() as cur:
        cur.execute("SELECT entity, unified_id, field FROM change_log WHERE unified_id = ANY(%s)", (child_ids,))
        change_log_rows = cur.fetchall()
    check("change_log entries written for the new accused rows", len(change_log_rows) > 0, change_log_rows)

    # ------------------------------------------------------------------
    # Existing, UNMODIFIED person_enrichment reused automatically.
    # ------------------------------------------------------------------
    print("\n== Running the existing, unmodified _person_enrichment over the new rows ==")
    RUN3 = "33333333-3333-3333-3333-333333333333"
    person_stats = runner_mod._person_enrichment(conn, RUN3, geo_kb=None)
    conn.commit()
    print(f"  person_enrichment stats: {person_stats}")
    with conn.cursor() as cur:
        cur.execute(
            "SELECT person_id, cleaned_full_name FROM person_enrichment WHERE person_id = ANY(%s) ORDER BY person_id",
            ([r[1] for r in children],),
        )
        enriched = dict(cur.fetchall())
    print(f"  cleaned names: {enriched}")
    check(
        "split children got real person_enrichment rows via the existing pipeline, unmodified",
        set(enriched.values()) == {"Abhishek Kumar", "Golla Srinivasulu"},
        enriched,
    )

    # ------------------------------------------------------------------
    # Existing AI roster builder (_load_accused_by_crime) with the flag on:
    # active children included exactly once, superseded parent excluded.
    # ------------------------------------------------------------------
    print("\n== _load_accused_by_crime: flag ENABLED ==")
    by_crime_enabled = runner_mod._load_accused_by_crime(conn)
    roster_924 = by_crime_enabled.get("924/2012", [])
    roster_ids = [r["accused_id"] for r in roster_924]
    check("parent 'V1:parent924' excluded from the roster once split", "V1:parent924" not in roster_ids, roster_ids)
    check("both children present exactly once each", sorted(roster_ids) == sorted(child_ids), roster_ids)

    print("\n== _load_accused_by_crime: flag DISABLED (must match pre-change behaviour) ==")
    os.environ["ETL3_MULTI_ACCUSED_SPLIT_ENABLED"] = "0"
    by_crime_disabled = runner_mod._load_accused_by_crime(conn)
    roster_924_disabled = [r["accused_id"] for r in by_crime_disabled.get("924/2012", [])]
    check(
        "disabled: roster is exactly the original parent, unaffected by the split rows that exist in the DB",
        roster_924_disabled == ["V1:parent924"],
        roster_924_disabled,
    )
    os.environ["ETL3_MULTI_ACCUSED_SPLIT_ENABLED"] = "1"

    # ------------------------------------------------------------------
    # Full AI-roster path through the REAL code (_accused_enrichment ->
    # _load_accused_by_crime -> process_accused_ai_for_crime ->
    # extract_accused_with_retry -> parse_accused_response ->
    # enrich_existing_accused -> upsert_rows), using a SCRIPTED client in
    # place of Ollama so this never dials out, plus real FIR text so the
    # existing V1 accused_code-from-FIR derivation
    # (apply_v1_code_type_and_ccl_numbering) has genuine signal for each
    # split child's own name.
    # ------------------------------------------------------------------
    print("\n== _accused_enrichment with a scripted AI client (no real network call) ==")
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO crimes_source (source_system, source_table, source_record_id,
                source_run_id, source_created_at, source_modified_at, payload, consolidation_run_id)
            VALUES ('V1', 'cctns_fir', '924/2012', %s, now(), now(), %s, %s)
            """,
            (
                RUN1,
                json.dumps({
                    "fir_reg_num": "924/2012",
                    "fir_contents": (
                        "On 12/05/2012 A-1 Abhishek Kumar entered the house and stole "
                        "jewellery while A-2 Golla Srinivasulu kept watch outside."
                    ),
                }),
                RUN1,
            ),
        )
    conn.commit()

    class _ScriptedAccusedClient:
        """Stands in for OllamaAccusedClient. Returns a canned response
        referencing the REAL (just-created) split accused_ids, so the
        extraction/validation/attribution pipeline runs against actual
        database-backed ids rather than hypothetical ones. Includes one
        deliberately-unknown accused_id to prove the roster validator
        still rejects anything not on this crime's real roster."""

        def complete(self, brief_facts, roster):
            return json.dumps({"accused": [
                {"accused_id": abhishek_id, "role_in_crime": "entered the house and stole jewellery", "age": 29},
                {"accused_id": golla_id, "role_in_crime": "acted as lookout outside", "age": 34},
                {"accused_id": "V1:does-not-exist", "role_in_crime": "should be rejected"},
            ]})

    # Never actually probe a network host: this test supplies its own
    # client and does not need (or want) the real Ollama health check.
    original_health_check = runner_mod._ollama_tags_healthy
    runner_mod._ollama_tags_healthy = lambda host, timeout=10.0: True
    try:
        RUN4 = "44444444-4444-4444-4444-444444444444"
        ai_settings_dict = {
            "enabled": True, "host": "unused", "model": "scripted-test", "timeout": 5,
            "mode": "backfill", "limit": 0, "batch_size": 5, "max_retries": 1,
            "request_delay_sec": 0, "health_cooldown_sec": 0,
        }
        accused_stats = runner_mod._accused_enrichment(
            conn, RUN4, accused_client=_ScriptedAccusedClient(), settings=ai_settings_dict,
        )
    finally:
        runner_mod._ollama_tags_healthy = original_health_check
    conn.commit()
    print(f"  accused_enrichment stats: {accused_stats}")

    with conn.cursor() as cur:
        cur.execute(
            "SELECT accused_id, accused_code, role_in_crime, age, field_sources "
            "FROM accused_enrichment WHERE accused_id = ANY(%s) ORDER BY accused_id",
            (child_ids,),
        )
        child_rows = {r[0]: {"accused_code": r[1], "role_in_crime": r[2], "age": r[3], "field_sources": r[4]} for r in cur.fetchall()}
    print(f"  child accused_enrichment rows: {json.dumps(child_rows, default=str, indent=2)}")
    check("each split child got its OWN accused_enrichment row", len(child_rows) == 2, child_rows)
    check(
        "each child's own A-code, derived from real FIR text, landed on the correct accused_id (not swapped)",
        child_rows.get(abhishek_id, {}).get("accused_code") == "A-1"
        and child_rows.get(golla_id, {}).get("accused_code") == "A-2",
        child_rows,
    )
    check(
        "Abhishek's AI-extracted role attributed to Abhishek's own accused_id, not swapped",
        child_rows.get(abhishek_id, {}).get("role_in_crime") == "entered the house and stole jewellery",
        child_rows.get(abhishek_id),
    )
    check(
        "Golla's AI-extracted role attributed to Golla's own accused_id, not swapped",
        child_rows.get(golla_id, {}).get("role_in_crime") == "acted as lookout outside",
        child_rows.get(golla_id),
    )
    check(
        "an unknown accused_id in the model response is rejected, not written to any record",
        "V1:does-not-exist" not in child_rows,
        list(child_rows),
    )
    with conn.cursor() as cur:
        cur.execute("SELECT status, validation_status FROM ai_extraction_attempts WHERE crime_id = '924/2012'")
        attempt_rows = cur.fetchall()
    check("the scripted AI attempt was recorded in ai_extraction_attempts for audit", len(attempt_rows) > 0, attempt_rows)

    # ------------------------------------------------------------------
    # Idempotent rerun: no duplicates, no churn.
    # ------------------------------------------------------------------
    print("\n== Idempotent rerun of the split stage (identical source text) ==")
    before_rerun = row_counts(conn, ["persons_unified", "accused_unified", "accused_split_audit"])
    result_rerun = runner_mod._multi_accused_split(conn, "55555555-5555-5555-5555-555555555555")
    conn.commit()
    after_rerun = row_counts(conn, ["persons_unified", "accused_unified", "accused_split_audit"])
    print(f"  rerun result: {result_rerun}")
    check("rerun creates zero new rows", before_rerun == after_rerun, (before_rerun, after_rerun))
    check("rerun finds zero newly-split parents (already split)", result_rerun["parents_newly_split"] == 0, result_rerun)

    # ------------------------------------------------------------------
    # Corrected name at one position -> old child superseded, new child
    # created, old child's enrichment preserved (never deleted).
    # ------------------------------------------------------------------
    print("\n== Source text corrected at position 2 (typo fix) ==")
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE persons_unified SET full_name = %s WHERE person_id = 'V1:P:924-2012-1'",
            ("1) Abhishek Kumar, 2) Golla Sriniwasulu",),
        )
    conn.commit()
    result_corrected = runner_mod._multi_accused_split(conn, "66666666-6666-6666-6666-666666666666")
    conn.commit()
    print(f"  corrected-text result: {json.dumps(result_corrected, default=str)}")
    with conn.cursor() as cur:
        cur.execute(
            "SELECT accused_id, split_superseded_at FROM accused_unified WHERE accused_id = %s", (golla_id,),
        )
        golla_after_correction = cur.fetchone()
    check("old 'Golla Srinivasulu' child superseded (not deleted) after the correction", golla_after_correction[1] is not None, golla_after_correction)
    with conn.cursor() as cur:
        cur.execute("SELECT cleaned_full_name FROM person_enrichment WHERE person_id = (SELECT person_id FROM accused_unified WHERE accused_id = %s)", (golla_id,))
        preserved = cur.fetchone()
    check("the superseded child's own prior person_enrichment row still exists (never deleted)", preserved is not None, preserved)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT a.accused_id, p.full_name FROM accused_unified a JOIN persons_unified p ON p.person_id = a.person_id "
            "WHERE a.derived_from_accused_id = 'V1:parent924' AND a.split_superseded_at IS NULL ORDER BY a.split_segment_index",
        )
        active_after_correction = cur.fetchall()
    check(
        "exactly 2 ACTIVE children after correction, with corrected spelling",
        [r[1] for r in active_after_correction] == ["Abhishek Kumar", "Golla Sriniwasulu"],
        active_after_correction,
    )
    check("Abhishek's own child id is unchanged by a correction elsewhere in the pack", active_after_correction[0][0] == abhishek_id, (active_after_correction, abhishek_id))

    # ------------------------------------------------------------------
    # Reordering: both old children superseded, two new ones created
    # (lossy-but-safe, never guesses "the same person moved").
    # ------------------------------------------------------------------
    print("\n== Reordered entries ==")
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE persons_unified SET full_name = %s WHERE person_id = 'V1:P:924-2012-1'",
            ("1) Golla Sriniwasulu, 2) Abhishek Kumar",),
        )
    conn.commit()
    result_reordered = runner_mod._multi_accused_split(conn, "77777777-7777-7777-7777-777777777777")
    conn.commit()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT accused_id FROM accused_unified WHERE derived_from_accused_id = 'V1:parent924' AND split_superseded_at IS NULL",
        )
        active_ids_after_reorder = {r[0] for r in cur.fetchall()}
    check(
        "reordering produced two fresh active children (old ones superseded, not reused in place)",
        abhishek_id not in active_ids_after_reorder and active_after_correction[1][0] not in active_ids_after_reorder,
        (active_ids_after_reorder, abhishek_id, active_after_correction[1][0]),
    )
    check("exactly 2 active children after reordering", len(active_ids_after_reorder) == 2, active_ids_after_reorder)

    # ------------------------------------------------------------------
    # Scenario: shared trailing address.
    # ------------------------------------------------------------------
    print("\n== Shared address scenario ==")
    RUN_SHARED = "88888888-8888-8888-8888-888888888888"
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO crimes_unified (crime_id, source_system, source_record_id, fir_reg_num, current_source_run_id, current_as_of)
            VALUES ('100/2013', 'V1', '100/2013', '100/2013', %s, now())
            """, (RUN_SHARED,),
        )
        cur.execute(
            """
            INSERT INTO persons_unified (person_id, source_system, source_record_id, full_name, current_source_run_id, current_as_of)
            VALUES ('V1:P:100-2013', 'V1', 'P:100-2013',
                    '1) Ramesh s/o Raju, 2) Suresh s/o Krishna, both R/o Hyderabad', %s, now())
            """, (RUN_SHARED,),
        )
        cur.execute(
            """
            INSERT INTO accused_unified (accused_id, source_system, source_record_id, crime_id, person_id, unlinked_person_flag, current_source_run_id, current_as_of)
            VALUES ('V1:parent100', 'V1', 'A:100-2013', '100/2013', 'V1:P:100-2013', FALSE, %s, now())
            """, (RUN_SHARED,),
        )
    conn.commit()
    runner_mod._multi_accused_split(conn, "99999999-9999-9999-9999-999999999999")
    conn.commit()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT p.present_address_text, p.permanent_address_text FROM accused_unified a "
            "JOIN persons_unified p ON p.person_id = a.person_id "
            "WHERE a.derived_from_accused_id = 'V1:parent100' AND a.split_superseded_at IS NULL",
        )
        shared_addrs = cur.fetchall()
    check("shared address text identical on both children", len(shared_addrs) == 2 and shared_addrs[0] == shared_addrs[1] == ("Hyderabad", "Hyderabad"), shared_addrs)

    # ------------------------------------------------------------------
    # Scenario: ambiguous unnumbered list -> no split at all.
    # ------------------------------------------------------------------
    print("\n== Ambiguous unnumbered list scenario ==")
    RUN_AMB = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO crimes_unified (crime_id, source_system, source_record_id, fir_reg_num, current_source_run_id, current_as_of) "
            "VALUES ('200/2014', 'V1', '200/2014', '200/2014', %s, now())", (RUN_AMB,),
        )
        cur.execute(
            "INSERT INTO persons_unified (person_id, source_system, source_record_id, full_name, current_source_run_id, current_as_of) "
            "VALUES ('V1:P:200-2014', 'V1', 'P:200-2014', 'Raju and Shiva', %s, now())", (RUN_AMB,),
        )
        cur.execute(
            "INSERT INTO accused_unified (accused_id, source_system, source_record_id, crime_id, person_id, unlinked_person_flag, current_source_run_id, current_as_of) "
            "VALUES ('V1:parent200', 'V1', 'A:200-2014', '200/2014', 'V1:P:200-2014', FALSE, %s, now())", (RUN_AMB,),
        )
    conn.commit()
    before_amb = row_counts(conn, ["persons_unified", "accused_unified"])
    runner_mod._multi_accused_split(conn, "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    conn.commit()
    after_amb = row_counts(conn, ["persons_unified", "accused_unified"])
    check("ambiguous unnumbered list creates no new rows at all", before_amb == after_amb, (before_amb, after_amb))
    with conn.cursor() as cur:
        cur.execute("SELECT split_superseded_at FROM accused_unified WHERE accused_id = 'V1:parent200'")
        parent200_superseded = cur.fetchone()[0]
    check("parent of an ambiguous list is left untouched (not marked split)", parent200_superseded is None, parent200_superseded)

    # ------------------------------------------------------------------
    # Scenario: "and N others" -> no fabricated people.
    # ------------------------------------------------------------------
    print("\n== 'and N others' scenario ==")
    RUN_OTH = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO crimes_unified (crime_id, source_system, source_record_id, fir_reg_num, current_source_run_id, current_as_of) "
            "VALUES ('300/2015', 'V1', '300/2015', '300/2015', %s, now())", (RUN_OTH,),
        )
        cur.execute(
            "INSERT INTO persons_unified (person_id, source_system, source_record_id, full_name, current_source_run_id, current_as_of) "
            "VALUES ('V1:P:300-2015', 'V1', 'P:300-2015', 'Ismail Khan and 5 others', %s, now())", (RUN_OTH,),
        )
        cur.execute(
            "INSERT INTO accused_unified (accused_id, source_system, source_record_id, crime_id, person_id, unlinked_person_flag, current_source_run_id, current_as_of) "
            "VALUES ('V1:parent300', 'V1', 'A:300-2015', '300/2015', 'V1:P:300-2015', FALSE, %s, now())", (RUN_OTH,),
        )
    conn.commit()
    before_oth = row_counts(conn, ["persons_unified", "accused_unified"])
    runner_mod._multi_accused_split(conn, "dddddddd-dddd-dddd-dddd-dddddddddddd")
    conn.commit()
    after_oth = row_counts(conn, ["persons_unified", "accused_unified"])
    check("'and 5 others' fabricates zero new people", before_oth == after_oth, (before_oth, after_oth))

    # ------------------------------------------------------------------
    # Scenario: parent with no linked person at all (unlinked_person_flag).
    # ------------------------------------------------------------------
    print("\n== Parent with no linked person (unlinked_person_flag=TRUE) ==")
    RUN_UNL = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO crimes_unified (crime_id, source_system, source_record_id, fir_reg_num, current_source_run_id, current_as_of) "
            "VALUES ('400/2016', 'V2', '400/2016', '400/2016', %s, now())", (RUN_UNL,),
        )
    conn.commit()
    # No persons_unified row at all -- accused_unified.person_id stays NULL,
    # exactly the documented "78 live V2 rows" shape. _load_parent_accused_for_split
    # requires a linked person to find a full_name, so this parent is correctly
    # invisible to the splitter (nothing to parse) -- verified, not assumed.
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO accused_unified (accused_id, source_system, source_record_id, crime_id, person_id, unlinked_person_flag, current_source_run_id, current_as_of) "
            "VALUES ('V2:parent400', 'V2', '400-acc', '400/2016', NULL, TRUE, %s, now())", (RUN_UNL,),
        )
    conn.commit()
    parents_after_unlinked = runner_mod._load_parent_accused_for_split(conn)
    check(
        "an accused with no linked person is correctly skipped by the splitter (nothing to parse)",
        all(p["accused_id"] != "V2:parent400" for p in parents_after_unlinked),
        [p["accused_id"] for p in parents_after_unlinked],
    )

    # ------------------------------------------------------------------
    # Snapshot + cleanup.
    # ------------------------------------------------------------------
    final_counts = row_counts(conn, [
        "crimes_unified", "persons_unified", "accused_unified",
        "accused_split_audit", "person_enrichment", "accused_enrichment", "change_log",
    ])
    print(f"\n== Final row counts: {final_counts} ==")
    conn.close()

    print(f"\n== Dropping disposable database {TEST_DB!r} ==")
    ac = admin_conn("postgres") if _can_connect("postgres") else admin_conn("dopams_cctns_v2")
    with ac.cursor() as cur:
        cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s", (TEST_DB,))
        cur.execute(f'DROP DATABASE "{TEST_DB}"')
    ac.close()
    print(f"  dropped {TEST_DB!r} -- nothing left behind")

    print(f"\n==================== SUMMARY ====================")
    print(f"PASS: {len(PASS)}   FAIL: {len(FAIL)}")
    for label, detail in FAIL:
        print(f"  [FAIL] {label}: {detail}")
    if FAIL:
        raise SystemExit(1)
    print("ALL CHECKS PASSED")


def _can_connect(dbname):
    try:
        c = admin_conn(dbname)
        c.close()
        return True
    except Exception:
        return False


if __name__ == "__main__":
    main()
