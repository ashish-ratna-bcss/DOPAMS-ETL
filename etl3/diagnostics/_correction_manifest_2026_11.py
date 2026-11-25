"""One-off historical correction script for the 2026-11 drug seizure
classification ticket (manifests built in
C:\\Users\\DELL\\AppData\\Local\\Temp\\claude\\744f5a975fc18c92\\...\\scratchpad\\kb_fix\\
REPORT_A_EXTRACTION_CORRECTIONS.csv / REPORT_B_DUPLICATE_RECONCILIATION.csv,
plus the 2 disputed crimes and 8 legacy garbled-name crimes resolved
directly against original FIR brief_facts text in this same ticket).

Every row below was individually verified against the original FIR
brief_facts before being listed here -- nothing here is derived by query,
by heuristic, or by re-running the AI model. This script only applies
already-approved, already-verified corrections; it does not decide
anything itself.

Four independent, separately-committed transactions (deliberately not one
combined transaction, so a problem in one category never blocks or rolls
back an already-verified, unrelated category):

  1. DELETE -- no physical seizure established in the original FIR text
     (17 original NO_SEIZURE crimes, 2 disputed crimes resolved as
     No drug Found using brief_facts as authoritative over a conflicting
     v2_property row, 1 "gulform" legacy crime with no seizure verb
     anywhere in its text, and 1 spurious zero-quantity duplicate row on
     a crime that was otherwise already correct).
  2. UPDATE -- quantity recovery, source-supported exactly (Heroin 6g,
     Hash oil 35g, Ganja 2g x2, Ganja 179.1g verified against its own
     41-packet table, Ganja 21.492kg verified against its own explicit
     stated total). Written via the project's existing upsert_rows()
     primitive since these change the hashed measurement fields.
  3. UPDATE -- 7 of the 8 legacy garbled-name V1 crimes ("gunjayi",
     "Gnaja", "gunja", "Cocien", "counter bands drugs") where the
     original FIR text explicitly describes a seizure/recovery/possession
     of a named quantity. primary_drug_name -> "Unidentified Substance"
     (Rule B), raw_drug_name/quantity/unit untouched -- no identity is
     invented, only the unresolved legacy NULL classification is
     corrected to the proper sentinel. Written as a direct UPDATE (not
     upsert_rows) because primary_drug_name/kb_match_tier are
     deliberately NOT part of _measurement_hash(), so upsert_rows's
     hash-diff gate would silently skip a primary_drug_name-only change.
  4. DELETE -- 31 duplicate rows from Report B (same substance,
     unmeasured, superseded by a canonical row for the same crime) that
     do not overlap categories 1 or 3.

Every category: snapshot BEFORE (read-only), write, re-read AFTER inside
the same still-open transaction, verify every targeted row matches
exactly and append one change_log row per write, verify no untouched row
in the same crime set changed. Any mismatch rolls back and exits
nonzero. Default is a dry run; pass --commit to apply.

Run (dry run, always safe):
    python etl3/diagnostics/_correction_manifest_2026_11.py

Run (apply, after reviewing the dry-run report):
    python etl3/diagnostics/_correction_manifest_2026_11.py --commit
"""
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.db import connections
from etl3.enrichment import kb as drug_kb
from etl3.enrichment.rules import resolve_drug_category

COLS = (
    "extraction_id, crime_id, source_system, provenance, raw_drug_name, "
    "primary_drug_name, drug_form, drug_category, raw_quantity, raw_unit, "
    "weight_g, weight_kg, volume_ml, volume_l, count_total, seizure_worth, "
    "is_commercial, confidence_score, kb_match_tier, input_hash"
)

