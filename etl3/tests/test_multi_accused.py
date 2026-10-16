"""Unit tests for conservative multi-accused pack detection and the pure,
DB-agnostic half of the split-row builder. No database -- pure functions
only. The DB-touching half (etl3.merger.accused_split.write_split_rows /
supersede_children / existing_active_children) requires migration 018 to be
applied and is reviewed, not exercised, here.

Run directly: python etl3/tests/test_multi_accused.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.enrichment.multi_accused import detect_multi_accused, trailer_address_text
from etl3.enrichment.names import clean_person_names
from etl3.merger.accused_split import (
    build_split_rows,
    diff_superseded_children,
    split_child_id,
)


def check(label, fn):
    try:
        fn()
        print(f"[PASS] {label}")
    except Exception as exc:
        print(f"[FAIL] {label}: {type(exc).__name__}: {exc}")
        raise


def _parent(accused_id="V1:FIR1:acc1", source_system="V1", crime_id="FIR1",
            person_id="V1:FIR1:acc1:person", run_id="run-1", as_of="2026-01-01T00:00:00Z"):
    return {
        "accused_id": accused_id,
        "source_system": source_system,
        "crime_id": crime_id,
        "person_id": person_id,
        "current_source_run_id": run_id,
        "current_as_of": as_of,
    }


# ---------------------------------------------------------------------------
# detect_multi_accused: high-confidence numbered packs
# ---------------------------------------------------------------------------

def test_two_numbered_names_high_confidence():
    r = detect_multi_accused("1) Abhishek Kumar, 2) Golla Srinivasulu")
    assert r["confidence"] == "high"
    assert r["method"] == "numbered_pack_v1"
    assert [s["raw_text"] for s in r["segments"]] == ["Abhishek Kumar", "Golla Srinivasulu"]
    assert [s["segment_index"] for s in r["segments"]] == [1, 2]
    assert [s["label_number"] for s in r["segments"]] == [1, 2]


def test_three_numbered_names_high_confidence():
    r = detect_multi_accused("1) Abhishek Kumar, 2) Golla Srinivasulu, 3) Mohammed Irfan")
    assert r["confidence"] == "high"
    assert len(r["segments"]) == 3
    assert r["segments"][2]["raw_text"] == "Mohammed Irfan"


def test_leading_zero_and_semicolon():
    r = detect_multi_accused("01) Ravi Kumar; 02) Mohan Rao")
    assert r["confidence"] == "high"
    assert [s["raw_text"] for s in r["segments"]] == ["Ravi Kumar", "Mohan Rao"]


def test_mixed_marker_punctuation_still_sequential():
    r = detect_multi_accused("1) Ravi Kumar, 2. Mohan Rao")
    assert r["confidence"] == "high"
    assert [s["raw_text"] for s in r["segments"]] == ["Ravi Kumar", "Mohan Rao"]


def test_numbered_names_with_initials_and_relationship_preserved():
    r = detect_multi_accused("1) A. Pawan Reddy, 2) K. Vamsi Krishna")
    assert r["confidence"] == "high"
    assert r["segments"][0]["raw_text"] == "A. Pawan Reddy"
    assert r["segments"][1]["raw_text"] == "K. Vamsi Krishna"

    r2 = detect_multi_accused("1) Kiran s/o Ram, 2) Suresh s/o Krishna")
    assert r2["confidence"] == "high"
    assert r2["segments"][0]["raw_text"] == "Kiran s/o Ram"
    assert r2["segments"][1]["raw_text"] == "Suresh s/o Krishna"


def test_shared_trailer_split_off_last_segment_only():
    r = detect_multi_accused(
        "1) Ramesh s/o Raju, 2) Suresh s/o Krishna, both R/o Hyderabad",
    )
    assert r["confidence"] == "high"
    assert r["shared_trailer_text"] == "both R/o Hyderabad"
    assert r["shared_address_text"] == "Hyderabad"
    assert r["segments"][0]["raw_text"] == "Ramesh s/o Raju"
    assert r["segments"][1]["raw_text"] == "Suresh s/o Krishna"


def test_trailer_address_text_helper():
    assert trailer_address_text("both R/o Hyderabad") == "Hyderabad"
    assert trailer_address_text("all R/o Secunderabad, Telangana") == "Secunderabad, Telangana"
    assert trailer_address_text(None) is None
    assert trailer_address_text("") is None


# ---------------------------------------------------------------------------
# detect_multi_accused: false-positive guards (must stay "none")
# ---------------------------------------------------------------------------

def test_single_leading_index_not_split():
    assert detect_multi_accused("1. Mohd Habeeb")["confidence"] == "none"


def test_initials_not_mistaken_for_markers():
    assert detect_multi_accused("A. Bhujangarao")["confidence"] == "none"
    assert detect_multi_accused("K. Vamsi Krishna")["confidence"] == "none"


def test_accused_code_style_markers_not_split():
    r = detect_multi_accused("A1) Ravi Kumar, A2) Suresh")
    assert r["confidence"] == "none"
    assert detect_multi_accused("A-1")["confidence"] == "none"


def test_phone_and_house_number_digits_not_mistaken_for_markers():
    assert detect_multi_accused("Ravi Kumar, Mobile 9876543210")["confidence"] == "none"
    assert detect_multi_accused("H.No. 1-2-3, Hyderabad")["confidence"] == "none"


def test_empty_and_none_input():
    for value in (None, "", "   "):
        r = detect_multi_accused(value)
        assert r["confidence"] == "none"
        assert r["segments"] == []


# ---------------------------------------------------------------------------
# detect_multi_accused: ambiguous numbering stays "low", never auto-split
# ---------------------------------------------------------------------------

def test_non_sequential_numbers_low_confidence():
    r = detect_multi_accused("1) Ravi Kumar, 3) Mohan Rao")
    assert r["confidence"] == "low"
    assert r["segments"] == []
    assert "non_sequential_marker_numbers" in r["warnings"]


def test_duplicate_marker_numbers_low_confidence():
    assert detect_multi_accused("1) Ravi Kumar, 1) Mohan Rao")["confidence"] == "low"


def test_out_of_order_numbers_low_confidence():
    assert detect_multi_accused("2) Ravi Kumar, 1) Mohan Rao")["confidence"] == "low"


def test_implausible_segment_low_confidence():
    r = detect_multi_accused("1) Ravi Kumar, 2) 12345")
    assert r["confidence"] == "low"
    assert "implausible_segment" in r["warnings"]


def test_excessively_long_segment_low_confidence():
    assert detect_multi_accused("1) Ravi Kumar, 2) " + ("Suresh " * 60))["confidence"] == "low"


def test_unnumbered_and_list_flagged_low_not_split():
    r = detect_multi_accused("Raju and Shiva")
    assert r["confidence"] == "low"
    assert r["method"] == "unnumbered_candidate"
    assert r["candidate_segments"] == ["Raju", "Shiva"]
    assert r["segments"] == []


def test_unnumbered_comma_list_flagged_low_not_split():
    r = detect_multi_accused("Ramesh, Suresh")
    assert r["confidence"] == "low"
    assert r["candidate_segments"] == ["Ramesh", "Suresh"]


# ---------------------------------------------------------------------------
# detect_multi_accused: "and N others" / "and others" never fabricates people
# ---------------------------------------------------------------------------

def test_and_n_others_recorded_not_fabricated():
    r = detect_multi_accused("Ismail Khan and 5 others")
    assert r["confidence"] == "none"
    assert r["segments"] == []
    assert r["other_count"] == 5
    assert r["other_count_known"] is True


def test_and_others_unknown_count_recorded_not_fabricated():
    r = detect_multi_accused("Ismail Khan and others")
    assert r["confidence"] == "none"
    assert r["other_count"] is None
    assert r["other_count_known"] is True


def test_and_others_trailing_a_numbered_pack():
    r = detect_multi_accused("1) Ravi Kumar, 2) Mohan Rao and 5 others")
    assert r["confidence"] == "high"
    assert r["other_count"] == 5
    assert r["segments"][1]["raw_text"] == "Mohan Rao"


def test_and_prefixed_final_marker_does_not_merge_into_previous_segment():
    """Real production case (accused_id V1:6aca34e6e316abdc89ad243f34ad1ab5):
    the last item of a numbered list is introduced with "and 4." rather
    than ", 4." -- without treating "and " as a marker boundary, segment 3
    would silently absorb "and 4. Manthri Shiva sai", fabricating a single
    garbled-name person out of two real accused."""
    r = detect_multi_accused(
        " 1. Karrothu Chandra, 2. Borrothu Prakash , 3. Maddula Venkata"
        " Shiva Shekar and 4. Manthri Shiva sai",
    )
    assert r["confidence"] == "high"
    assert [s["raw_text"] for s in r["segments"]] == [
        "Karrothu Chandra",
        "Borrothu Prakash",
        "Maddula Venkata Shiva Shekar",
        "Manthri Shiva sai",
    ]
    assert [s["label_number"] for s in r["segments"]] == [1, 2, 3, 4]


def test_and_prefixed_marker_simple_two_segment_case():
    r = detect_multi_accused("1) Ramesh Kumar and 2) Suresh Babu")
    assert r["confidence"] == "high"
    assert [s["raw_text"] for s in r["segments"]] == ["Ramesh Kumar", "Suresh Babu"]


# ---------------------------------------------------------------------------
# build_split_rows: pure row-building for persons_unified/accused_unified.
# Reuses nothing from names.py/address.py here on purpose -- cleaning and
# address resolution happen later via the existing, unmodified enrichment
# pipeline once these rows are real. This module only decides identity/
# provenance of the new rows.
# ---------------------------------------------------------------------------

def test_build_split_rows_basic_two_segments():
    person_rows, accused_rows, audit_rows, parsed = build_split_rows(
        _parent(), "1) Abhishek Kumar, 2) Golla Srinivasulu",
    )
    assert parsed["confidence"] == "high"
    assert len(person_rows) == 2 and len(accused_rows) == 2 and len(audit_rows) == 2

    assert {a["crime_id"] for a in accused_rows} == {"FIR1"}
    assert {a["derived_from_accused_id"] for a in accused_rows} == {"V1:FIR1:acc1"}
    assert {a["source_system"] for a in accused_rows} == {"V1"}
    assert accused_rows[0]["accused_id"] != accused_rows[1]["accused_id"]
    assert accused_rows[0]["person_id"] == person_rows[0]["person_id"]
    assert accused_rows[0]["unlinked_person_flag"] is False

    # Raw segment text preserved verbatim on the new person row -- cleaning
    # is the existing person_enrichment pipeline's job, not this module's.
    assert person_rows[0]["full_name"] == "Abhishek Kumar"
    assert person_rows[1]["full_name"] == "Golla Srinivasulu"
    assert person_rows[0]["derived_from_person_id"] == "V1:FIR1:acc1:person"

    # The original packed text is preserved in the audit trail, never on
    # the new rows themselves (they hold only their own segment).
    assert audit_rows[0]["raw_pack_text"] == "1) Abhishek Kumar, 2) Golla Srinivasulu"
    assert audit_rows[0]["parent_accused_id"] == "V1:FIR1:acc1"
    assert audit_rows[0]["created_accused_id"] == accused_rows[0]["accused_id"]
    assert audit_rows[0]["created_person_id"] == person_rows[0]["person_id"]


def test_build_split_rows_low_confidence_and_single_produce_nothing():
    person_rows, accused_rows, audit_rows, parsed = build_split_rows(
        _parent(), "1) Ravi Kumar, 3) Mohan Rao",
    )
    assert person_rows == [] and accused_rows == [] and audit_rows == []
    assert parsed["confidence"] == "low"

    person_rows2, accused_rows2, audit_rows2, parsed2 = build_split_rows(_parent(), "Mohd Habeeb")
    assert person_rows2 == [] and accused_rows2 == [] and audit_rows2 == []
    assert parsed2["confidence"] == "none"


def test_build_split_rows_never_fabricates_from_others():
    person_rows, accused_rows, audit_rows, parsed = build_split_rows(
        _parent(), "Ismail Khan and 5 others",
    )
    assert person_rows == [] and accused_rows == [] and audit_rows == []
    assert parsed["other_count"] == 5


def test_build_split_rows_shared_address_applies_identically_to_all():
    person_rows, accused_rows, audit_rows, parsed = build_split_rows(
        _parent(), "1) Ramesh s/o Raju, 2) Suresh s/o Krishna, both R/o Hyderabad",
    )
    assert parsed["shared_address_text"] == "Hyderabad"
    assert person_rows[0]["present_address_text"] == "Hyderabad"
    assert person_rows[1]["present_address_text"] == "Hyderabad"
    assert person_rows[0]["permanent_address_text"] == person_rows[1]["permanent_address_text"]


def test_duplicate_name_segment_does_not_contaminate_sibling_segment():
    """One segment of a pack is itself an exact-duplicate name artifact
    (e.g. "China China") -- collapsing it, via the existing
    clean_person_names reused per-segment by person_enrichment, must never
    affect the sibling segment's own (correctly different) name."""
    person_rows, accused_rows, _, parsed = build_split_rows(
        _parent(), "1) China China, 2) Ravi Kumar",
    )
    assert parsed["confidence"] == "high"
    assert person_rows[0]["full_name"] == "China China"  # raw segment, untouched here
    assert person_rows[1]["full_name"] == "Ravi Kumar"

    # person_enrichment cleans each persons_unified row independently --
    # simulate that per-row call exactly as runner._person_enrichment does.
    cleaned = [clean_person_names(p["full_name"]) for p in person_rows]
    assert cleaned[0]["cleaned_full_name"] == "China"
    assert cleaned[1]["cleaned_full_name"] == "Ravi Kumar"
    assert accused_rows[0]["person_id"] != accused_rows[1]["person_id"]


