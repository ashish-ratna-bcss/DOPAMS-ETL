"""Read-only audit: person_enrichment rows where nationality resolved to
India but domicile_classification (and therefore state) is null.

For the V2 cohort, mines ward_colony/locality_village/area_mandal values
that recur across the unresolved set, and cross-references each one against
ALL V2 person records (not just unresolved ones) where the SAME value was
typed alongside a clerk-filled district+state -- the same evidence method
used to confirm the Dhoolpet -> Hyderabad/Telangana/Asif Nagar mapping.
Only candidates with strong, unambiguous corroboration are surfaced.

Read-only. Writes nothing.
"""
import sys
import json
from pathlib import Path
from collections import Counter, defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.db import connections
from etl3.enrichment.address import norm_token, LOCALITY_TO_DISTRICT_MANDAL

MIN_UNRESOLVED_OCCURRENCES = 3
MIN_CORROBORATION = 3
MIN_AGREEMENT = 0.90


def _norm(v):
    t = norm_token(v)
    return t.lower() if t else None


def main():
    conn = connections.get_unified_connection(readonly=True)
    with conn.cursor() as cur:
        print("== Top-line counts ==")
        cur.execute(
            "SELECT count(*) FROM person_enrichment WHERE nationality='India' AND domicile_classification IS NULL"
        )
        total = cur.fetchone()[0]
        print("nationality=India AND domicile_classification IS NULL:", total)

        cur.execute(
            """
            SELECT pe.source_system, count(*) FROM person_enrichment pe
            WHERE pe.nationality='India' AND pe.domicile_classification IS NULL
            GROUP BY 1
            """
        )
        print("by source_system:", cur.fetchall())

        # ---- V2 cohort: pull unresolved person_ids + their latest raw payload ----
        cur.execute(
            """
            SELECT pe.person_id
            FROM person_enrichment pe
            WHERE pe.nationality = 'India' AND pe.domicile_classification IS NULL
              AND pe.source_system = 'V2'
            """
        )
        unresolved_ids = {row[0] for row in cur.fetchall()}
        print(f"\nV2 unresolved person_ids: {len(unresolved_ids)}")

        cur.execute(
            """
            SELECT DISTINCT ON (source_record_id) source_record_id, payload
            FROM persons_source
            WHERE source_system = 'V2'
            ORDER BY source_record_id, source_fetched_at DESC
            """
        )
        all_payloads = {}
        for rid, payload in cur.fetchall():
            if isinstance(payload, str):
                payload = json.loads(payload)
            all_payloads[rid] = payload
    conn.close()

    print(f"Total V2 persons_source payloads loaded: {len(all_payloads)}")

    unresolved_payloads = {
        pid: all_payloads[pid] for pid in unresolved_ids if pid in all_payloads
    }
    print(f"Unresolved V2 persons with a payload found: {len(unresolved_payloads)}")

    # ---- Build locality -> (district,state,mandal) evidence index from the FULL V2 set ----
    # key: normalized value of ward_colony / locality_village  -> Counter of (district,state,mandal)
    evidence = defaultdict(Counter)
    for rid, payload in all_payloads.items():
        for prefix in ("present", "permanent"):
            district = payload.get(f"{prefix}_district")
            state = payload.get(f"{prefix}_state_ut")
            mandal = payload.get(f"{prefix}_area_mandal")
            if not district or not state:
                continue
            for field in ("ward_colony", "locality_village"):
                val = _norm(payload.get(f"{prefix}_{field}"))
                if not val or len(val) < 4:
                    continue
                evidence[val][(district.strip(), state.strip(), (mandal or "").strip())] += 1

    # ---- Count how often each locality value appears in the UNRESOLVED set ----
    unresolved_counts = Counter()
    for rid, payload in unresolved_payloads.items():
        seen_this_record = set()
        for prefix in ("present", "permanent"):
            for field in ("ward_colony", "locality_village"):
                val = _norm(payload.get(f"{prefix}_{field}"))
                if val and len(val) >= 4:
                    seen_this_record.add(val)
        for val in seen_this_record:
            unresolved_counts[val] += 1

    print(f"\nDistinct locality/ward values seen in unresolved set: {len(unresolved_counts)}")

    already_mapped = set(LOCALITY_TO_DISTRICT_MANDAL.keys())

    candidates = []
    for val, unresolved_n in unresolved_counts.most_common():
        if unresolved_n < MIN_UNRESOLVED_OCCURRENCES:
            continue
        if val in already_mapped:
            continue
        counter = evidence.get(val)
        if not counter:
            continue
        corroborating_total = sum(counter.values())
        if corroborating_total < MIN_CORROBORATION:
            continue
        top_combo, top_count = counter.most_common(1)[0]
        agreement = top_count / corroborating_total
        if agreement < MIN_AGREEMENT:
            continue
        candidates.append((val, unresolved_n, corroborating_total, agreement, top_combo, dict(counter)))

    print(f"\n== Candidate KB seeds (corroborated >= {MIN_CORROBORATION}x, agreement >= {MIN_AGREEMENT:.0%}) ==")
    print(f"{len(candidates)} candidates\n")
    for val, unresolved_n, corrob, agreement, combo, full in candidates:
        print(f"  {val!r}: unresolved_hits={unresolved_n}, corroborating={corrob}, agreement={agreement:.0%}")
        print(f"      -> district={combo[0]!r} state={combo[1]!r} mandal={combo[2]!r}")
        if len(full) > 1:
            print(f"      (other values seen: {full})")

    # ---- Values that recur in the unresolved set but have NO corroboration anywhere ----
    no_evidence = [
        (val, n) for val, n in unresolved_counts.most_common()
        if n >= MIN_UNRESOLVED_OCCURRENCES and val not in already_mapped and not evidence.get(val)
    ]
    print(f"\n== Recurring unresolved values with NO corroborating filled record anywhere ({len(no_evidence)}) ==")
    for val, n in no_evidence[:40]:
        print(f"  {val!r}: {n} unresolved occurrences, no corroboration found")

    # ---- DEBUG: every recurring value (n>=3) with whatever evidence exists, regardless of threshold ----
    print(f"\n== DEBUG: all recurring (n>=3) locality values with raw evidence ==")
    shown = 0
    for val, n in unresolved_counts.most_common():
        if n < MIN_UNRESOLVED_OCCURRENCES or val in already_mapped:
            continue
        counter = evidence.get(val)
        print(f"  {val!r}: unresolved_hits={n}, evidence={dict(counter) if counter else None}")
        shown += 1
        if shown >= 60:
            break

    # ---- How many unresolved records have genuinely no locality signal at all ----
    truly_blank = 0
    for rid, payload in unresolved_payloads.items():
        has_any = False
        for prefix in ("present", "permanent"):
            for field in ("ward_colony", "locality_village", "street_road_no", "house_no", "landmark_milestone", "address_text"):
                v = payload.get(f"{prefix}_{field}")
                if v and str(v).strip():
                    has_any = True
                    break
            if has_any:
                break
        if not has_any:
            truly_blank += 1
    print(f"\nV2 unresolved records with NO address text at all (any field): {truly_blank} / {len(unresolved_payloads)}")


if __name__ == "__main__":
    main()