# ---------------------------------------------------------------------------
# Category 1 -- DELETE: no physical seizure established in the original FIR
# ---------------------------------------------------------------------------
DELETE_NO_SEIZURE = [
    ("V2:ai:66fc798c3a5f1e53471eb113:0", "no_physical_seizure_consumption_urine_test_only"),
    ("V2:ai:67dc561e593fd97bb662a28b:0", "no_physical_seizure_consumption_urine_test_only"),
    ("V2:ai:6830f7666e1231be5258e9f7:0", "no_physical_seizure_consumption_urine_test_only"),
    ("V2:ai:6883467991d11d4489b9be5f:0", "no_physical_seizure_denied_possession_urine_test_only"),
    ("V2:ai:6883467991d11d4489b9be5f:1", "no_physical_seizure_denied_possession_urine_test_only"),
    ("V2:ai:6883467991d11d4489b9be5f:2", "no_physical_seizure_denied_possession_urine_test_only"),
    ("V2:ai:6883467991d11d4489b9be5f:3", "no_physical_seizure_denied_possession_urine_test_only"),
    ("V2:ai:6883467991d11d4489b9be5f:4", "no_physical_seizure_denied_possession_urine_test_only"),
    ("V2:ai:688dfcb291d11d21489db24a:0", "no_physical_seizure_consumption_admission_only"),
    ("V2:ai:688dfcb291d11d21489db24a:1", "no_physical_seizure_consumption_admission_only"),
    ("V2:ai:688dfcb291d11d21489db24a:2", "no_physical_seizure_consumption_admission_only"),
    ("V2:ai:69a26976160a4d0ea8a3373b:0", "no_physical_seizure_apprehension_only"),
    ("V2:ai:69d92814a8881aa1c235ca5f:0", "no_physical_seizure_consumption_admission_only"),
    ("V2:ai:6a302da0affa374f96b2e75a:0", "no_physical_seizure_intelligence_narrative_only"),
    ("V2:ai:6a50f41eed1c75a57d2866a9:0", "no_physical_seizure_consumption_admission_only"),
    ("V2:ai:6a58f63da14db8ee6a97bcd7:0", "no_physical_seizure_consumption_admission_only"),
    ("V2:ai:6a5a1d3e64ad674cec0e486e:0", "no_physical_seizure_consumption_admission_only"),
    ("V2:ai:6a641bbd7aaa6fc1d8156100:0", "no_physical_seizure_consumption_admission_only"),
    ("V2:ai:6a694a1ee97d635661d96306:0", "no_physical_seizure_consumption_admission_only"),
    ("V2:ai:6a743a46c5c7abcfe2daadca:0", "no_physical_seizure_consumption_admission_only"),
    ("V2:ai:6a85c7992bf408f4ac11968f:0", "no_physical_seizure_consumption_admission_only"),
    ("V2:ai:6abfb9f6e06993b0ba1997b8:0", "no_physical_seizure_consumption_admission_only"),
    ("V2:ai:6adcea197ab0743c1ba685d9:0", "no_physical_seizure_consumption_admission_only"),
    # spurious zero-qty duplicate on a crime otherwise already correct (3 of 4 rows retained)
    ("V2:ai:6921c8c344eb621d50853bba:3", "spurious_zero_qty_duplicate_of_boilerplate_denial_sentence"),
    # 2 disputed crimes, resolved using original FIR text as authoritative over a
    # conflicting v2_property row (ticket instruction: "Resolve the 2 disputed
    # crimes using the original FIR evidence")
    ("V2:ai:68989be612c367fa810503c2:0",
     "disputed_resolved_no_seizure: brief_facts states accused "
     "'are not having any drug in their personal possession'; urine-test-only"),
    ("V2:ai:6a4eac457492d91be872eec8:0",
     "disputed_resolved_no_seizure: brief_facts seizure is of the urine drug-testing-kit "
     "containers (M-1/M-2/M-3), not any drug; urine-test-only"),
    ("V2:ai:6a4eac457492d91be872eec8:1",
     "disputed_resolved_no_seizure: brief_facts seizure is of the urine drug-testing-kit "
     "containers (M-1/M-2/M-3), not any drug; urine-test-only"),
    ("V2:ai:6a4eac457492d91be872eec8:2",
     "disputed_resolved_no_seizure: brief_facts seizure is of the urine drug-testing-kit "
     "containers (M-1/M-2/M-3), not any drug; urine-test-only"),
    # legacy "gulform" crime: zero seizure/recovery verb anywhere in its one-sentence
    # brief_facts (alleges only that adulterated toddy was sold)
    ("V1:ai:2041032100062:0",
     "legacy_resolved_no_seizure: brief_facts alleges toddy adulterated with 'gulform' was "
     "sold; no seizure/recovery verb anywhere in the text"),
]

# ---------------------------------------------------------------------------
# Category 2 -- UPDATE: source-supported quantity recovery
# ---------------------------------------------------------------------------
QTY_UPDATES = [
    # extraction_id, new_raw_quantity, new_raw_unit, new_weight_g, new_weight_kg
    ("V2:ai:670422f9598680fab40bec21:0", 6.0, "grams", 6.0, 0.006),
    ("V2:ai:67b063ce494f2f5516485070:0", 35.0, "grams", 35.0, 0.035),
    ("V2:ai:68907d66a9c9ac715f87e3de:0", 2.0, "grams", 2.0, 0.002),
    ("V2:ai:68eb32963789c28b0ded2b4f:0", 2.0, "grams", 2.0, 0.002),
    ("V2:ai:6adad7d81257c236ed7a437b:0", 179.1, "grams", 179.1, 0.1791),
    ("V2:ai:66cf873e6e8acd09b0cb3c94:0", 21.492, "kg", 21492.0, 21.492),
]

