"""drug_finding_status historical backfill -- 2026-11 ticket, standalone.

Strict isolation: this script does not import anything from
etl3.enrichment.ai or etl3.enrichment.project, does not touch the AI
prompts/validation/KB, does not call the AI model, does not create any
drug_extractions row, and is never imported by or wired into the normal
ETL scheduling/execution path (etl3/run_enrichment.py and friends). It
only reads and writes etl3.enrichment.runner's existing, already-committed
drug_finding_status primitives (DRUG_FINDING_COLUMNS, DRUG_FOUND,
NO_DRUG_FOUND, _drug_finding_status_row) and etl3.enrichment.persist's
upsert_rows -- the same primitives the live pipeline already uses to
write this table, imported read-only, never modified.

Population: the fixed set of 15,462 crime_ids identified in this
ticket's earlier read-only audit as "has/had an etl3_ai drug_extractions
row, no drug_finding_status row" -- captured in
REPORT_C_STATUS_BACKFILL.csv *before* the 4 historical drug_extractions
corrections (deletions/quantity recovery/legacy-name resolution/duplicate
removal, all already committed in a separate, separately-approved step)
were applied. This script does not repeat, redo, or depend on redoing
those corrections -- it only classifies the resulting (already corrected)
drug_extractions rows.

Classification (no branch ever writes "Manual review" -- there is no
such status in this project):

    >=1 row with a real identified primary_drug_name       -> Drug found
        (backfill_from_identified_extraction)
    >=1 row with primary_drug_name = Unidentified Substance,
        none identified                                     -> Drug found
        (backfill_from_unidentified_substance)
    >=1 row with primary_drug_name NULL and raw_drug_name
        not null (legacy unresolved -- should be zero after
        the prior correction step; kept as a named exclusion
        bucket, not a silent default, in case of future drift) -> EXCLUDED
        (excluded_null_primary_legacy)
    zero etl3_ai rows survive (all removed as no-physical-
        seizure, or the crime never had any)                -> No drug Found
        (backfill_no_physical_seizure)

Idempotency / "avoid overwriting unnecessarily": for every population
crime_id this script computes existing vs. proposed status/method.
- No existing row + proposed status -> write (insert).
- Existing row already identical to proposed -> left out of the write
  batch entirely (upsert_rows would no-op it anyway via its input_hash
  compare, but excluding it up front keeps "rows to write" an honest count
  and makes a rerun after commit trivially show zero pending writes).
- Existing row that DIFFERS from the freshly-derived proposal -> never
  silently overwritten. Flagged as a validation failure and the whole run
  aborts before writing anything; a disagreement like that means either
  concurrent/unexpected activity wrote a status this run didn't expect,
  or the derivation rule itself needs a human look -- not something a
  one-off backfill script should paper over.

Writes go through etl3.enrichment.persist.upsert_rows (the project's
existing idempotent primitive) inside one transaction, with a fresh
enrichment_run_id and the standard change_log entry per row
(change_classification='initial_observation' for a crime's first-ever
status row). Verified by re-reading the same still-open transaction
before commit; any mismatch rolls back and exits nonzero. Dry run by
default; --commit applies the write; --yes skips the interactive
confirmation prompt (needed for non-interactive invocation, since stdin
is not attached to a terminal here) -- --commit alone still requires an
explicit CONFIRM_BACKFILL=1 environment variable, so a bare `--commit`
typo can never silently write to production.

Run (dry run + full pre-write validation report, always safe):
    python etl3/diagnostics/_backfill_drug_finding_status_2026_11.py

Run (apply, after reviewing the dry-run report):
    CONFIRM_BACKFILL=1 python etl3/diagnostics/_backfill_drug_finding_status_2026_11.py --commit --yes
"""
import csv
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.db import connections
from etl3.enrichment import kb as drug_kb
from etl3.enrichment.persist import upsert_rows
from etl3.enrichment.runner import (
    DRUG_FINDING_COLUMNS, DRUG_FOUND, NO_DRUG_FOUND, _drug_finding_status_row,
)

SCRATCH = Path(r"C:\Users\DELL\AppData\Local\Temp\claude\744f5a975fc18c92\bd158eec-61cc-4d5d-b749-3749bfb2bb11\scratchpad\kb_fix")
ORIGINAL_POPULATION_CSV = SCRATCH / "REPORT_C_STATUS_BACKFILL.csv"
DRYRUN_CSV = SCRATCH / "DRUG_FINDING_STATUS_BACKFILL_DRYRUN.csv"

