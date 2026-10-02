"""
V1 <-> V2 person identity candidate generation.

Hard rules (MERGER_REVALIDATION.md section 8, ETL3_MERGER_IMPLEMENTATION_PLAN.md
section 9, and this phase's own explicit instructions):
  - DOB is NEVER used as a match input. Confirmed populated on well under 1%
    of rows on both sides (V1 accused: 272/34,384 = 0.79%; V2 persons:
    79/32,790 = 0.24%) -- a field this sparse would, if weighted, just
    reward the rare rows that happen to have it, not actually discriminate.
  - Nothing here EVER sets identity_links.status to 'confirmed'. Every row
    this module writes is 'candidate'. The only code path that can move a
    row to 'confirmed' or 'rejected' is a human review action (reviewed_by
    set), which is explicitly outside ETL-3's scope.
  - Ambiguity (more than one plausible match on either side of a key) is
    recorded, not resolved by picking one -- every candidate pair for an
    ambiguous key is written, each confidence-scored lower than an
    unambiguous match on the same evidence tier.

Evidence tiers, each independently explainable and testable:
  DETERMINISTIC   (0.95) -- normalized phone matches exactly AND normalized
                            full name matches exactly, unambiguous both
                            sides (exactly one V1 and one V2 person for the
                            shared phone number).
  STRONG_SUPPORTED(0.80) -- normalized phone matches exactly AND at least
                            one name token overlaps (not necessarily the
                            full name), unambiguous both sides.
  WEAK_CANDIDATE  (0.60) -- normalized (name, father_name) pair matches
                            exactly, no phone corroboration, unambiguous
                            both sides.
  AMBIGUOUS       (0.30) -- same evidence as WEAK_CANDIDATE, but more than
                            one V1 or more than one V2 person shares the
                            matching key -- cannot tell which pairing (if
                            any) is correct from this evidence alone.

Phone and name-token matches are reported at STRONG_SUPPORTED/DETERMINISTIC
confidence regardless of count ONLY when the match is 1:1 on both sides for
that specific phone number; a phone number shared by more than one person on
either side is itself a form of ambiguity and is scored accordingly.
"""
import re

from psycopg2.extras import execute_values


def _norm_name(value):
    if not value:
        return None
    s = re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()
    return s or None


def _tokens(value):
    n = _norm_name(value)
    return set(t for t in n.split()) if n else set()


def _norm_phone(value):
    if not value:
        return None
    digits = re.sub(r"\D", "", str(value))
    return digits[-10:] if len(digits) >= 10 else None


def load_persons(conn, source_system: str):
    """[(person_id, norm_full_name, tokens, norm_relative_name, norm_phone), ...]"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT person_id, full_name, relative_name, phone_number "
            "FROM persons_unified WHERE source_system = %s",
            (source_system,),
        )
        out = []
        for person_id, full_name, relative_name, phone in cur.fetchall():
            out.append((person_id, _norm_name(full_name), _tokens(full_name), _norm_name(relative_name), _norm_phone(phone)))
        return out


def generate_candidates(conn):
    """Returns a list of (person_a_id, person_b_id, match_basis, confidence)
    ready for identity_links, person_a_id < person_b_id already enforced."""
    v1 = load_persons(conn, "V1")
    v2 = load_persons(conn, "V2")

    v1_by_phone, v2_by_phone = {}, {}
    v1_by_nf, v2_by_nf = {}, {}
    for pid, name, toks, father, phone in v1:
        if phone:
            v1_by_phone.setdefault(phone, []).append((pid, toks))
        if name and father:
            v1_by_nf.setdefault((name, father), []).append(pid)
    for pid, name, toks, father, phone in v2:
        if phone:
            v2_by_phone.setdefault(phone, []).append((pid, toks))
        if name and father:
            v2_by_nf.setdefault((name, father), []).append(pid)

    candidates = {}  # (a,b) -> (basis, confidence) ; keep the HIGHEST confidence if found via multiple tiers

    def _add(a, b, basis, confidence):
        pair = (a, b) if a < b else (b, a)
        if pair not in candidates or confidence > candidates[pair][1]:
            candidates[pair] = (basis, confidence)

    # Tier: phone-based
    for phone in set(v1_by_phone) & set(v2_by_phone):
        v1_matches = v1_by_phone[phone]
        v2_matches = v2_by_phone[phone]
        unambiguous = len(v1_matches) == 1 and len(v2_matches) == 1
        for a_id, a_toks in v1_matches:
            for b_id, b_toks in v2_matches:
                shared_tokens = bool(a_toks & b_toks)
                if not shared_tokens:
                    continue  # phone alone, no name corroboration at all -- not used as a candidate basis
                if unambiguous and a_toks == b_toks and a_toks:
                    _add(a_id, b_id, "phone_exact+name_exact", 0.95)
                elif unambiguous:
                    _add(a_id, b_id, "phone_exact+name_token", 0.80)
                else:
                    _add(a_id, b_id, "phone_exact+name_token+ambiguous_phone", 0.30)

    # Tier: name+father_name
    for key in set(v1_by_nf) & set(v2_by_nf):
        a_ids = v1_by_nf[key]
        b_ids = v2_by_nf[key]
        unambiguous = len(a_ids) == 1 and len(b_ids) == 1
        for a_id in a_ids:
            for b_id in b_ids:
                if unambiguous:
                    _add(a_id, b_id, "name_and_father_name_exact", 0.60)
                else:
                    _add(a_id, b_id, "name_and_father_name_exact+ambiguous", 0.30)

    return [(a, b, basis, conf) for (a, b), (basis, conf) in candidates.items()]


def write_candidates(conn, candidates, consolidation_run_id: str) -> dict:
    """Idempotent: relies on identity_links' own UNIQUE(person_a_id, person_b_id)
    constraint -- replaying the same candidate set is always a safe no-op,
    never a duplicate, never an overwrite of a status a human may since have
    set (ON CONFLICT DO NOTHING, not DO UPDATE -- a human's confirmed/rejected
    decision on a pair is never silently reverted by a later candidate-
    generation run)."""
    if not candidates:
        return {"candidates_generated": 0, "newly_inserted": 0}
    with conn.cursor() as cur:
        # fetch=True + RETURNING is required for an accurate count here --
        # execute_values splits large inserts into multiple internal
        # batches, and cur.rowcount alone only reflects the LAST batch, not
        # the true total (found and fixed while testing this: reported
        # "27 newly inserted" against an actual, verified 1,127).
        inserted_rows = execute_values(
            cur,
            """
            INSERT INTO identity_links
                (person_a_id, person_b_id, match_basis, confidence_score, status, generated_by_run_id)
            VALUES %s
            ON CONFLICT (person_a_id, person_b_id) DO NOTHING
            RETURNING person_a_id
            """,
            [(a, b, basis, conf, "candidate", consolidation_run_id) for a, b, basis, conf in candidates],
            fetch=True,
        )
        inserted = len(inserted_rows)
    return {"candidates_generated": len(candidates), "newly_inserted": inserted}