# ---------------------------------------------------------------------------
# Category 3 -- UPDATE: legacy garbled name -> Unidentified Substance,
# only where the original FIR text establishes a physical seizure
# ---------------------------------------------------------------------------
LEGACY_UNIDENTIFIED = [
    ("V1:ai:2011057050068:0", "found 6 bags gunjayi and seized"),
    ("V1:ai:2041008150015:0", "found one bag containing sume Gnaja"),
    ("V1:ai:2024049020087:0", "illegal possetion of gunja...brought the accused along with property"),
    ("V1:ai:2011022050096:0", "found 7 kgs gunjayi arrested him"),
    ("V1:ai:2032032090187:0", "one bag was found...contained with gunja...produced...with Ganja bag"),
    ("V1:ai:2023057120349:0", "seized (4) small Cocien pockets containing 1 gram each"),
    ("V1:ai:2022018100109:0", "transporting counter bands drugs around 25 kg...in possessing"),
]

# ---------------------------------------------------------------------------
# Category 4 -- DELETE: duplicate rows (Report B), excluding rows already
# covered by category 1 (6883467991d11d4489b9be5f and 6a4eac457492d91be872eec8)
# ---------------------------------------------------------------------------
DELETE_DUPLICATES = [
    "V1:ai:2023039090250:2", "V1:ai:2023039090250:3",
    "V2:ai:63dbe10686768533e14633bb:3",
    "V2:ai:66d4a68f1b33d23007032dd6:1", "V2:ai:66d4a68f1b33d23007032dd6:2", "V2:ai:66d4a68f1b33d23007032dd6:3",
    "V2:ai:673fa43c38a015ab8bc44604:4",
    "V2:ai:677069610e26af029d5aa22d:1", "V2:ai:677069610e26af029d5aa22d:2",
    "V2:ai:677069610e26af029d5aa22d:3", "V2:ai:677069610e26af029d5aa22d:4",
    "V2:ai:685e42a7410d22848138d3f1:1",
    "V2:ai:68b1e9d7b35b5e78de11d362:8",
    "V2:ai:69005612197d4f56111fce1f:2", "V2:ai:69005612197d4f56111fce1f:3",
    "V2:ai:6916822d8c0d8e5c5164e44c:1",
    "V2:ai:691720a85941dea536ae849a:2", "V2:ai:691720a85941dea536ae849a:3",
    "V2:ai:69c12ea9cbb1a857f870d405:0", "V2:ai:69c12ea9cbb1a857f870d405:1",
    "V2:ai:6a148ac58ea11f4192f45108:1", "V2:ai:6a148ac58ea11f4192f45108:2", "V2:ai:6a148ac58ea11f4192f45108:3",
    "V2:ai:6a3f8b64f54f7c3475e037bb:1",
    "V2:ai:6a43d78484dbb144879cad54:0", "V2:ai:6a43d78484dbb144879cad54:1", "V2:ai:6a43d78484dbb144879cad54:2",
    "V2:ai:6a61e71f8e1386a0cec3efff:1", "V2:ai:6a61e71f8e1386a0cec3efff:2",
    "V2:ai:6ae5930357b36c3ad7ecb0d6:0", "V2:ai:6ae5930357b36c3ad7ecb0d6:1",
]


def _snapshot(conn, extraction_ids):
    with conn.cursor() as cur:
        cur.execute(f"SELECT {COLS} FROM drug_extractions WHERE extraction_id = ANY(%s)", (extraction_ids,))
        cols = [d.name for d in cur.description]
        return {row[0]: dict(zip(cols, row)) for row in cur.fetchall()}


def _insert_change_log(cur, entries):
    if not entries:
        return
    from psycopg2.extras import execute_values
    execute_values(
        cur,
        """
        INSERT INTO change_log
            (entity, unified_id, field, old_value, new_value, observed_at,
             source_system, source_run_id, change_classification)
        VALUES %s
        """,
        entries,
        template="(%s, %s, %s, %s, %s, now(), %s, %s, %s)",
    )


