"""Read-only audit: what would the corrected (deterministic) enrichment code
change in the real person_enrichment table right now, after commit e3c9ff0
(fuzzy address tie-break determinism) and the already-applied 21-row
nationality/domicile fill.

Makes NO writes. The write connection is used only because
etl3.enrichment.project.person_row needs a DB-backed GeoKB and the same
query shape as production _person_enrichment; every transaction opened here
is rolled back, never committed.

Classifies every person_enrichment row into exactly one of:
  1. nationality_domicile -- nationality/domicile_classification/
     domicile_method differ, address_resolution identical. (Expect zero:
     the 21-row fix is already committed.)
  2. mandal_tie -- address_resolution differs, but state/district/country
     are identical between stored and recomputed; only the mandal/village
     differs. Given the fuzzy-match code path (same KB, same input text),
     this is only possible when the old and new winners were tied at the
     same best score -- the determinism bug's exact fingerprint. The KB
     itself is static (all 676,108 kb.geo_reference rows share one bulk
     load timestamp), so this can't be "the reference data moved".
  3. other_address_change -- address_resolution differs AND state/district/
     country also differ. Would need independent justification; NOT
     assumed safe.
  4. unchanged -- every tracked column matches exactly.

Run: python etl3/diagnostics/_audit_address_fix_impact.py
"""
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.db import connections
from etl3.enrichment.address import load_geo_kb
from etl3.enrichment.project import person_row
from etl3.enrichment.runner import _latest, _pairs

FULL_COLUMNS = (
    "person_id, source_system, domicile_classification, domicile_method, "
    "nationality, surname, relation_type, gender_source, address_resolution, "
    "raw_full_name, cleaned_full_name, cleaned_given_name, cleaned_alias, "
    "cleaned_relative_name, input_hash"
)
NAT_KEYS = ("domicile_classification", "domicile_method", "nationality")
OTHER_STABLE_KEYS = (
    "surname", "relation_type", "gender_source",
    "raw_full_name", "cleaned_full_name", "cleaned_given_name",
    "cleaned_alias", "cleaned_relative_name",
)


def snapshot(conn):
    with conn.cursor() as cur:
        cur.execute(f"SELECT {FULL_COLUMNS} FROM person_enrichment")
        cols = [d.name for d in cur.description]
        return {row[0]: dict(zip(cols, row)) for row in cur.fetchall()}


def build_candidate_rows(conn, geo_kb):
    """Same construction _person_enrichment uses, without ever calling
    upsert_rows -- this script never writes anything."""
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


def _addr_geo(addr):
    """(state, district, country) per slot, ignoring mandal, for comparison."""
    if not addr:
        return None
    out = {}
    for slot in ("present", "permanent"):
        d = addr.get(slot) or {}
        out[slot] = (d.get("state"), d.get("district"), d.get("country"))
    return out


def classify(before, candidates):
    buckets = {"nationality_domicile": [], "mandal_tie": [], "other_address_change": [], "unchanged": []}
    for pid, cand in candidates.items():
        prev = before.get(pid)
        if prev is None:
            buckets["other_address_change"].append((pid, None, cand))
            continue
        all_cols = NAT_KEYS + OTHER_STABLE_KEYS + ("address_resolution",)
        if all((prev.get(k) or None) == (cand.get(k) or None) for k in all_cols):
            buckets["unchanged"].append(pid)
            continue
        nat_changed = any((prev.get(k) or None) != (cand.get(k) or None) for k in NAT_KEYS)
        addr_changed = (prev.get("address_resolution") or None) != (cand.get("address_resolution") or None)
        stable_ok = all((prev.get(k) or None) == (cand.get(k) or None) for k in OTHER_STABLE_KEYS)
        if nat_changed and not addr_changed and stable_ok:
            buckets["nationality_domicile"].append((pid, prev, cand))
        elif addr_changed and not nat_changed and stable_ok:
            geo_before = _addr_geo(prev.get("address_resolution"))
            geo_after = _addr_geo(cand.get("address_resolution"))
            if geo_before == geo_after:
                buckets["mandal_tie"].append((pid, prev, cand))
            else:
                buckets["other_address_change"].append((pid, prev, cand))
        else:
            buckets["other_address_change"].append((pid, prev, cand))
    return buckets


def main():
    print("== Snapshotting current person_enrichment (read-only connection) ==")
    ro_conn = connections.get_unified_connection(readonly=True)
    before = snapshot(ro_conn)
    ro_conn.close()
    print(f"  {len(before)} rows")

    print("\n== Computing what the corrected code would produce (never committed) ==")
    conn = connections.get_unified_connection()
    geo_kb = load_geo_kb(conn, schema="kb")
    candidates = build_candidate_rows(conn, geo_kb)
    conn.rollback()
    conn.close()
    print(f"  {len(candidates)} candidate rows computed")

    buckets = classify(before, candidates)
    print(f"\n  1. nationality_domicile corrections : {len(buckets['nationality_domicile'])}")
    print(f"  2. mandal/village tie-break changes  : {len(buckets['mandal_tie'])}")
    print(f"  3. other address-resolution changes  : {len(buckets['other_address_change'])}")
    print(f"  4. unchanged                         : {len(buckets['unchanged'])}")
    print(f"  TOTAL                                : {sum(len(v) for v in buckets.values())}")

    if buckets["nationality_domicile"]:
        print("\n  -- nationality_domicile rows (expected: already applied, should be none) --")
        for pid, prev, cand in buckets["nationality_domicile"][:25]:
            print(f"    {pid}: nationality {prev.get('nationality')!r}->{cand.get('nationality')!r}, "
                  f"domicile {prev.get('domicile_classification')!r}/{prev.get('domicile_method')!r} -> "
                  f"{cand.get('domicile_classification')!r}/{cand.get('domicile_method')!r}")

    if buckets["mandal_tie"]:
        print("\n  -- mandal_tie rows (same state/district/country, mandal/village flipped) --")
        for pid, prev, cand in buckets["mandal_tie"][:25]:
            b = prev.get("address_resolution") or {}
            a = cand.get("address_resolution") or {}
            print(f"    {pid}: present mandal {((b.get('present') or {}).get('mandal'))!r} -> "
                  f"{((a.get('present') or {}).get('mandal'))!r}; "
                  f"permanent mandal {((b.get('permanent') or {}).get('mandal'))!r} -> "
                  f"{((a.get('permanent') or {}).get('mandal'))!r}")

    if buckets["other_address_change"]:
        print("\n  -- other_address_change rows (NOT assumed safe; needs its own justification) --")
        for pid, prev, cand in buckets["other_address_change"][:25]:
            print(f"    {pid}: before={prev}  after={cand}")

    print("\n== No writes were made. Both connections closed without committing. ==")


if __name__ == "__main__":
    main()