MANUAL_REVIEW = "Manual review"
DISPUTED_CRIME_IDS = ["68989be612c367fa810503c2", "6a4eac457492d91be872eec8"]
LEGACY_CRIME_IDS = [
    "2011057050068", "2041008150015", "2024049020087", "2011022050096",
    "2041032100062", "2032032090187", "2023057120349", "2022018100109",
]
LEGACY_EXPECTED = {cid: (NO_DRUG_FOUND if cid == "2041032100062" else DRUG_FOUND) for cid in LEGACY_CRIME_IDS}
MANIFEST_EXPECTED_COUNTS = {
    "backfill_from_identified_extraction": 15432,
    "backfill_from_unidentified_substance": 7,
    "backfill_no_physical_seizure": 23,
}


def load_fixed_population():
    with open(ORIGINAL_POPULATION_CSV, newline="", encoding="utf-8") as f:
        crime_ids = [row["crime_id"] for row in csv.DictReader(f)]
    assert len(crime_ids) == len(set(crime_ids)), "duplicate crime_id in population CSV"
    return crime_ids


def _summarize(item_rows):
    if not item_rows:
        return "0 rows: no physical seizure (all extraction rows removed as unsupported, or none ever existed)"
    parts = []
    for primary, raw, qty, unit in item_rows:
        label = primary or raw or "?"
        qty_s = f" {qty}{unit or ''}" if qty not in (None, "") else ""
        parts.append(f"{label}{qty_s}")
    return f"{len(item_rows)} row(s): " + "; ".join(parts)


def derive(conn, crime_ids):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT crime_id, source_system, primary_drug_name, raw_drug_name, raw_quantity, raw_unit "
            "FROM drug_extractions WHERE provenance = 'etl3_ai' AND crime_id = ANY(%s)",
            (crime_ids,),
        )
        by_crime = {}
        for crime_id, source_system, primary, raw, qty, unit in cur.fetchall():
            by_crime.setdefault(crime_id, {"source_system": source_system, "rows": []})
            by_crime[crime_id]["rows"].append((primary, raw, qty, unit))

        cur.execute(
            "SELECT crime_id, status, method FROM drug_finding_status WHERE crime_id = ANY(%s)",
            (crime_ids,),
        )
        already = {r[0]: (r[1], r[2]) for r in cur.fetchall()}

        # Drift check: crimes OUTSIDE the fixed population that currently have
        # an etl3_ai row and no status -- would mean new processing happened
        # since the original audit that this fixed-population run doesn't know
        # about yet.
        cur.execute(
            """
            SELECT DISTINCT de.crime_id FROM drug_extractions de
            WHERE de.provenance = 'etl3_ai'
              AND NOT EXISTS (SELECT 1 FROM drug_finding_status s WHERE s.crime_id = de.crime_id)
              AND NOT (de.crime_id = ANY(%s))
            """,
            (crime_ids,),
        )
        drift_extra = [r[0] for r in cur.fetchall()]

    csv_rows, write_rows, conflicts, counts = [], [], [], {}
    for crime_id in crime_ids:
        info = by_crime.get(crime_id)
        item_rows = info["rows"] if info else []
        source_system = info["source_system"] if info else "V2"
        identified = [r for r in item_rows if r[0] and r[0] != drug_kb.UNIDENTIFIED_SUBSTANCE]
        unidentified = [r for r in item_rows if r[0] == drug_kb.UNIDENTIFIED_SUBSTANCE]
        legacy_null = [r for r in item_rows if r[0] is None and r[1] is not None]

        if identified:
            status, method = DRUG_FOUND, "backfill_from_identified_extraction"
        elif unidentified:
            status, method = DRUG_FOUND, "backfill_from_unidentified_substance"
        elif legacy_null:
            status, method = None, "excluded_null_primary_legacy"
        else:
            status, method = NO_DRUG_FOUND, "backfill_no_physical_seizure"

        existing = already.get(crime_id)
        existing_status, existing_method = existing if existing else (None, None)

        if status is None:
            action = "exclude_needs_human_decision"
        elif existing is None:
            action = "insert"
            write_rows.append((crime_id, source_system, status, method))
        elif existing_status == status and existing_method == method:
            action = "unchanged_skip"
        else:
            action = "CONFLICT_existing_status_differs"
            conflicts.append((crime_id, existing, (status, method)))

        counts[method if status is not None else "excluded_null_primary_legacy"] = \
            counts.get(method if status is not None else "excluded_null_primary_legacy", 0) + 1

        csv_rows.append({
            "crime_id": crime_id,
            "existing_status": existing_status or "",
            "proposed_status": status or "",
            "method": method,
            "extraction_count": len(item_rows),
            "identified_drug_count": len(identified),
            "unidentified_substance_count": len(unidentified),
            "source_evidence_summary": _summarize(item_rows),
            "action": action,
        })
    return csv_rows, write_rows, conflicts, counts, already, drift_extra


