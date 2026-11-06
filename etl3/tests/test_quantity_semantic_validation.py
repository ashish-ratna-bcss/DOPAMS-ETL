"""Regression tests for semantic drug-quantity validation.

Covers the Category E audit finding: source_mentions_quantity() rejected a
correctly-extracted quantity whenever Brief Facts expressed it as a split
("21 Kgs 499 grams") or converted ("500 grams" vs model's "0.5 kg") unit
expression instead of the model's single combined decimal appearing as a
literal substring.

Run directly (``python etl3/tests/test_quantity_semantic_validation.py``) or
via pytest; both work, matching etl3/tests/test_drug_ai_hardening.py's style.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.enrichment.ai import (
    REASON_SOURCE_UNSUPPORTED,
    parse_drug_response,
    source_mentions_quantity,
    validate_ai_drug_item,
)


# ---------------------------------------------------------------------------
# 1-6: the task's worked examples (split-unit, decimal, g->kg, mg->kg,
# mixed-unit, abbreviation/spelling variants).
# ---------------------------------------------------------------------------

def test_split_unit_kg_and_grams():
    assert source_mentions_quantity(21.499, "Police seized 21 Kgs 499 grams of ganja.", unit="kg")


def test_split_unit_kg_and_grams_variant_amounts():
    assert source_mentions_quantity(5.250, "a total of 5 Kgs 250 grams of ganja was recovered", unit="kg")


def test_decimal_quantity_direct():
    assert source_mentions_quantity(2.5, "seized 2.5 kilograms of ganja", unit="kg")


def test_grams_to_kilograms():
    assert source_mentions_quantity(0.5, "weighing about 500 grams was seized", unit="kg")


def test_milligrams_to_kilograms():
    assert source_mentions_quantity(0.00075, "a packet containing 750 milligrams was found", unit="kg")


def test_mixed_unit_kg_and_grams_with_and():
    assert source_mentions_quantity(1.5, "recovered 1 Kg and 500 grams of charas", unit="kg")


def test_mixed_unit_kg_and_grams_with_comma():
    assert source_mentions_quantity(15.631, "total weight 15 Kgs, 631 grams of ganja", unit="kg")


def test_unit_abbreviation_gms():
    assert source_mentions_quantity(0.25, "found 250 gms of ganja in his possession", unit="kg")


def test_unit_abbreviation_kilograms_plural():
    assert source_mentions_quantity(2.0, "seized 2 kilograms of contraband", unit="kg")


def test_unit_case_insensitive_and_whitespace():
    assert source_mentions_quantity(5.25, "SEIZED 5   KGS   250   GRAMS OF GANJA", unit="kg")


def test_volume_split_litre_and_ml():
    assert source_mentions_quantity(1.5, "recovered 1 Ltr and 500 ml of codeine syrup", unit="l")


# ---------------------------------------------------------------------------
# 7: multiple distinct quantities in one FIR. Each is individually
# source-supported; their arithmetic sum is NOT (guards against the
# model-summing bug separately observed in the audit, SNo 925).
# ---------------------------------------------------------------------------

MULTI_SEIZURE_TEXT = (
    "On search, 5 grams of ganja was recovered from accused A1 and "
    "3 grams of charas was recovered from accused A2."
)


def test_multiple_quantities_first_matches():
    assert source_mentions_quantity(5, MULTI_SEIZURE_TEXT, unit="g")


def test_multiple_quantities_second_matches():
    assert source_mentions_quantity(3, MULTI_SEIZURE_TEXT, unit="g")


def test_multiple_quantities_sum_not_accepted():
    # 5 + 3 = 8 was never stated as one seizure; must not validate.
    assert not source_mentions_quantity(8, MULTI_SEIZURE_TEXT, unit="g")


# ---------------------------------------------------------------------------
# 8: numbers unrelated to seized quantities (FIR number, section number,
# phone number, vehicle number) must never be read as a quantity just
# because the digits appear somewhere in the text.
# ---------------------------------------------------------------------------

UNRELATED_NUMBERS_TEXT = (
    "FIR No. 123/2021 of XYZ PS under Section 8(c) r/w 20(b)(ii)(C) NDPS Act. "
    "Accused was contacted on phone number 9848012345 and was travelling in "
    "vehicle bearing number TS09AB1234. No contraband was found on his person."
)


def test_fir_number_not_treated_as_quantity():
    assert not source_mentions_quantity(123, UNRELATED_NUMBERS_TEXT, unit="kg")


def test_section_number_not_treated_as_quantity():
    assert not source_mentions_quantity(20, UNRELATED_NUMBERS_TEXT, unit="kg")


def test_phone_number_not_treated_as_quantity():
    assert not source_mentions_quantity(9848012345, UNRELATED_NUMBERS_TEXT, unit="g")


def test_vehicle_number_fragment_not_treated_as_quantity():
    assert not source_mentions_quantity(9, UNRELATED_NUMBERS_TEXT, unit="kg")


# ---------------------------------------------------------------------------
# 9: quantity present but drug name absent -> parser drops the whole item
# (Category C root cause; unrelated to this fix, confirmed unchanged).
# ---------------------------------------------------------------------------

def test_quantity_present_name_absent_item_dropped():
    parsed = parse_drug_response(
        '{"drugs":[{"raw_drug_name":null,"raw_quantity":5,"raw_unit":"grams",'
        '"drug_form":"solid","seizure_worth":0,"worth_scope":"individual"}]}'
    )
    assert parsed["drugs"] == []


# ---------------------------------------------------------------------------
# 10: drug name present but quantity absent -> name still validates; no
# quantity-rejection is raised for a quantity that was never claimed.
# ---------------------------------------------------------------------------

def test_name_present_quantity_absent_not_rejected_for_quantity():
    result = validate_ai_drug_item(
        {"raw_drug_name": "Ganja", "raw_quantity": None, "raw_unit": None},
        "Ganja packets were seized from the accused.",
    )
    assert result["reject_reason"] is None
    assert result["raw_quantity"] is None
    assert "quantity_reject" not in result


# ---------------------------------------------------------------------------
# 11: ambiguous quantity expressions (a stated range) are rejected, not
# guessed, even when the AI's figure falls inside the range.
# ---------------------------------------------------------------------------

def test_ambiguous_range_expression_rejected():
    text = "approximately between 20 and 22 kgs of ganja was estimated to be stored there"
    assert not source_mentions_quantity(21, text, unit="kg")


# ---------------------------------------------------------------------------
# 12: existing valid (pre-fix) behaviour is preserved — a quantity whose
# literal decimal already appears verbatim still validates, with or
# without a unit hint, and the full validate_ai_drug_item path still
# accepts a normal, unambiguous, single-expression seizure.
# ---------------------------------------------------------------------------

def test_existing_literal_match_still_works_no_unit():
    assert source_mentions_quantity(6.585, "total approximately 6.585 grams under a cover")


def test_existing_literal_match_still_works_with_unit():
    assert source_mentions_quantity(6.585, "total approximately 6.585 grams under a cover", unit="grams")


def test_full_validate_path_accepts_unambiguous_seizure():
    result = validate_ai_drug_item(
        {"raw_drug_name": "Ganja", "raw_quantity": 21.499, "raw_unit": "kg",
         "seizure_worth": 0, "drug_form": "solid"},
        "Police searched the house and seized 21 Kgs 499 grams of dry ganja "
        "kept in polythene covers.",
    )
    assert result["reject_reason"] is None
    assert result["raw_quantity"] == 21.499
    assert "quantity_reject" not in result


def test_count_style_quantity_unaffected_by_semantic_path():
    # No weight/volume unit -> legacy literal fallback, unchanged behaviour.
    result = validate_ai_drug_item(
        {"raw_drug_name": "intoxicated chocolates", "raw_quantity": 280, "raw_unit": "chocolates",
         "seizure_worth": 0, "drug_form": "count"},
        "Police seized (280) intoxicated chocolates from the accused.",
    )
    assert result["reject_reason"] is None
    assert result["raw_quantity"] == 280


def test_truly_unsupported_quantity_still_rejected():
    # Guards against weakening validation: a hallucinated quantity with no
    # textual support at all must still be rejected.
    result = validate_ai_drug_item(
        {"raw_drug_name": "Ganja", "raw_quantity": 99.0, "raw_unit": "kg",
         "seizure_worth": 0, "drug_form": "solid"},
        "Ganja packets were seized from the accused; weight not mentioned.",
    )
    assert result["reject_reason"] is None  # name itself is still source-supported
    assert result["raw_quantity"] is None
    assert result.get("quantity_reject") == "INVALID_QUANTITY"


def test_source_unsupported_name_unaffected():
    result = validate_ai_drug_item(
        {"raw_drug_name": "Heroin", "raw_quantity": 5, "raw_unit": "grams"},
        "Ganja packets were seized from the accused.",
    )
    assert result["reject_reason"] == REASON_SOURCE_UNSUPPORTED


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"OK  {fn.__name__}")
        except Exception as exc:
            failed += 1
            print(f"FAIL {fn.__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    if failed:
        raise SystemExit(failed)
    print("ALL PASSED")


if __name__ == "__main__":
    main()