def test_build_split_rows_idempotent_rerun_same_ids():
    first_person, first_accused, first_audit, _ = build_split_rows(
        _parent(), "1) Abhishek Kumar, 2) Golla Srinivasulu",
    )
    second_person, second_accused, second_audit, _ = build_split_rows(
        _parent(), "1) Abhishek Kumar, 2) Golla Srinivasulu",
    )
    assert [a["accused_id"] for a in first_accused] == [a["accused_id"] for a in second_accused]
    assert [p["person_id"] for p in first_person] == [p["person_id"] for p in second_person]
    assert [a["input_hash"] for a in first_audit] == [a["input_hash"] for a in second_audit]


def test_split_child_id_changes_when_segment_text_corrected():
    before = split_child_id("acc1", 1, "Abhishek Kumar")
    after_typo_fix = split_child_id("acc1", 1, "Abhishek Kumarr")
    assert before != after_typo_fix
    assert before == split_child_id("acc1", 1, "Abhishek Kumar")


def test_build_split_rows_unlinked_parent_person_still_gets_own_split_person():
    # Parent has no linked person_id (the ~78 live V2 unlinked_person_flag
    # rows) -- a split child must still get ITS OWN new person_id rather
    # than crashing or silently reusing the (nonexistent) parent person.
    parent = _parent(person_id=None)
    person_rows, accused_rows, _, parsed = build_split_rows(
        parent, "1) Ravi Kumar, 2) Mohan Rao",
    )
    assert parsed["confidence"] == "high"
    assert person_rows[0]["person_id"] and person_rows[1]["person_id"]
    assert person_rows[0]["person_id"] != person_rows[1]["person_id"]
    assert person_rows[0]["derived_from_person_id"] is None