def run_validation(crime_ids, csv_rows, write_rows, conflicts, counts, already, drift_extra):
    checks = []
    checks.append(("1_complete_population_identified", len(csv_rows) == len(crime_ids),
                    f"{len(csv_rows)}/{len(crime_ids)}"))

    bad_zero_as_found = [r for r in csv_rows if r["extraction_count"] == 0 and r["proposed_status"] == DRUG_FOUND]
    checks.append(("4_no_zero_seizure_classified_as_drug_found", not bad_zero_as_found,
                    f"{len(bad_zero_as_found)} violations"))

    bad_generic = [r for r in csv_rows if r["unidentified_substance_count"] > 0
                   and r["identified_drug_count"] == 0 and r["proposed_status"] != DRUG_FOUND]
    checks.append(("5_unidentified_substance_always_drug_found", not bad_generic,
                    f"{len(bad_generic)} violations"))

    bad_identified = [r for r in csv_rows if r["identified_drug_count"] > 0 and r["proposed_status"] != DRUG_FOUND]
    checks.append(("6_identified_extractions_remain_drug_found", not bad_identified,
                    f"{len(bad_identified)} violations"))

    bad_manual = [r for r in csv_rows if r["proposed_status"] == MANUAL_REVIEW]
    checks.append(("7_no_manual_review_status_generated", not bad_manual,
                    f"{len(bad_manual)} violations"))

    accounted = sum(1 for r in csv_rows if r["action"] in
                     ("insert", "unchanged_skip", "exclude_needs_human_decision", "CONFLICT_existing_status_differs"))
    checks.append(("8_every_target_crime_accounted_for", accounted == len(crime_ids),
                    f"{accounted}/{len(crime_ids)}"))

    reconciles = all(counts.get(k, 0) == v for k, v in MANIFEST_EXPECTED_COUNTS.items()) \
        and counts.get("excluded_null_primary_legacy", 0) == 0
    checks.append(("9_reconciles_with_correction_manifests", reconciles,
                    f"{ {k: counts.get(k, 0) for k in MANIFEST_EXPECTED_COUNTS} }, "
                    f"excluded={counts.get('excluded_null_primary_legacy', 0)}"))

    checks.append(("10a_no_duplicate_crime_ids_in_population", len(crime_ids) == len(set(crime_ids)),
                    f"{len(crime_ids)} total, {len(set(crime_ids))} distinct"))
    checks.append(("10b_no_conflicting_concurrent_status", not conflicts,
                    f"{len(conflicts)} conflicts: {conflicts[:5]}"))
    checks.append(("10c_no_population_drift_since_audit", not drift_extra,
                    f"{len(drift_extra)} crimes outside population now need status: {drift_extra[:10]}"))

    spot = {r["crime_id"]: r for r in csv_rows}
    disputed_ok = all(spot.get(cid, {}).get("proposed_status") == NO_DRUG_FOUND for cid in DISPUTED_CRIME_IDS)
    checks.append(("disputed_crimes_resolved_no_drug_found", disputed_ok,
                    {cid: spot.get(cid, {}).get("proposed_status") for cid in DISPUTED_CRIME_IDS}))
    legacy_ok = all(spot.get(cid, {}).get("proposed_status") == exp for cid, exp in LEGACY_EXPECTED.items())
    checks.append(("legacy_crimes_correct_classification", legacy_ok,
                    {cid: spot.get(cid, {}).get("proposed_status") for cid in LEGACY_CRIME_IDS}))

    return checks