def run_category_1_delete_no_seizure(commit):
    ids = [eid for eid, _ in DELETE_NO_SEIZURE]
    run_id = str(uuid.uuid4())
    print(f"\n{'='*70}\nCATEGORY 1 -- DELETE no-physical-seizure rows (run_id={run_id})\n{'='*70}")
    conn = connections.get_unified_connection()
    before = _snapshot(conn, ids)
    missing = [eid for eid in ids if eid not in before]
    if missing:
        print(f"  ABORT: {len(missing)} expected rows not found: {missing}")
        conn.rollback()
        conn.close()
        raise SystemExit(1)
    print(f"  {len(before)} rows confirmed present, will be deleted:")
    for eid, reason in DELETE_NO_SEIZURE:
        row = before[eid]
        print(f"    {eid}: raw={row['raw_drug_name']!r} primary={row['primary_drug_name']!r} "
              f"qty={row['raw_quantity']} -- {reason}")
    with conn.cursor() as cur:
        log_rows = []
        for eid, reason in DELETE_NO_SEIZURE:
            row = before[eid]
            log_rows.append((
                "drug_extraction", eid, "row_deleted",
                f"raw={row['raw_drug_name']!r},primary={row['primary_drug_name']!r},qty={row['raw_quantity']}",
                f"deleted:{reason}", row["source_system"], run_id, "business_change",
            ))
        _insert_change_log(cur, log_rows)
        cur.execute("DELETE FROM drug_extractions WHERE extraction_id = ANY(%s)", (ids,))
        deleted = cur.rowcount
    after = _snapshot(conn, ids)
    if deleted != len(ids) or after:
        print(f"  ABORT: expected {len(ids)} deletes, got {deleted}; {len(after)} rows still present")
        conn.rollback()
        conn.close()
        raise SystemExit(1)
    print(f"  Verified: all {deleted} rows deleted, {len(log_rows)} change_log entries staged.")
    _finish(conn, commit)


def run_category_2_qty_updates(commit):
    ids = [eid for eid, *_ in QTY_UPDATES]
    run_id = str(uuid.uuid4())
    print(f"\n{'='*70}\nCATEGORY 2 -- UPDATE quantity recovery (run_id={run_id})\n{'='*70}")
    conn = connections.get_unified_connection()
    before = _snapshot(conn, ids)
    missing = [eid for eid in ids if eid not in before]
    if missing:
        print(f"  ABORT: {len(missing)} expected rows not found: {missing}")
        conn.rollback()
        conn.close()
        raise SystemExit(1)
    with conn.cursor() as cur:
        log_rows = []
        for eid, qty, unit, weight_g, weight_kg in QTY_UPDATES:
            row = before[eid]
            print(f"    {eid}: {row['primary_drug_name']} qty {row['raw_quantity']}{row['raw_unit'] or ''} "
                  f"-> {qty}{unit}")
            cur.execute(
                """
                UPDATE drug_extractions
                SET raw_quantity = %s, raw_unit = %s, weight_g = %s, weight_kg = %s,
                    enrichment_run_id = %s, computed_at = now()
                WHERE extraction_id = %s
                """,
                (qty, unit, weight_g, weight_kg, run_id, eid),
            )
            log_rows.append((
                "drug_extraction", eid, "raw_quantity",
                f"{row['raw_quantity']}{row['raw_unit'] or ''}", f"{qty}{unit}",
                row["source_system"], run_id, "business_change",
            ))
        _insert_change_log(cur, log_rows)
    after = _snapshot(conn, ids)
    bad = [eid for eid, qty, unit, *_ in QTY_UPDATES
           if float(after[eid]["raw_quantity"]) != qty or after[eid]["raw_unit"] != unit]
    if bad:
        print(f"  ABORT: verification failed for {bad}")
        conn.rollback()
        conn.close()
        raise SystemExit(1)
    print(f"  Verified: all {len(ids)} rows updated correctly, {len(log_rows)} change_log entries staged.")
    _finish(conn, commit)


