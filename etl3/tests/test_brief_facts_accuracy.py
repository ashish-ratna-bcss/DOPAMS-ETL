"""Regression tests for the brief-facts-only drug extraction accuracy pass.

Grounded in a reproducible random + stratified sample of 151 real FIR Brief
Facts (seed 42; see the accompanying edge-case reference report) drawn from
the Excel reference workbook `DOPAMS_FIRs_2026-11-06_12-05.xlsx` and from the
497-crime still-unsupported pool left by the prior quantity-validation fix.
Every case below is tied to an actual observed pattern, not a hypothetical.

Covers:
  - Non-drug seized property (mobile phones, cash, vehicles, cell phones,
    laptops, polythene covers) must never become a drug_extractions row
    (original audit Finding 1; confirmed recurring in 7+ sampled FIRs).
  - A count with no stated unit must never be guessed into a weight
    ("2 Mobile Phones" -> weight_g=2; "12 LSD blots" -> weight_g=12).
  - Fraction quantities ("1½ Kgs", "3 ½ Kgs", "1 1/2 Kg", "6(1/2)KG"),
    recurring across multiple independent V1-era FIRs in the still-bad pool.
  - The "N <items> each <weight>" multiplicative pattern ("5 Ganja packets
    each one 100 grams", "19 packets (each packet wg about 10 grams)").
  - Ambiguous suspected-substance seizures (quantity/worth stated, identity
    only "suspected"/generic -- SNo 5210/5290 pattern) are kept for manual
    review, never silently dropped and never collapsed into "No drug Found".
  - The crime-level "Drug found" / "No drug Found" / "Manual review"
    classification (runner._finalize_drug_extraction), including that a
    confirmed drug always wins over leftover ambiguous residue, and that
    "No drug Found" is never written as a fabricated drug_extractions row.

Run directly (``python etl3/tests/test_brief_facts_accuracy.py``) or via
pytest; both work, matching this repo's other etl3/tests/test_*.py files.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.enrichment.ai import (
    REASON_NON_DRUG_PROPERTY,
    REASON_SOURCE_UNSUPPORTED,
    is_non_drug_property,
    parse_drug_response,
    source_mentions_quantity,
    validate_ai_drug_item,
)
from etl3.enrichment.project import ai_drug_rows
from etl3.enrichment.rules import standardize_measurement


# ---------------------------------------------------------------------------
# Non-drug seized property must never become a drug row.
# ---------------------------------------------------------------------------

def test_mobile_phone_rejected_as_non_drug_property():
    result = validate_ai_drug_item(
        {"raw_drug_name": "Mobile Phones", "raw_quantity": 4, "raw_unit": None},
        "Seized one OnePlus mobile phone and Ganja from the accused.",
    )
    assert result["reject_reason"] == REASON_NON_DRUG_PROPERTY


def test_cash_rejected_as_non_drug_property():
    result = validate_ai_drug_item(
        {"raw_drug_name": "Cash", "raw_quantity": 3000, "raw_unit": "Rs"},
        "Seized Rs 3000 cash from the accused along with Ganja.",
    )
    assert result["reject_reason"] == REASON_NON_DRUG_PROPERTY


def test_vehicle_and_bike_rejected_as_non_drug_property():
    # "Cell Phone" is the model's literal raw_drug_name in the real sample
    # (crime 6a57ff66eb9b8d146dde070c); the brand/model text it later
    # resolves to ("Realme Narzo 50-A") is not itself checked here --
    # validation gates on raw_drug_name, before any KB resolution.
    for name in ("Vehicle", "TVS Star City Bike", "Cell Phone"):
        result = validate_ai_drug_item(
            {"raw_drug_name": name, "raw_quantity": 1, "raw_unit": None},
            f"Seized a {name} along with Ganja from the accused.",
        )
        assert result["reject_reason"] == REASON_NON_DRUG_PROPERTY, name


def test_polythene_cover_and_laptop_rejected():
    for name in ("Polythene Cover", "Laptop", "SIM Card"):
        assert is_non_drug_property(name), name


def test_real_drug_name_not_misflagged_as_property():
    """Word-boundary matching: "gunja" contains "gun", "Carfentanil" contains
    "car", but neither is a whole-word match for a property term."""
    assert not is_non_drug_property("gunja")
    assert not is_non_drug_property("Carfentanil")
    assert not is_non_drug_property("Ganja")
    assert not is_non_drug_property("Cocaine")


def test_non_drug_property_rows_excluded_from_ai_drug_rows():
    parsed = parse_drug_response(
        '{"drugs":['
        '{"raw_drug_name":"Ganja","raw_quantity":2,"raw_unit":"kg","drug_form":"solid"},'
        '{"raw_drug_name":"Mobile Phones","raw_quantity":2,"raw_unit":null,"drug_form":"solid"}'
        ']}'
    )
    rows = ai_drug_rows(
        "C1", "V2", parsed["drugs"], [("ganja", "Ganja")],
        source_text="Seized 2 Kgs of Ganja and two Mobile Phones from the accused.",
    )
    assert len(rows) == 1
    assert rows[0]["primary_drug_name"] == "Ganja"
    assert any(r["reason"] == REASON_NON_DRUG_PROPERTY for r in rows.rejections)


# ---------------------------------------------------------------------------
# A count with no stated unit must never be guessed into a weight.
# ---------------------------------------------------------------------------

def test_unitless_count_does_not_become_weight():
    """Real observed bug: "12 LSD blots" with raw_unit=None and the model's
    default drug_form="solid" silently became weight_g=12 (12 grams). The
    fix must stop that fabricated value; a pre-existing, unrelated fallback
    elsewhere in this function may still turn a fully-unmeasured row into
    weight_g=0.0 (zero, not the invented count) rather than None -- either
    is an acceptable "not fabricated", only the count value itself is not."""
    measured = standardize_measurement(12, None, drug_form="solid", raw_drug_name="LSD blots")
    assert measured["weight_g"] in (None, 0.0)
    assert measured["count_total"] is None


def test_unitless_count_mobile_phones_does_not_become_weight():
    measured = standardize_measurement(2, None, drug_form="solid", raw_drug_name="Mobile Phones")
    assert measured["weight_g"] in (None, 0.0)


def test_known_unit_still_resolves_normally():
    """The fix only removes the no-unit guess; a real unit still works."""
    measured = standardize_measurement(12, "blots", drug_form="solid", raw_drug_name="LSD blots")
    assert measured["count_total"] == 12


def test_unrecognized_but_present_unit_still_uses_drug_form_fallback():
    """A unit WAS given, just not one of the tabled ones -- still allowed to
    fall back on drug_form, since the model gave *some* evidence of a unit."""
    measured = standardize_measurement(53, "small containers", drug_form="liquid", raw_drug_name="Hash Oil")
    assert measured["volume_ml"] == 53


# ---------------------------------------------------------------------------
# Fraction quantities, recurring across multiple real V1-era FIRs.
# ---------------------------------------------------------------------------

def test_unicode_fraction_one_and_half():
    assert source_mentions_quantity(1.5, "seized 1½ Kgs Ganja from the accused.", unit="kg")


def test_unicode_fraction_with_space():
    assert source_mentions_quantity(3.5, "found in possession of 3 ½ Kgs of Contraband Ganja.", unit="kg")


def test_ascii_mixed_fraction_with_space():
    assert source_mentions_quantity(1.5, "seized 1 1/2 Kg s Ganja from the possession of accused.", unit="kg")


def test_ascii_fraction_in_parentheses():
    # Real observed text split the unit letters with a space ("6(1/2)K G
    # Ganga"); that specific letter-split is a documented remaining gap
    # (not fixed here). The parenthesized-fraction pattern itself is real
    # and fixed -- exercised here with the unit written normally ("Kgs").
    assert source_mentions_quantity(6.5, "the accused confessed 6(1/2)Kgs Ganga illegally transport.", unit="kg")


def test_bare_half_kg():
    assert source_mentions_quantity(0.5, "found him corrign conttaing Ganja 1/2 kg immediatle.", unit="kg")


def test_fraction_date_not_misread_as_quantity():
    """"1/2/2024" is a date, not a fraction: the trailing "/2024" blocks the
    bare-fraction match (negative lookahead), so this must NOT match 1 kg."""
    assert not source_mentions_quantity(1.0, "Occurred on 1/2/2024 seizure of narcotics.", unit="kg")


# ---------------------------------------------------------------------------
# "N <items> each <weight>" multiplicative packet pattern.
# ---------------------------------------------------------------------------

def test_n_packets_each_weight_matches_total():
    assert source_mentions_quantity(
        500, "on weighing it was found that there were 5 Ganja packets each one 100 grams of dry Ganja.",
        unit="grams",
    )


def test_n_packets_parenthetical_each_matches_total():
    assert source_mentions_quantity(
        190, "seized remaining dry ganja of 19 packets (each packet wg about 10 grams).",
        unit="grams",
    )


def test_each_multiplier_does_not_fire_without_the_word_each():
    """Guard: a bare count near a weight mention, with no "each", must not
    be treated as a multiplier (would wrongly inflate unrelated numbers)."""
    assert not source_mentions_quantity(
        500, "5 accused persons were produced along with 100 grams of ganja.",
        unit="grams",
    )


# ---------------------------------------------------------------------------
# Ambiguous suspected-substance seizures (SNo 5210 / 5290 pattern): kept for
# manual review, never silently dropped, never invented a drug name.
# ---------------------------------------------------------------------------

def test_ambiguous_suspected_bundle_kept_for_review():
    parsed = parse_drug_response(
        '{"drugs":[{"raw_drug_name":null,"raw_quantity":46,"raw_unit":"kg",'
        '"drug_form":"solid","seizure_worth":920000}]}'
    )
    assert parsed["drugs"] == []
    assert len(parsed["ambiguous"]) == 1
    assert parsed["ambiguous"][0]["raw_quantity"] == 46
    assert parsed["ambiguous"][0]["seizure_worth"] == 920000


def test_ambiguous_requires_real_evidence_not_just_a_blank_item():
    """A null-name item with no quantity/unit/worth at all is genuinely
    nothing -- it must NOT be promoted to "ambiguous" (that would turn every
    ordinary empty response into a spurious manual-review case)."""
    parsed = parse_drug_response('{"drugs":[{"raw_drug_name":null,"raw_quantity":0}]}')
    assert parsed["drugs"] == []
    assert parsed["ambiguous"] == []


def test_ambiguous_with_unit_only_also_kept():
    parsed = parse_drug_response(
        '{"drugs":[{"raw_drug_name":null,"raw_quantity":0,"raw_unit":"sachets"}]}'
    )
    assert len(parsed["ambiguous"]) == 1


# ---------------------------------------------------------------------------
# Crime-level classification: Drug found / No drug Found / Manual review.
# ---------------------------------------------------------------------------

def _fake_conn():
    conn = mock.MagicMock()
    cur = mock.MagicMock()
    cur.__enter__ = mock.MagicMock(return_value=cur)
    cur.__exit__ = mock.MagicMock(return_value=False)
    conn.cursor.return_value = cur
    return conn


def _row_list(rows, rejections=None):
    rl = mock.MagicMock()
    rl.__iter__ = mock.Mock(return_value=iter(rows))
    rl.__bool__ = mock.Mock(return_value=bool(rows))
    rl.rejections = rejections or []
    return rl


def test_finalize_drug_found_when_rows_present():
    from etl3.enrichment import runner as R

    conn = _fake_conn()
    rows = _row_list([{"extraction_id": "e1", "crime_id": "C1", "input_hash": "h"}])
    with mock.patch.object(R, "upsert_rows") as upsert:
        result = R._finalize_drug_extraction(
            conn, "run", "C1", "V2", {"drugs": [{"raw_drug_name": "Ganja"}], "ambiguous": []}, rows,
        )
    assert result["drug_finding_status"] == R.DRUG_FOUND
    finding_calls = [c for c in upsert.call_args_list if c.args[1] == "drug_finding_status"]
    assert len(finding_calls) == 1
    assert finding_calls[0].args[3][0]["status"] == "Drug found"


def test_finalize_no_drug_found_when_model_proposes_nothing():
    from etl3.enrichment import runner as R

    conn = _fake_conn()
    rows = _row_list([])
    with mock.patch.object(R, "upsert_rows") as upsert:
        result = R._finalize_drug_extraction(
            conn, "run", "C1", "V2", {"drugs": [], "ambiguous": []}, rows,
        )
    assert result["drug_finding_status"] == R.NO_DRUG_FOUND
    finding_calls = [c for c in upsert.call_args_list if c.args[1] == "drug_finding_status"]
    assert finding_calls[0].args[3][0]["status"] == "No drug Found"
    # Never a fabricated drug_extractions upsert for a no-drug crime.
    drug_calls = [c for c in upsert.call_args_list if c.args[1] == "drug_extractions"]
    assert drug_calls == []


def test_finalize_no_drug_found_when_only_property_items_rejected():
    from etl3.enrichment import runner as R

    conn = _fake_conn()
    rows = _row_list([], rejections=[{"raw_drug_name": "Mobile Phones", "reason": REASON_NON_DRUG_PROPERTY}])
    with mock.patch.object(R, "upsert_rows"):
        result = R._finalize_drug_extraction(
            conn, "run", "C1", "V2",
            {"drugs": [{"raw_drug_name": "Mobile Phones"}], "ambiguous": []}, rows,
        )
    assert result["drug_finding_status"] == R.NO_DRUG_FOUND


def test_finalize_manual_review_for_ambiguous_suspected_substance():
    from etl3.enrichment import runner as R

    conn = _fake_conn()
    rows = _row_list([])
    ambiguous = [{"raw_quantity": 46, "raw_unit": "kg", "seizure_worth": 920000}]
    with mock.patch.object(R, "upsert_rows") as upsert:
        result = R._finalize_drug_extraction(
            conn, "run", "C1", "V2", {"drugs": [], "ambiguous": ambiguous}, rows,
        )
    assert result["drug_finding_status"] == R.MANUAL_REVIEW
    finding_calls = [c for c in upsert.call_args_list if c.args[1] == "drug_finding_status"]
    assert finding_calls[0].args[3][0]["status"] == "Manual review"


def test_finalize_confirmed_drug_wins_over_leftover_ambiguous_item():
    """A crime with one confirmed drug AND one separate ambiguous/suspected
    item is still "Drug found" -- the confirmed identity always wins."""
    from etl3.enrichment import runner as R

    conn = _fake_conn()
    rows = _row_list([{"extraction_id": "e1", "crime_id": "C1", "input_hash": "h"}])
    ambiguous = [{"raw_quantity": 5, "raw_unit": "grams"}]
    with mock.patch.object(R, "upsert_rows"):
        result = R._finalize_drug_extraction(
            conn, "run", "C1", "V2",
            {"drugs": [{"raw_drug_name": "Ganja"}], "ambiguous": ambiguous}, rows,
        )
    assert result["drug_finding_status"] == R.DRUG_FOUND


def test_finalize_missing_ambiguous_key_defaults_safely():
    """Historical ai_extraction_attempts.parsed_response rows predate the
    "ambiguous" key; replay must not KeyError on them."""
    from etl3.enrichment import runner as R

    conn = _fake_conn()
    rows = _row_list([])
    with mock.patch.object(R, "upsert_rows"):
        result = R._finalize_drug_extraction(
            conn, "run", "C1", "V2", {"drugs": []}, rows,
        )
    assert result["drug_finding_status"] == R.NO_DRUG_FOUND


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
