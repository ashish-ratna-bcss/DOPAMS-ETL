"""Controlled, verify-before-commit targeted fill for person_enrichment rows
where nationality resolved to India, domicile_classification/state was
ALREADY resolved (so these are NOT in the _backfill_missing_state_india.py
cohort), but address_resolution's district stayed null in both the present
and permanent slots -- the "Bhukya Yashwanth" class of gap (district blank
even though the UI's STATE/DOMICILE columns already show Telangana).

Root cause fixed alongside this script: "Medchal Malkajgiri" district
(2016 Telangana reorganization) is near-universally shortened to just
"Malkajgiri" in free-text addresses (confirmed against 555 real V1 records
using the short form with no "Medchal" anywhere in the text, consistently
in the trailing district position "...,Malkajgiri,Telangana"). See the
"malkajgiri" entry added to DISTRICT_ALIASES in etl3/enrichment/address.py.
Confirmed that this alias is only reached when no other token already
matched a district first in the left-to-right scan -- 18 V1 records mention
both "Hyderabad" and "Malkajgiri"; "Hyderabad" (appearing earlier in the
text) always wins and this alias never overrides that.

District is purely descriptive and does not feed classify_domicile() (which
only takes state/country) -- so filling it in can never change
domicile_classification/domicile_method/nationality for these rows. A
candidate is only applied when every one of those, plus every other stable
column, is unchanged from the BEFORE snapshot; only address_resolution may
differ, and only by filling in a previously-null district (and/or mandal)
in a slot that already had a resolved state.

Safety design: identical verify-before-commit pattern to
_backfill_missing_state_india.py / _reprocess_nationality_fix_verified.py.
  1. Snapshot every person_enrichment row's full column set BEFORE, on a
     read-only connection.
  2. Open a write connection, build candidate rows the same way the real
     _person_enrichment() does (same query, same person_row()).
  3. A candidate is "district_safe" only when: BEFORE had nationality='India'
     and address_resolution's district was null in both present and
     permanent slots (or address_resolution itself was null); AFTER has a
     non-null district in at least one slot; domicile_classification,
     domicile_method, nationality and every STABLE_KEYS column are all
     unchanged. Everything else is left exactly as stored.
  4. upsert_rows() is called with ONLY the district_safe rows, inside the
     same open transaction.
  5. Re-read from the same (still open, uncommitted) transaction and
     verify: district_safe rows now match the candidate values, and a
     broad sample of every other row is byte-identical to its BEFORE
     snapshot. Any surprise rolls back -- nothing is kept.
  6. Commit only with --commit; default is a dry run (report + rollback).

Run (dry run, always safe):
    python etl3/diagnostics/_backfill_missing_district_india.py

Run (apply, after reviewing the dry-run report):
    python etl3/diagnostics/_backfill_missing_district_india.py --commit
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
# Everything that must stay byte-identical. domicile_classification/method
# and nationality are included here (not just STABLE_KEYS) because this
# script's whole premise is that it never touches them -- only
# address_resolution's district/mandal may improve.
STABLE_KEYS = (
    "domicile_classification", "domicile_method", "nationality",
    "surname", "relation_type", "gender_source",
    "raw_full_name", "cleaned_full_name", "cleaned_given_name",
    "cleaned_alias", "cleaned_relative_name",
)


def _district_missing(address_resolution):
    if not address_resolution:
        return True
    present = (address_resolution.get("present") or {}).get("district")
    permanent = (address_resolution.get("permanent") or {}).get("district")
    return not present and not permanent


def _district_present(address_resolution):
    if not address_resolution:
        return False
    present = (address_resolution.get("present") or {}).get("district")
    permanent = (address_resolution.get("permanent") or {}).get("district")
    return bool(present or permanent)


def snapshot(conn):
    with conn.cursor() as cur:
        cur.execute(f"SELECT {FULL_COLUMNS} FROM person_enrichment")
        cols = [d.name for d in cur.description]
        return {row[0]: dict(zip(cols, row)) for row in cur.fetchall()}


def build_candidate_rows(conn, geo_kb):
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
    district_safe, other = [], []
    for pid, cand in candidates.items():
        prev = before.get(pid)
        if prev is None:
            continue
        was_target = (
            prev.get("nationality") == "India"
            and prev.get("domicile_classification") is not None
            and _district_missing(prev.get("address_resolution"))
        )
        if not was_target:
            continue
        now_has_district = _district_present(cand.get("address_resolution"))
        if not now_has_district:
            continue
        stable_ok = all((prev.get(k) or None) == (cand.get(k) or None) for k in STABLE_KEYS)
        if stable_ok:
            district_safe.append((pid, prev, cand))
        else:
            diffs = {k: (prev.get(k), cand.get(k)) for k in STABLE_KEYS
                     if (prev.get(k) or None) != (cand.get(k) or None)}
            other.append((pid, prev, cand, f"stable column changed: {diffs}"))
    return district_safe, other


def main():
    commit = "--commit" in sys.argv[1:]

    print("== Snapshotting BEFORE state (read-only connection) ==")
    ro_conn = connections.get_unified_connection(readonly=True)
    before = snapshot(ro_conn)
    ro_conn.close()
    print(f"  {len(before)} person_enrichment rows snapshotted")
    target_count = sum(
        1 for row in before.values()
        if row.get("nationality") == "India"
        and row.get("domicile_classification") is not None
        and _district_missing(row.get("address_resolution"))
    )
    print(f"  of which target cohort (nationality=India, domicile resolved, district null): {target_count}")

    print("\n== Opening write connection, building candidate rows (not yet upserted) ==")
    conn = connections.get_unified_connection()
    geo_kb = load_geo_kb(conn, schema="kb")
    run_id = str(uuid.uuid4())
    print(f"  run_id = {run_id}")
    candidates = build_candidate_rows(conn, geo_kb)
    print(f"  {len(candidates)} candidate rows computed")

    district_safe, other = classify(before, candidates)
    print(f"\n  district-safe newly-filled rows (will be applied): {len(district_safe)}")
    for pid, prev, cand in district_safe[:80]:
        addr = cand.get("address_resolution") or {}
        slot = addr.get("permanent") or addr.get("present") or {}
        print(f"    {pid}: district None -> {slot.get('district')!r} "
              f"(state={slot.get('state')!r}, mandal={slot.get('mandal')!r})")
    if len(district_safe) > 80:
        print(f"    ... and {len(district_safe) - 80} more")

    print(f"\n  excluded / not district-safe (NOT applied -- left exactly as stored): {len(other)}")
    for item in other[:20]:
        pid, prev, cand, reason = item
        print(f"    {pid}: {reason}")

    if not commit:
        print("\n==> DRY RUN only (no --commit). Rolling back, nothing written.")
        conn.rollback()
        conn.close()
        return

    if not district_safe:
        print("\n==> Nothing to commit (zero district-safe rows). Rolling back.")
        conn.rollback()
        conn.close()
        raise SystemExit(1)

    rows_to_write = [cand for _pid, _prev, cand in district_safe]
    print(f"\n== Upserting ONLY the {len(rows_to_write)} district-safe rows ==")
    stats = upsert_rows(
        conn, "person_enrichment", "person_id", rows_to_write,
        UPSERT_COLUMNS, run_id, "person_enrichment",
    )
    print(f"  upsert stats: {stats}")

    print("\n== Re-reading AFTER state from the SAME (still uncommitted) transaction ==")
    after = snapshot(conn)

    bad = []
    for pid, _prev, cand in district_safe:
        actual = after.get(pid)
        if actual is None or any(
            (actual.get(k) or None) != (cand.get(k) or None)
            for k in UPSERT_COLUMNS if k != "input_hash"
        ):
            bad.append((pid, cand, actual))

    district_safe_ids = {pid for pid, _prev, _cand in district_safe}
    untouched_violations = []
    for pid, prev, cand, _reason in other:
        actual = after.get(pid)
        if any((actual.get(k) or None) != (prev.get(k) or None) for k in UPSERT_COLUMNS if k != "input_hash"):
            untouched_violations.append((pid, prev, actual))
    sample_untouched = [pid for pid in before if pid not in district_safe_ids][:8000]
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

    print(f"\n==> Verified: exactly {len(district_safe)} rows updated, "
          f"all other {len(before) - len(district_safe)} rows byte-identical to before. Committing.")
    conn.commit()
    conn.close()
    print("COMMITTED.")


if __name__ == "__main__":
    main()