def write_dryrun_csv(csv_rows):
    with open(DRYRUN_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "crime_id", "existing_status", "proposed_status", "method", "extraction_count",
            "identified_drug_count", "unidentified_substance_count", "source_evidence_summary", "action",
        ])
        w.writeheader()
        w.writerows(csv_rows)


def main():
    commit = "--commit" in sys.argv[1:]
    print(f"Mode: {'COMMIT' if commit else 'DRY RUN (default)'}")
    crime_ids = load_fixed_population()
    print(f"Fixed population (from {ORIGINAL_POPULATION_CSV.name}): {len(crime_ids)} crime_ids")

    conn = connections.get_unified_connection()
    csv_rows, write_rows, conflicts, counts, already, drift_extra = derive(conn, crime_ids)
    write_dryrun_csv(csv_rows)
    print(f"Wrote {DRYRUN_CSV}")

    checks = run_validation(crime_ids, csv_rows, write_rows, conflicts, counts, already, drift_extra)
    print("\n== Pre-write validation ==")
    all_passed = True
    for name, passed, detail in checks:
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}: {detail}")
        all_passed = all_passed and passed

    print(f"\nDerivation counts: {counts}")
    already_matching = sum(1 for r in csv_rows if r["action"] == "unchanged_skip")
    print(f"Existing rows already correct (left out of write batch): {already_matching}")
    print(f"Rows to write (insert): {len(write_rows)}  "
          f"(Drug found: {sum(1 for r in write_rows if r[2] == DRUG_FOUND)}, "
          f"No drug Found: {sum(1 for r in write_rows if r[2] == NO_DRUG_FOUND)})")

    if not all_passed:
        print("\n==> ABORT: one or more mandatory pre-write validation checks failed. Nothing written.")
        conn.rollback()
        conn.close()
        raise SystemExit(1)

    if not write_rows:
        print("\n==> Nothing to write (every population crime already has the correct status). "
              "This is the expected result on an idempotent rerun after commit.")
        conn.rollback()
        conn.close()
        return

    run_id = str(uuid.uuid4())
    print(f"\nrun_id = {run_id}")
    candidate_rows = [_drug_finding_status_row(cid, ss, status, method) for cid, ss, status, method in write_rows]
    stats = upsert_rows(conn, "drug_finding_status", "crime_id", candidate_rows, DRUG_FINDING_COLUMNS, run_id, "drug_finding_status")
    print(f"upsert_rows stats: {stats}")

    print("\n== Re-reading AFTER state from the SAME (still open) transaction ==")
    with conn.cursor() as cur:
        cur.execute(
            "SELECT crime_id, status, method, enrichment_run_id FROM drug_finding_status WHERE crime_id = ANY(%s)",
            ([r[0] for r in write_rows],),
        )
        after = {r[0]: (r[1], r[2], str(r[3])) for r in cur.fetchall()}

    expected = {cid: (status, method) for cid, _ss, status, method in write_rows}
    bad = [cid for cid, (status, method) in expected.items()
           if cid not in after or after[cid][0] != status or after[cid][1] != method or after[cid][2] != run_id]

    if bad or len(after) != len(write_rows):
        print(f"\n==> ROLLING BACK -- verification failed: {len(bad)} mismatched rows, "
              f"{len(after)} written vs {len(write_rows)} expected.")
        for cid in bad[:10]:
            print(f"    {cid}: expected {expected[cid]}, got {after.get(cid)}")
        conn.rollback()
        conn.close()
        raise SystemExit(1)

    print(f"\n==> Verified: exactly {len(write_rows)} rows written, all match expected status/method/run_id.")

    if not commit:
        print("\n==> DRY RUN only (no --commit). Rolling back, nothing written.")
        conn.rollback()
        conn.close()
        return

    if os.environ.get("CONFIRM_BACKFILL") != "1":
        print("\n==> ABORT: --commit was passed but CONFIRM_BACKFILL=1 is not set in the environment. "
              "This is a deliberate second gate so a bare --commit can never silently write to production.")
        conn.rollback()
        conn.close()
        raise SystemExit(1)

    conn.commit()
    conn.close()
    print(f"COMMITTED. run_id={run_id}")


if __name__ == "__main__":
    main()
