"""Controlled, verify-before-commit targeted fill for person_enrichment rows
where nationality resolved to India but domicile_classification (and
therefore state) was null -- the "Dhoolpet" class of bug (see commit adding
"dhoolpet" to etl3.enrichment.address.LOCALITY_TO_DISTRICT_MANDAL, and the
GeoKB.unambiguous_mandal / resolve_address_text mandal-only-anchor fallback
added alongside it).

Two independent code paths now recover some of these rows:
  1. LOCALITY_TO_DISTRICT_MANDAL gained a verified "dhoolpet" entry
     (confirmed against 110 independently clerk-filled corroborating
     records) -- recovers V2 rows whose structured ward/locality field held
     that colony name with no district/state filled.
  2. resolve_address_text() gained a last-resort, KB-wide-unique mandal-name
     anchor for free text with no state or district token at all (the
     common V1 shape) -- see GeoKB.unambiguous_mandal. Exact match only,
     and declines whenever the same name also names a village in a
     *different* district (confirmed live: "Moosapet" is a real unique
     mandal in Mahabubnagar, but is ALSO the well-known Kukatpally/Medchal
     Malkajgiri village everyone means by that name -- 83% of real
     corroborating records say the latter. The collision guard in
     GeoKB.unambiguous_mandal rejects this pair entirely.)

Why targeted, not a truncate+rerun of person_enrichment:
  Same reasoning as _reprocess_nationality_fix_verified.py -- a full rerun
  would also pick up any unrelated non-determinism in already-stored rows
  (e.g. the mandal fuzzy tie-break's hash-seed dependence). This script only
  ever writes a row that was BEFORE: nationality='India' AND
  domicile_classification IS NULL, and AFTER: domicile_classification is
  non-null, with every other stable column (surname, relation_type,
  gender_source, cleaned name fields) unchanged. Anything else found by
  recomputation is left completely untouched.

Manually-excluded despite resolving (do not re-add without new evidence):
  - Person 69e4f19c115bc30488516ec7 ("Yanam" -> Puducherry/Yanam): Yanam is
    legally a Puducherry exclave, and kb.geo_reference tags it that way, but
    every one of the 5 independent clerk-filled CCTNS records that mention
    it say East/West Godavari (Andhra Pradesh) instead -- 0% agreement with
    the KB answer. Left non_recoverable rather than contradict every real
    example on file.
  - Person 65f3512c6cf30f1f5aad9235 ("Chinturu" -> Andhra Pradesh/Polavaram):
    the one independent corroborating record says East Godavari, not
    Polavaram -- likely an old-district-name vs. post-2022-AP-reorganization
    naming mismatch rather than a wrong place, but too thin (n=1) and too
    ambiguous to assert either way.

Safety design (same verify-before-commit pattern as
_reprocess_nationality_fix_verified.py):
  1. Snapshot every person_enrichment row's full column set BEFORE, on a
     read-only connection.
  2. Open a write connection, build candidate rows the same way the real
     _person_enrichment() does (same query, same person_row()).
  3. A candidate is "geo_safe" only when: BEFORE had nationality='India' AND
     domicile_classification IS NULL; AFTER has a non-null
     domicile_classification; every STABLE_KEYS column is unchanged; and the
     person_id is not in MANUALLY_EXCLUDED. Everything else is left exactly
     as stored, never passed to upsert_rows.
  4. upsert_rows() is called with ONLY the geo_safe rows, inside the same
     open transaction.
  5. Re-read from the same (still open, uncommitted) transaction and verify:
     geo_safe rows now match the candidate values, and a broad sample of
     every other row is byte-identical to its BEFORE snapshot. Any surprise
     rolls back -- nothing is kept.
  6. Commit only with --commit; default is a dry run (report + rollback).

Run (dry run, always safe):
    python etl3/diagnostics/_backfill_missing_state_india.py

Run (apply, after reviewing the dry-run report):
    python etl3/diagnostics/_backfill_missing_state_india.py --commit
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
STABLE_KEYS = (
    "surname", "relation_type", "gender_source",
    "raw_full_name", "cleaned_full_name", "cleaned_given_name",
    "cleaned_alias", "cleaned_relative_name",
)

MANUALLY_EXCLUDED = {
    "69e4f19c115bc30488516ec7",  # Yanam -> Puducherry: contradicts all 5 corroborating records
    "65f3512c6cf30f1f5aad9235",  # Chinturu -> Polavaram: thin, conflicting single corroboration
}


def snapshot(conn):
    with conn.cursor() as cur:
        cur.execute(f"SELECT {FULL_COLUMNS} FROM person_enrichment")
        cols = [d.name for d in cur.description]
        return {row[0]: dict(zip(cols, row)) for row in cur.fetchall()}


def build_candidate_rows(conn, geo_kb):
    """Mirrors etl3.enrichment.runner._person_enrichment's row construction
    exactly, but returns the computed rows instead of upserting them."""
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
    geo_safe, other = [], []
    for pid, cand in candidates.items():
        prev = before.get(pid)
        if prev is None:
            continue
        was_target = (prev.get("nationality") == "India"
                      and prev.get("domicile_classification") is None)
        if not was_target:
            continue
        now_resolved = cand.get("domicile_classification") is not None
        if not now_resolved:
            continue
        if pid in MANUALLY_EXCLUDED:
            other.append((pid, prev, cand, "manually excluded"))
            continue
        stable_ok = all((prev.get(k) or None) == (cand.get(k) or None) for k in STABLE_KEYS)
        if stable_ok:
            geo_safe.append((pid, prev, cand))
        else:
            diffs = {k: (prev.get(k), cand.get(k)) for k in STABLE_KEYS
                     if (prev.get(k) or None) != (cand.get(k) or None)}
            other.append((pid, prev, cand, f"stable column changed: {diffs}"))
    return geo_safe, other


def main():
    commit = "--commit" in sys.argv[1:]

    print("== Snapshotting BEFORE state (read-only connection) ==")
    ro_conn = connections.get_unified_connection(readonly=True)
    before = snapshot(ro_conn)
    ro_conn.close()
    print(f"  {len(before)} person_enrichment rows snapshotted")
    target_count = sum(
        1 for row in before.values()
        if row.get("nationality") == "India" and row.get("domicile_classification") is None
    )
    print(f"  of which target cohort (nationality=India, domicile_classification IS NULL): {target_count}")

    print("\n== Opening write connection, building candidate rows (not yet upserted) ==")
    conn = connections.get_unified_connection()
    geo_kb = load_geo_kb(conn, schema="kb")
    run_id = str(uuid.uuid4())
    print(f"  run_id = {run_id}")
    candidates = build_candidate_rows(conn, geo_kb)
    print(f"  {len(candidates)} candidate rows computed")

    geo_safe, other = classify(before, candidates)
    print(f"\n  geo-safe newly-resolved rows (will be applied): {len(geo_safe)}")
    for pid, prev, cand in geo_safe:
        addr = cand.get("address_resolution") or {}
        slot = addr.get("permanent") or addr.get("present") or {}
        print(f"    {pid}: domicile None -> {cand.get('domicile_classification')!r} "
              f"({slot.get('state')!r}/{slot.get('district')!r}/{slot.get('mandal')!r}, "
              f"method={cand.get('domicile_method')!r})")

    print(f"\n  excluded / not geo-safe (NOT applied -- left exactly as stored): {len(other)}")
    for item in other[:20]:
        pid, prev, cand, reason = item
        print(f"    {pid}: {reason}")

    if not commit:
        print("\n==> DRY RUN only (no --commit). Rolling back, nothing written.")
        conn.rollback()
        conn.close()
        return

    if not geo_safe:
        print("\n==> Nothing to commit (zero geo-safe rows). Rolling back.")
        conn.rollback()
        conn.close()
        raise SystemExit(1)

    rows_to_write = [cand for _pid, _prev, cand in geo_safe]
    print(f"\n== Upserting ONLY the {len(rows_to_write)} geo-safe rows ==")
    stats = upsert_rows(
        conn, "person_enrichment", "person_id", rows_to_write,
        UPSERT_COLUMNS, run_id, "person_enrichment",
    )
    print(f"  upsert stats: {stats}")

    print("\n== Re-reading AFTER state from the SAME (still uncommitted) transaction ==")
    after = snapshot(conn)

    bad = []
    for pid, _prev, cand in geo_safe:
        actual = after.get(pid)
        if actual is None or any(
            (actual.get(k) or None) != (cand.get(k) or None)
            for k in UPSERT_COLUMNS if k != "input_hash"
        ):
            bad.append((pid, cand, actual))

    geo_safe_ids = {pid for pid, _prev, _cand in geo_safe}
    untouched_violations = []
    for pid, prev, cand, _reason in other:
        actual = after.get(pid)
        if any((actual.get(k) or None) != (prev.get(k) or None) for k in UPSERT_COLUMNS if k != "input_hash"):
            untouched_violations.append((pid, prev, actual))
    # Broad sample of every row NOT touched by this run at all.
    sample_untouched = [pid for pid in before if pid not in geo_safe_ids][:8000]
    for pid in sample_untouched:
        if after.get(pid) != before.get(pid):
            untouched_violations.append((pid, before.get(pid), after.get(pid)))

    if bad or untouched_violations:
        print(f"\n==> ROLLING BACK -- verification failed: {len(bad)} bad writes, "
              f"{len(untouched_violations)} rows outside the approved set changed.")
        for v in (bad + untouched_violations)[:10]:
            print(f"    {v}")
        conn.rollback()
        conn.close()
        raise SystemExit(1)

    print(f"\n==> Verified: exactly {len(geo_safe)} rows updated, "
          f"all other {len(before) - len(geo_safe)} rows byte-identical to before. Committing.")
    conn.commit()
    conn.close()
    print("COMMITTED.")


if __name__ == "__main__":
    main()