def run_category_3_legacy_unidentified(commit):
    ids = [eid for eid, _ in LEGACY_UNIDENTIFIED]
    run_id = str(uuid.uuid4())
    new_category = resolve_drug_category(drug_kb.UNIDENTIFIED_SUBSTANCE)
    print(f"\n{'='*70}\nCATEGORY 3 -- UPDATE legacy garbled name -> Unidentified Substance "
          f"(run_id={run_id})\n{'='*70}")
    conn = connections.get_unified_connection()
    before = _snapshot(conn, ids)
    missing = [eid for eid in ids if eid not in before]
    if missing:
        print(f"  ABORT: {len(missing)} expected rows not found: {missing}")
        conn.rollback()
        conn.close()
        raise SystemExit(1)
    bad_pre = [eid for eid in ids if before[eid]["primary_drug_name"] is not None]
    if bad_pre:
        print(f"  ABORT: expected primary_drug_name NULL pre-correction, found non-null on {bad_pre}")
        conn.rollback()
        conn.close()
        raise SystemExit(1)
    with conn.cursor() as cur:
        log_rows = []
        for eid, evidence in LEGACY_UNIDENTIFIED:
            row = before[eid]
            print(f"    {eid}: raw={row['raw_drug_name']!r} qty={row['raw_quantity']}{row['raw_unit'] or ''} "
                  f"primary NULL -> {drug_kb.UNIDENTIFIED_SUBSTANCE!r} ({evidence})")
            cur.execute(
                """
                UPDATE drug_extractions
                SET primary_drug_name = %s, drug_category = %s,
                    kb_match_tier = 'unidentified_legacy_correction',
                    enrichment_run_id = %s, computed_at = now()
                WHERE extraction_id = %s
                """,
                (drug_kb.UNIDENTIFIED_SUBSTANCE, new_category, run_id, eid),
            )
            log_rows.append((
                "drug_extraction", eid, "primary_drug_name", None, drug_kb.UNIDENTIFIED_SUBSTANCE,
                row["source_system"], run_id, "business_change",
            ))
        _insert_change_log(cur, log_rows)
    after = _snapshot(conn, ids)
    bad = [eid for eid in ids if after[eid]["primary_drug_name"] != drug_kb.UNIDENTIFIED_SUBSTANCE
           or after[eid]["raw_drug_name"] != before[eid]["raw_drug_name"]
           or after[eid]["raw_quantity"] != before[eid]["raw_quantity"]]
    if bad:
        print(f"  ABORT: verification failed for {bad}")
        conn.rollback()
        conn.close()
        raise SystemExit(1)
    print(f"  Verified: all {len(ids)} rows updated correctly (raw name/quantity untouched), "
          f"{len(log_rows)} change_log entries staged.")
    _finish(conn, commit)


def run_category_4_delete_duplicates(commit):
    ids = DELETE_DUPLICATES
    run_id = str(uuid.uuid4())
    print(f"\n{'='*70}\nCATEGORY 4 -- DELETE duplicate rows (run_id={run_id})\n{'='*70}")
    conn = connections.get_unified_connection()
    before = _snapshot(conn, ids)
    missing = [eid for eid in ids if eid not in before]
    if missing:
        print(f"  ABORT: {len(missing)} expected rows not found: {missing}")
        conn.rollback()
        conn.close()
        raise SystemExit(1)
    bad_pre = [eid for eid in ids if before[eid]["raw_quantity"] not in (None, 0) or
               (before[eid]["seizure_worth"] not in (None, 0))]
    if bad_pre:
        print(f"  ABORT: safety check failed -- a row proposed for duplicate-deletion carries a "
              f"positive quantity/worth: {bad_pre}")
        conn.rollback()
        conn.close()
        raise SystemExit(1)
    print(f"  {len(before)} rows confirmed present (all zero quantity/worth, safe to remove), will be deleted.")
    with conn.cursor() as cur:
        log_rows = []
        for eid in ids:
            row = before[eid]
            log_rows.append((
                "drug_extraction", eid, "row_deleted",
                f"raw={row['raw_drug_name']!r},primary={row['primary_drug_name']!r}",
                "deleted:duplicate_unmeasured_mention_superseded_by_canonical_row",
                row["source_system"], run_id, "business_change",
            ))
        _insert_change_log(cur, log_rows)
        cur.execute("DELETE FROM drug_extractions WHERE extraction_id = ANY(%s)", (ids,))
        deleted = cur.rowcount
    after = _snapshot(conn, ids)
    if deleted != len(ids) or after:
        print(f"  ABORT: expected {len(ids)} deletes, got {deleted}; {len(after)} rows still present")
        conn.rollback()
        conn.close()
        raise SystemExit(1)
    print(f"  Verified: all {deleted} rows deleted, {len(log_rows)} change_log entries staged.")
    _finish(conn, commit)


def _finish(conn, commit):
    if not commit:
        print("  ==> DRY RUN only (no --commit). Rolling back, nothing written.")
        conn.rollback()
    else:
        conn.commit()
        print("  ==> COMMITTED.")
    conn.close()


def main():
    commit = "--commit" in sys.argv[1:]
    print(f"Mode: {'COMMIT' if commit else 'DRY RUN (default)'}")
    run_category_1_delete_no_seizure(commit)
    run_category_2_qty_updates(commit)
    run_category_3_legacy_unidentified(commit)
    run_category_4_delete_duplicates(commit)
    print("\nAll 4 categories processed." + (" COMMITTED." if commit else " Dry run complete, nothing written."))


if __name__ == "__main__":
    main()
