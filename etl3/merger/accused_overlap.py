"""Crime-level name-overlap detection for the multi-accused split gate.

This module answers exactly one question: "does a parsed combined-name
segment look like it might already be represented by an existing,
non-split accused record on the same crime?" It never answers "is this the
same person" -- that decision is explicitly out of scope everywhere in
this codebase (see etl3/identity/person_matching.py's own docstring: DOB
is never used, nothing here ever auto-confirms an identity, ambiguity is
recorded, not resolved). Every result from this module is evidence for a
human reviewer, surfaced via etl3.merger.accused_split's review-row path --
nothing calls this and then auto-links, auto-merges, or auto-assigns an
existing accused_id to a new segment.

Reuses etl3.identity.person_matching's own normalization helpers
(_norm_name/_tokens) rather than inventing a second normalizer, and the
project's existing fuzzy-matching precedent (difflib.SequenceMatcher,
already used the same way in etl3/merger/ps_enrichment.py and at the same
~0.75-0.85 order of magnitude as the SIM_* constants in
etl3/enrichment/address.py) rather than adding a new dependency.
"""
import difflib

from etl3.identity.person_matching import _norm_name, _tokens

# A token counts as "matched" against another token at this ratio or above
# (SequenceMatcher.ratio(), 0..1). Empirically tuned against real CCTNS
# spelling-drift pairs observed in FIR 869/2026 ("bebri"/"hebri"=0.80,
# "koushal"/"kaushal"=0.857, "divi"/"ddivi"=0.889, "siva"/"shiva"=0.889 --
# all genuine same-person variants) against genuinely different short
# tokens from the same dataset ("rafi"/"yusuf"=0.222, "raju"/"ravi"=0.5,
# "kumar"/"kiran"=0.4, "singh"/"sunil"=0.4 -- all comfortably excluded).
# 0.78 catches every same-person pair above while excluding every
# different-person pair tested; see etl3/tests/test_accused_overlap.py.
TOKEN_MATCH_RATIO = 0.78

# Below this overall score, two names are not reported as an overlap at
# all. Chosen low enough that an asymmetric partial match (one side has an
# extra token the other doesn't, e.g. a dropped middle name) still flags --
# the cost of a false positive here is a human review, never a merge --
# while two people who merely share one common first-name token do not.
OVERLAP_SCORE_THRESHOLD = 0.60

EXACT_NORMALIZED_NAME = "exact_normalized_name"
EXACT_TOKEN_SET_REORDERED = "exact_token_set_reordered"
STRONG_NAME_SIMILARITY = "strong_name_similarity"


def _token_coverage(tokens_a, tokens_b):
    """Fraction of tokens_a that have a fuzzy match (ratio >= TOKEN_MATCH_RATIO)
    somewhere in tokens_b. 0.0 when tokens_a is empty -- never divides by zero."""
    if not tokens_a:
        return 0.0
    matched = 0
    for ta in tokens_a:
        if any(
            difflib.SequenceMatcher(None, ta, tb).ratio() >= TOKEN_MATCH_RATIO
            for tb in tokens_b
        ):
            matched += 1
    return matched / len(tokens_a)


def name_overlap_score(name_a, name_b):
    """(basis, score) describing how strongly name_a and name_b might refer
    to the same person. Evidence only -- see module docstring.

    basis is one of EXACT_NORMALIZED_NAME, EXACT_TOKEN_SET_REORDERED,
    STRONG_NAME_SIMILARITY, or None when the score is below
    OVERLAP_SCORE_THRESHOLD. score is always returned, even when basis is
    None, so a caller building an audit/review record can show the number
    a human will want to see.
    """
    norm_a, norm_b = _norm_name(name_a), _norm_name(name_b)
    if not norm_a or not norm_b:
        return (None, 0.0)
    if norm_a == norm_b:
        return (EXACT_NORMALIZED_NAME, 1.0)

    toks_a, toks_b = _tokens(name_a), _tokens(name_b)
    if toks_a and toks_b and toks_a == toks_b:
        # Same token set, different order -- e.g. "Kattimani Mandira" vs
        # "Mandira Kattimani". Reordering alone is not identity, but it is
        # strong enough evidence to always route for review.
        return (EXACT_TOKEN_SET_REORDERED, 0.97)

    coverage_a = _token_coverage(toks_a, toks_b)
    coverage_b = _token_coverage(toks_b, toks_a)
    score = round((coverage_a + coverage_b) / 2, 4) if (toks_a or toks_b) else 0.0
    if score >= OVERLAP_SCORE_THRESHOLD:
        return (STRONG_NAME_SIMILARITY, score)
    return (None, score)


def find_crime_overlaps(segments, existing_accused):
    """Per-segment overlap lookup against accused already on the same crime.

    segments: the list shape detect_multi_accused()/build_split_rows()
        already use -- each a dict with at least "segment_index" and
        "raw_text".
    existing_accused: list of (accused_id, full_name) tuples. The CALLER
        is responsible for scoping this to: the same crime_id, excluding
        the parent itself, excluding any row derived from a split
        (derived_from_accused_id IS NOT NULL), and excluding anything
        already superseded (split_superseded_at IS NOT NULL). This
        function never queries a crime_id or opens a connection -- it is
        pure, so it can be unit-tested without a database.

    Returns {segment_index: {"accused_id", "full_name", "basis", "score"}}
    for every segment that has at least one match at or above
    OVERLAP_SCORE_THRESHOLD -- only the single strongest match per segment
    (nothing downstream acts on a second-best candidate). A segment or
    existing_accused entry with no usable name text never matches anything.
    """
    overlaps = {}
    for segment in segments:
        best = None
        for accused_id, full_name in existing_accused:
            basis, score = name_overlap_score(segment["raw_text"], full_name)
            if basis is None:
                continue
            if best is None or score > best["score"]:
                best = {
                    "accused_id": accused_id,
                    "full_name": full_name,
                    "basis": basis,
                    "score": score,
                }
        if best is not None:
            overlaps[segment["segment_index"]] = best
    return overlaps
