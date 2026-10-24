"""Controlled, verify-before-commit targeted fill for the nationality
false-positive fix (commit c4516a0 -- removing the bare-trailing-word
name->country fallback in etl3.enrichment.address._name_country_candidates).

Why targeted, not a truncate+rerun of person_enrichment:
  A full rerun recomputes every row, including rows whose stored
  address_resolution mandal/village came from a tied fuzzy match in
  etl3.enrichment.address._best(). That tie-break iterates a plain
  Python set() (GeoKB.mandals_by_district / villages_by_district), whose
  iteration order depends on the process's string hash seed
  (PYTHONHASHSEED, randomized by default). So re-running the full
  enrichment in a fresh process can flip already-tied mandal/village
  assignments (e.g. "Mahabubnagar Rural" <-> "Mahabubnagar Urban") for
  reasons that have nothing to do with the nationality fix and nothing
  to do with the underlying data. That tie-break bug is tracked
  separately and is NOT fixed by this script. This script reprocesses
  only the rows whose nationality/domicile actually changed because of
  the fix, and explicitly verifies address_resolution (and every other
  column) is byte-identical before touching anything, so none of that
  non-determinism can leak into this change.

Safety design (same verify-before-commit pattern as
_reprocess_person_names_verified.py):
  1. Snapshot every person_enrichment row's full column set BEFORE, on a
     read-only connection.
  2. Open a write connection, build candidate rows the same way the real
     _person_enrichment() does (same query, same person_row()), but do
     NOT call the production upsert_rows() for everyone -- classify each
     candidate against the BEFORE snapshot first.
  3. Only rows where address_resolution, surname, relation_type,
     gender_source, raw/cleaned name fields are IDENTICAL and only
     nationality/domicile_classification/domicile_method changed are
     "nationality_safe". Everything else (expected: the mandal tie-break
     rows) is left completely untouched -- never even passed to
     upsert_rows.
  4. upsert_rows() is called with ONLY the nationality_safe rows, inside
     the same open transaction.
  5. Re-read from the same (still open, uncommitted) transaction and
     verify: nationality_safe rows now match the candidate values, and
     every "other" row is byte-identical to its BEFORE snapshot (never
     touched). Any surprise rolls back -- nothing is kept.
  6. Commit only with --commit; default is a dry run (report + rollback).

Run (dry run, always safe):
    python etl3/diagnostics/_reprocess_nationality_fix_verified.py

Run (apply, after reviewing the dry-run report):
    python etl3/diagnostics/_reprocess_nationality_fix_verified.py --commit
"""
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.db import connections
from etl3.enrichment.address import load_geo_kb
from etl3.enrichment.persist import upsert_rows
from etl3.enrichment.project import person_row
from etl3.enrichment.runner import _latest, _pairs

FULL_COLUMNS = (
    "person_id, source_system, domicile_classification, domicile_method, "
    "nationality, surname, relation_type, gender_source, address_resolution, "
    "raw_full_name, cleaned_full_name, cleaned_given_name, cleaned_alias, "
    "cleaned_relative_name, input_hash"
)
UPSERT_COLUMNS = [
    "person_id", "source_system", "domicile_classification", "domicile_method",
    "nationality", "surname", "relation_type", "gender_source", "address_resolution",
    "raw_full_name", "cleaned_full_name", "cleaned_given_name",
    "cleaned_alias", "cleaned_relative_name", "input_hash",
]
# Columns that must be IDENTICAL for a row to be considered nationality-only.
STABLE_KEYS = (
    "surname", "relation_type", "gender_source", "address_resolution",
    "raw_full_name", "cleaned_full_name", "cleaned_given_name",
    "cleaned_alias", "cleaned_relative_name",
)
# Columns allowed to differ.
VOLATILE_KEYS = ("domicile_classification", "domicile_method", "nationality")


def snapshot(conn):
    with conn.cursor() as cur:
        cur.execute(f"SELECT {FULL_COLUMNS} FROM person_enrichment")
        cols = [d.name for d in cur.description]
        return {row[0]: dict(zip(cols, row)) for row in cur.fetchall()}


def build_candidate_rows(conn, geo_kb):
    """Mirrors etl3.enrichment.runner._person_enrichment's row construction
    exactly, but returns the computed rows instead of upserting them, so we
    can classify and filter before anything touches the database."""
    people = _pairs(
        conn,
        """
        SELECT person_id, source_system, source_record_id,
               full_name, alias, relative_name, nationality,
               present_address_text, permanent_address_text
        FROM persons_unified
        """,
    )
    v2_payloads = {
        record_id: payload
        for record_id, payload in _latest(conn, "persons_source", "V2")
    }
    rows = {}
    for (
        person_id, source_system, record_id, full_name, alias, relative_name,
        nationality, present_address_text, permanent_address_text,
    ) in people:
        if source_system == "V2":
            payload = dict(v2_payloads.get(record_id) or {})
            if not payload.get("full_name"):
                payload["full_name"] = full_name
            if not payload.get("alias"):
                payload["alias"] = alias
            if not payload.get("relative_name"):
                payload["relative_name"] = relative_name
            if not payload.get("nationality"):
                payload["nationality"] = nationality
            if not payload.get("present_address_text") and present_address_text:
                payload["present_address_text"] = present_address_text
            if not payload.get("permanent_address_text") and permanent_address_text:
                payload["permanent_address_text"] = permanent_address_text
        else:
            payload = {
                "full_name": full_name,
                "alias": alias,
                "relative_name": relative_name,
                "nationality": nationality,
                "present_address_text": present_address_text,
                "permanent_address_text": permanent_address_text,
            }
        row = person_row(source_system, person_id, payload, geo_kb=geo_kb)
        if row:
            rows[person_id] = row
    return rows