# ---------------------------------------------------------------------------
# diff_superseded_children: supersede-safety, never misattribute
# ---------------------------------------------------------------------------

def test_diff_keeps_unchanged_supersedes_changed():
    existing = ["X", "Y"]
    new_ids = ["X", "Z"]  # the child at Y's position was recomputed as Z
    assert diff_superseded_children(existing, new_ids) == ["Y"]


def test_diff_reordering_supersedes_both_instead_of_guessing():
    before_person, before_accused, _, _ = build_split_rows(_parent(), "1) Alpha, 2) Beta")
    after_person, after_accused, _, _ = build_split_rows(_parent(), "1) Beta, 2) Alpha")
    before_ids = [a["accused_id"] for a in before_accused]
    after_ids = [a["accused_id"] for a in after_accused]
    to_supersede = diff_superseded_children(before_ids, after_ids)
    # Reordering is lossy-but-safe: both old ids are superseded rather than
    # the module guessing "Beta moved from slot 2 to slot 1".
    assert set(to_supersede) == set(before_ids)


def test_diff_no_change_supersedes_nothing():
    assert diff_superseded_children(["X"], ["X"]) == []


def main():
    check("two numbered names", test_two_numbered_names_high_confidence)
    check("three numbered names", test_three_numbered_names_high_confidence)
    check("leading zero + semicolon", test_leading_zero_and_semicolon)
    check("mixed marker punctuation", test_mixed_marker_punctuation_still_sequential)
    check("initials and relationship preserved", test_numbered_names_with_initials_and_relationship_preserved)
    check("shared trailer split off last segment", test_shared_trailer_split_off_last_segment_only)
    check("trailer_address_text helper", test_trailer_address_text_helper)
    check("single leading index not split", test_single_leading_index_not_split)
    check("initials not mistaken for markers", test_initials_not_mistaken_for_markers)
    check("accused-code markers not split", test_accused_code_style_markers_not_split)
    check("phone/house-number digits not mistaken", test_phone_and_house_number_digits_not_mistaken_for_markers)
    check("empty and none input", test_empty_and_none_input)
    check("non-sequential numbers low confidence", test_non_sequential_numbers_low_confidence)
    check("duplicate marker numbers low confidence", test_duplicate_marker_numbers_low_confidence)
    check("out of order numbers low confidence", test_out_of_order_numbers_low_confidence)
    check("implausible segment low confidence", test_implausible_segment_low_confidence)
    check("excessively long segment low confidence", test_excessively_long_segment_low_confidence)
    check("unnumbered 'and' list flagged, not split", test_unnumbered_and_list_flagged_low_not_split)
    check("unnumbered comma list flagged, not split", test_unnumbered_comma_list_flagged_low_not_split)
    check("'and N others' recorded, not fabricated", test_and_n_others_recorded_not_fabricated)
    check("'and others' unknown count recorded", test_and_others_unknown_count_recorded_not_fabricated)
    check("'and others' trailing a numbered pack", test_and_others_trailing_a_numbered_pack)
    check("and-prefixed final marker does not merge into previous segment", test_and_prefixed_final_marker_does_not_merge_into_previous_segment)
    check("and-prefixed marker simple two-segment case", test_and_prefixed_marker_simple_two_segment_case)
    check("build_split_rows: basic two segments", test_build_split_rows_basic_two_segments)
    check("duplicate-name segment does not contaminate sibling", test_duplicate_name_segment_does_not_contaminate_sibling_segment)
    check("build_split_rows: low/none produce nothing", test_build_split_rows_low_confidence_and_single_produce_nothing)
    check("build_split_rows: never fabricate from others", test_build_split_rows_never_fabricates_from_others)
    check("build_split_rows: shared address applies to all", test_build_split_rows_shared_address_applies_identically_to_all)
    check("build_split_rows: idempotent rerun", test_build_split_rows_idempotent_rerun_same_ids)
    check("split_child_id changes on text correction", test_split_child_id_changes_when_segment_text_corrected)
    check("build_split_rows: unlinked parent person handled", test_build_split_rows_unlinked_parent_person_still_gets_own_split_person)
    check("diff: keeps unchanged, supersedes changed", test_diff_keeps_unchanged_supersedes_changed)
    check("diff: reordering supersedes both", test_diff_reordering_supersedes_both_instead_of_guessing)
    check("diff: no change supersedes nothing", test_diff_no_change_supersedes_nothing)
    print("all multi-accused unit tests passed")


if __name__ == "__main__":
    main()
