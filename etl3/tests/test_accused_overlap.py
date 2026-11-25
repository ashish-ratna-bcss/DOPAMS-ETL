"""Unit tests for etl3.merger.accused_overlap -- the crime-level duplicate
guard added after FIR 869/2026 (anonymized real example used throughout:
the same crime carried four independently-coded CCTNS V2 accused records
AND one combined-pack record for the same four people). Pure functions
only, no database -- same discipline as test_multi_accused.py.

Run directly: python etl3/tests/test_accused_overlap.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.merger.accused_overlap import (
    EXACT_NORMALIZED_NAME,
    EXACT_TOKEN_SET_REORDERED,
    STRONG_NAME_SIMILARITY,
    find_crime_overlaps,
    name_overlap_score,
)


def check(label, fn):
    try:
        fn()
        print(f"[PASS] {label}")
    except Exception as exc:
        print(f"[FAIL] {label}: {type(exc).__name__}: {exc}")
        raise


# ---------------------------------------------------------------------------
# name_overlap_score: scenario #4 (exact duplicate, different source ids
# is the CALLER's concern -- this function only scores two name strings)
# and #5 (same name, different people must not be assumed identical --
# this function never claims identity, only returns evidence)
# ---------------------------------------------------------------------------

def test_exact_match():
    basis, score = name_overlap_score("Ravi Kumar", "ravi   kumar")
    assert basis == EXACT_NORMALIZED_NAME
    assert score == 1.0


def test_exact_token_set_reordered():
    # Anonymized FIR 869/2026 pattern: segment 4 vs its pre-existing,
    # independently-coded CCTNS record -- same two tokens, swapped order.
    basis, score = name_overlap_score("Person4 Family4", "Family4 Person4")
    assert basis == EXACT_TOKEN_SET_REORDERED
    assert score == 0.97


def test_strong_similarity_asymmetric_extra_token():
    # Anonymized FIR 869/2026 pattern: segment 1 ("Person1 Family1") vs its
    # pre-existing record ("Family1 Extra1 person1scrambled") -- reordered,
    # one extra token, one token scrambled/typo'd. Real-world asymmetric
    # partial overlap, not a clean exact match.
    basis, score = name_overlap_score("Divi Sampath", "Sampath Goud ddivi")
    assert basis == STRONG_NAME_SIMILARITY
    assert score >= 0.60


def test_strong_similarity_spelling_drift_and_reorder():
    # Anonymized FIR 869/2026 pattern: segment 2 vs its pre-existing record
    # -- same three given/relative/family tokens, reordered, one token
    # spelling-drifted ("Ameer" vs "Amir").
    basis, score = name_overlap_score("Syed Ameer Ali", "Amir Ali Syed")
    assert basis == STRONG_NAME_SIMILARITY
    assert score >= 0.60


def test_strong_similarity_severe_letter_transposition():
    # Anonymized FIR 869/2026 pattern: the juvenile/CCL segment -- the
    # most severely distorted pair in the real record (a transposed
    # leading letter plus a dropped trailing token on the existing side).
    basis, score = name_overlap_score("Bebri koushal Naik", "kAUSHAL Hebri")
    assert basis == STRONG_NAME_SIMILARITY
    assert score >= 0.60


def test_different_people_sharing_one_common_token_do_not_overlap():
    """Scenario #5: identical/overlapping names do not prove identical
    people, but the converse matters just as much here -- two different
    people who merely share ONE common first name (very common in this
    dataset) must NOT be flagged, or the review queue would drown in noise."""
    basis, score = name_overlap_score("Mohammed Rafi", "Mohammed Yusuf")
    assert basis is None
    assert score < 0.60


def test_wholly_different_names_do_not_overlap():
    basis, score = name_overlap_score("Suresh Kumar", "Naveen Singh")
    assert basis is None


def test_ambiguous_partial_name_still_flags_for_review():
    """A bare first name vs a full name is genuinely ambiguous (could be
    the same person with a dropped surname, or a coincidence) -- per the
    task's own rule ("strong similarity: flag for manual review"), this
    must flag, not auto-dismiss. The cost of flagging is a human glance;
    the cost of not flagging is a second FIR-869-style triplication."""
    basis, score = name_overlap_score("Tony", "Tony Kumar")
    assert basis == STRONG_NAME_SIMILARITY


# ---------------------------------------------------------------------------
# Expanded validation (safety-review item 2): representative Indian names --
# initials, aliases, common surnames, reordering, and different individuals
# who merely share a common name. Every assertion here is pure evidence
# (basis/score) -- nothing in this module or its caller ever auto-merges
# regardless of the result; the only action an overlap ever triggers
# anywhere in this codebase is withholding + recording for human review
# (see etl3.merger.accused_split.build_split_rows).
# ---------------------------------------------------------------------------

def test_common_surname_different_given_names_do_not_overlap():
    """Two different people who merely share a common Indian surname
    ("Kumar") but have clearly different given names must not be flagged
    -- the surname alone is not evidence of shared identity."""
    basis, score = name_overlap_score("Ramesh Kumar", "Suresh Kumar")
    assert basis is None
    assert score < 0.60


def test_common_given_name_different_surnames_do_not_overlap():
    basis, score = name_overlap_score("Mohammed Ali", "Mohammed Aslam")
    assert basis is None


def test_elder_younger_naming_convention_correctly_not_flagged():
    """"Pedda" (elder) and "China" (younger) are a standard Telugu naming
    convention distinguishing two DIFFERENT people (often close relatives)
    who otherwise share a base name -- these must not be treated as the
    same person just because the rest of the name matches."""
    basis, score = name_overlap_score("Pedda Obulesu", "China Obulesu")
    assert basis is None
    assert score < 0.60


def test_spelling_variant_of_the_same_convention_word_does_flag():
    """By contrast, "Chinna"/"China" are spelling variants of the SAME
    word (not the elder/younger distinction above) -- this is a genuine
    same-person spelling-drift pair and correctly flags for review."""
    basis, score = name_overlap_score("Chinna Obulesu", "China Obulesu")
    assert basis == STRONG_NAME_SIMILARITY


def test_reordered_three_token_indian_name_flags_as_exact_reordered():
    basis, score = name_overlap_score("Nagesh Babu Reddy", "Reddy Nagesh Babu")
    assert basis == EXACT_TOKEN_SET_REORDERED
    assert score == 0.97


def test_alias_marker_overlapping_a_plain_name_flags_for_review():
    """CCTNS raw text sometimes carries an alias marker ("@", "alias") --
    this module has no concept of what that punctuation means, it just
    compares normalized text. A segment carrying an alias that is also a
    plain existing accused's whole name must flag (ambiguous: could be
    one person known by that alias, or a coincidence) -- never silently
    ignored and never auto-merged."""
    basis, score = name_overlap_score("Pappu @ Rajesh", "Rajesh")
    assert basis == STRONG_NAME_SIMILARITY


def test_relative_marker_suffix_still_flags_on_shared_given_name():
    basis, score = name_overlap_score("Ramesh S/O Krishna", "Ramesh")
    assert basis == STRONG_NAME_SIMILARITY


def test_initial_in_place_of_a_full_name_is_a_known_false_negative():
    """Documented limitation, not a bug: an initial ("S.", "K.") never
    fuzzy-matches the word it stands for (the per-token ratio is far
    below TOKEN_MATCH_RATIO), so a name using an initial scores BELOW the
    overlap threshold against the equivalent full name even when a human
    would recognize them as potentially the same person. This is an
    accepted false-negative tradeoff (fewer review-queue false positives)
    -- see DOPAMS_ETL3_ACCUSED_RECONCILIATION_IMPLEMENTATION_REPORT.md
    section 10 for the operational implication. This test exists so a
    future change to the scoring function cannot silently alter this
    behavior without a test failing."""
    basis, score = name_overlap_score("S. Venkatesh", "Srinivas Venkatesh")
    assert basis is None
    assert score < 0.60


def test_common_abbreviation_mohd_is_a_known_false_negative():
    """"Mohd." is an extremely common Indian-record abbreviation for
    "Mohammed"/"Mohammad" -- but "mohd" vs "mohammed" scores below
    TOKEN_MATCH_RATIO (0.667 < 0.78), so this pair is NOT flagged.
    Documented limitation (see test above); not fixed here because
    lowering the per-token ratio to catch this risks materially more
    false-positive review-queue noise across the dataset and was not
    empirically re-validated against production data for this pass."""
    basis, score = name_overlap_score("Mohd. Yusuf", "Mohammed Yusuf")
    assert basis is None


def test_wholly_different_indian_names_sharing_no_tokens_do_not_overlap():
    basis, score = name_overlap_score("Narasimha Rao Gundu", "Lakshmi Devi Pillai")
    assert basis is None
    assert score == 0.0


# ---------------------------------------------------------------------------
# Placeholder / blank names (scenario #13) -- must behave like any other
# string, never specially interpreted, never crash on empty input.
# ---------------------------------------------------------------------------

def test_placeholder_names_compare_like_ordinary_text():
    # Two placeholder rows on the same crime score an exact match on the
    # placeholder TEXT -- this module never knows what "absconding" means;
    # it is the caller's job (build_split_rows is never even invoked on a
    # bare placeholder, since detect_multi_accused returns confidence
    # "none" for it -- see test_multi_accused.py) to avoid treating that
    # as evidence of a real duplicate person.
    basis, score = name_overlap_score("absconding", "absconding")
    assert basis == EXACT_NORMALIZED_NAME


def test_blank_or_none_never_matches_anything():
    assert name_overlap_score("", "Ravi Kumar") == (None, 0.0)
    assert name_overlap_score(None, "Ravi Kumar") == (None, 0.0)
    assert name_overlap_score("Ravi Kumar", None) == (None, 0.0)
    assert name_overlap_score("", "") == (None, 0.0)


# ---------------------------------------------------------------------------
# find_crime_overlaps: the per-segment lookup build_split_rows actually
# calls. Scenario #1/#2/#3 (combined vs existing coded accused; complete
# and partial overlap) and #15 (crime scoping is the CALLER's job -- this
# function trusts whatever list it's given and never queries a crime_id).
# ---------------------------------------------------------------------------

def _fir_869_segments():
    return [
        {"segment_index": 1, "raw_text": "Divi Sampath"},
        {"segment_index": 2, "raw_text": "Syed Ameer Ali"},
        {"segment_index": 3, "raw_text": "Bebri koushal Naik"},
        {"segment_index": 4, "raw_text": "Kattimani Mandira"},
    ]


def _fir_869_existing_accused():
    return [
        ("acc-A1", "Sampath Goud ddivi"),
        ("acc-A2", "Amir Ali Syed"),
        ("acc-CCL3", "kAUSHAL Hebri"),
        ("acc-A4", "Mandira Kattimani"),
    ]


def test_complete_overlap_fir_869_pattern():
    """Scenario #1 and #10 (combined-and-separate overlap): all four
    segments of the real FIR 869/2026 pack match their four pre-existing,
    independently-coded accused -- every segment must be withheld."""
    overlaps = find_crime_overlaps(_fir_869_segments(), _fir_869_existing_accused())
    assert set(overlaps.keys()) == {1, 2, 3, 4}
    assert overlaps[1]["accused_id"] == "acc-A1"
    assert overlaps[2]["accused_id"] == "acc-A2"
    assert overlaps[3]["accused_id"] == "acc-CCL3"
    assert overlaps[4]["accused_id"] == "acc-A4"


def test_partial_overlap_only_matching_segments_withheld():
    """Scenario #3: partial overlap must not silently discard the clear
    segments -- only the one(s) that actually match something existing
    are reported; an unrelated, genuinely new segment is absent from the
    result entirely (the caller's signal to proceed with it normally)."""
    segments = _fir_869_segments() + [{"segment_index": 5, "raw_text": "Brand New Person"}]
    overlaps = find_crime_overlaps(segments, _fir_869_existing_accused())
    assert set(overlaps.keys()) == {1, 2, 3, 4}
    assert 5 not in overlaps


def test_no_overlap_when_existing_roster_is_unrelated():
    segments = [{"segment_index": 1, "raw_text": "Divi Sampath"}]
    overlaps = find_crime_overlaps(segments, [("acc-X", "Totally Unrelated Person")])
    assert overlaps == {}


def test_empty_existing_roster_never_withholds_anything():
    overlaps = find_crime_overlaps(_fir_869_segments(), [])
    assert overlaps == {}


def test_strongest_match_wins_when_multiple_candidates_score():
    """Only ever the single best match per segment is reported -- nothing
    downstream acts on a second-best candidate, so returning more than one
    would be misleading, not more thorough."""
    segments = [{"segment_index": 1, "raw_text": "Ravi Kumar"}]
    existing = [
        ("acc-weak", "Ravi Something Else"),  # weaker match
        ("acc-strong", "Ravi Kumar"),         # exact match
    ]
    overlaps = find_crime_overlaps(segments, existing)
    assert overlaps[1]["accused_id"] == "acc-strong"
    assert overlaps[1]["score"] == 1.0


def test_crime_scoping_is_the_callers_responsibility_not_this_functions():
    """Scenario #15 (multiple-FIR relationships): this function has no
    concept of a crime_id at all -- it will happily "match" against
    whatever list it is handed. The guarantee that the list only ever
    contains accused from the SAME crime (and never the parent itself)
    lives entirely in the caller (etl3.enrichment.runner._multi_accused_split
    / _load_existing_crime_rosters), which is covered separately in
    test_multi_accused.py's disposable-DB tests. This test just documents
    that boundary so it is not mistaken for a gap in accused_overlap.py."""
    segments = [{"segment_index": 1, "raw_text": "Ravi Kumar"}]
    existing_from_a_different_crime_in_reality = [("acc-other-fir", "Ravi Kumar")]
    overlaps = find_crime_overlaps(segments, existing_from_a_different_crime_in_reality)
    assert 1 in overlaps  # by design -- scoping must happen before calling this


def main():
    check("exact normalized match", test_exact_match)
    check("exact token-set reordered", test_exact_token_set_reordered)
    check("strong similarity: asymmetric extra token", test_strong_similarity_asymmetric_extra_token)
    check("strong similarity: spelling drift + reorder", test_strong_similarity_spelling_drift_and_reorder)
    check("strong similarity: severe letter transposition", test_strong_similarity_severe_letter_transposition)
    check("different people sharing one common token: no overlap", test_different_people_sharing_one_common_token_do_not_overlap)
    check("wholly different names: no overlap", test_wholly_different_names_do_not_overlap)
    check("ambiguous partial name still flags for review", test_ambiguous_partial_name_still_flags_for_review)
    check("common surname, different given names: no overlap", test_common_surname_different_given_names_do_not_overlap)
    check("common given name, different surnames: no overlap", test_common_given_name_different_surnames_do_not_overlap)
    check("elder/younger (Pedda/China) naming convention correctly not flagged", test_elder_younger_naming_convention_correctly_not_flagged)
    check("spelling variant of the same convention word does flag", test_spelling_variant_of_the_same_convention_word_does_flag)
    check("reordered three-token Indian name: exact token-set reordered", test_reordered_three_token_indian_name_flags_as_exact_reordered)
    check("alias marker overlapping a plain name flags for review", test_alias_marker_overlapping_a_plain_name_flags_for_review)
    check("relative-name (S/O) suffix still flags on shared given name", test_relative_marker_suffix_still_flags_on_shared_given_name)
    check("initial in place of full name: documented false negative", test_initial_in_place_of_a_full_name_is_a_known_false_negative)
    check("common abbreviation 'Mohd.': documented false negative", test_common_abbreviation_mohd_is_a_known_false_negative)
    check("wholly different Indian names sharing no tokens: no overlap", test_wholly_different_indian_names_sharing_no_tokens_do_not_overlap)
    check("placeholder names compare like ordinary text", test_placeholder_names_compare_like_ordinary_text)
    check("blank/none never matches anything", test_blank_or_none_never_matches_anything)
    check("complete overlap: FIR 869/2026 pattern", test_complete_overlap_fir_869_pattern)
    check("partial overlap: only matching segments withheld", test_partial_overlap_only_matching_segments_withheld)
    check("no overlap when roster is unrelated", test_no_overlap_when_existing_roster_is_unrelated)
    check("empty roster never withholds anything", test_empty_existing_roster_never_withholds_anything)
    check("strongest match wins among multiple candidates", test_strongest_match_wins_when_multiple_candidates_score)
    check("crime scoping is the caller's responsibility", test_crime_scoping_is_the_callers_responsibility_not_this_functions)
    print("all accused_overlap unit tests passed")


if __name__ == "__main__":
    main()