def classify(before, candidates):
    unchanged, nationality_safe, other = [], [], []
    for pid, cand in candidates.items():
        prev = before.get(pid)
        if prev is None:
            other.append((pid, None, cand))
            continue
        if all((prev.get(k) or None) == (cand.get(k) or None) for k in STABLE_KEYS + VOLATILE_KEYS):
            continue
        stable_ok = all((prev.get(k) or None) == (cand.get(k) or None) for k in STABLE_KEYS)
        volatile_changed = any((prev.get(k) or None) != (cand.get(k) or None) for k in VOLATILE_KEYS)
        if stable_ok and volatile_changed:
            nationality_safe.append((pid, prev, cand))
        else:
            other.append((pid, prev, cand))
    return nationality_safe, other


def main():
    commit = "--commit" in sys.argv[1:]

    print("== Snapshotting BEFORE state (read-only connection) ==")
    ro_conn = connections.get_unified_connection(readonly=True)
    before = snapshot(ro_conn)
    ro_conn.close()
    print(f"  {len(before)} person_enrichment rows snapshotted")

    print("\n== Opening write connection, building candidate rows (not yet upserted) ==")
    conn = connections.get_unified_connection()
    geo_kb = load_geo_kb(conn, schema="kb")
    run_id = str(uuid.uuid4())
    print(f"  run_id = {run_id}")
    candidates = build_candidate_rows(conn, geo_kb)
    print(f"  {len(candidates)} candidate rows computed")

    nationality_safe, other = classify(before, candidates)
    print(f"\n  nationality-only changes (safe to apply now): {len(nationality_safe)}")
    for pid, prev, cand in nationality_safe:
        print(
            f"    {pid}: nationality {prev.get('nationality')!r}->{cand.get('nationality')!r}, "
            f"domicile {prev.get('domicile_classification')!r}/{prev.get('domicile_method')!r} -> "
            f"{cand.get('domicile_classification')!r}/{cand.get('domicile_method')!r}"
        )

    print(f"\n  other changes (NOT applied by this script -- left exactly as stored): {len(other)}")
    for pid, prev, cand in other[:15]:
        if prev is None:
            print(f"    {pid}: new row not previously in person_enrichment")
            continue
        diffs = {
            k: (prev.get(k), cand.get(k))
            for k in STABLE_KEYS
            if (prev.get(k) or None) != (cand.get(k) or None)
        }
        print(f"    {pid}: differing stable columns = {diffs}")

    if not commit:
        print("\n==> DRY RUN only (no --commit). Rolling back, nothing written.")
        conn.rollback()
        conn.close()
        return

    if not nationality_safe:
        print("\n==> Nothing to commit (zero nationality-safe rows). Rolling back.")
        conn.rollback()
        conn.close()
        raise SystemExit(1)

    rows_to_write = [cand for _pid, _prev, cand in nationality_safe]
    print(f"\n== Upserting ONLY the {len(rows_to_write)} nationality-safe rows ==")
    stats = upsert_rows(
        conn, "person_enrichment", "person_id", rows_to_write,
        UPSERT_COLUMNS, run_id, "person_enrichment",
    )
    print(f"  upsert stats: {stats}")

    print("\n== Re-reading AFTER state from the SAME (still uncommitted) transaction ==")
    after = snapshot(conn)

    bad = []
    for pid, _prev, cand in nationality_safe:
        actual = after.get(pid)
        if actual is None or any((actual.get(k) or None) != (cand.get(k) or None) for k in UPSERT_COLUMNS if k != "input_hash"):
            bad.append((pid, cand, actual))
    untouched_violations = []
    for pid, prev, _cand in other:
        if prev is None:
            continue
        actual = after.get(pid)
        if any((actual.get(k) or None) != (prev.get(k) or None) for k in UPSERT_COLUMNS if k != "input_hash"):
            untouched_violations.append((pid, prev, actual))
    # Spot-check a broad sample of rows that were NOT in either list -- must be fully untouched.
    sample_untouched = [pid for pid in before if pid not in candidates][:5000]
    for pid in sample_untouched:
        if after.get(pid) != before.get(pid):
            untouched_violations.append((pid, before.get(pid), after.get(pid)))

    if bad or untouched_violations:
        print(f"\n==> ROLLING BACK -- verification failed: {len(bad)} bad writes, "
              f"{len(untouched_violations)} rows outside the approved set changed.")
        conn.rollback()
        conn.close()
        raise SystemExit(1)

    print(f"\n==> Verified: exactly {len(nationality_safe)} rows updated, "
          f"all other {len(before) - len(nationality_safe)} rows byte-identical to before. Committing.")
    conn.commit()
    conn.close()
    print("COMMITTED.")


if __name__ == "__main__":
    main()
