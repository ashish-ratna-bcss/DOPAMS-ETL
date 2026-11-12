"""Regression tests for independent (non-AI) missed-drug-mention detection.

Grounded in the historical full-database scan (17,311 eligible FIRs):
369 crimes confirmed with a locally-evidenced missing drug; 96 of those
had zero existing named drug extraction at all (recovered, provenance
'etl3_text_scan'). Every case below reproduces a real pattern found during
that scan, including the false positives caught and fixed before recovery.

Run directly (``python etl3/tests/test_missed_drug_scan.py``) or via pytest.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.enrichment.missed_drug_scan import (
    EXCLUDED_RAW_NAMES,
    find_missing_drug_mentions,
)


def _vocab(*pairs):
    """Build (pattern, raw_to_std) from (raw_name, standard_name) pairs,
    the same shape load_kb_vocab() returns, without touching the DB."""
    terms = sorted(pairs, key=lambda t: len(t[0]), reverse=True)
    pattern = re.compile(r"\b(" + "|".join(re.escape(r) for r, _ in terms) + r")\b", re.IGNORECASE)
    raw_to_std = {r.lower(): s for r, s in terms}
    return pattern, raw_to_std


# ---------------------------------------------------------------------------
# True positives: a confirmed miss, with local seizure-quantity evidence.
# ---------------------------------------------------------------------------

def test_zero_existing_rows_with_explicit_weight_is_confirmed():
    """Real case (crime 2036007070190): V1 cultivation FIR, (30) ganja
    plants seized, zero existing drug_extractions rows at all."""
    pattern, raw_to_std = _vocab(("ganja", "Ganja"))
    text = (
        "LW-1 and his force found (30) ganja plants rose mixing in cotton crop "
        "to an extent of 0-20 acre... collected ganja leafs as samples, got the "
        "Ganja plants photographed, weighing about 2 Kgs of dry ganja."
    )
    confirmed, review, hits = find_missing_drug_mentions(text, [], pattern, raw_to_std)
    assert confirmed == ["Ganja"]
    assert review == []


def test_second_distinct_drug_separately_itemized_is_confirmed():
    """Real case (crime 6464f5658413dec4487e9c03): MDMA and Cocaine already
    extracted; "ECSTASY PILLS (MDMA DRUG)-58...23 grams" is a separately
    itemized, separately weighed substance with its own KB standard name."""
    pattern, raw_to_std = _vocab(
        ("mdma", "MDMA"), ("cocaine", "Cocaine"), ("ecstasy pills", "Ecstasy"),
    )
    text = (
        "he had MDMA pills and cocaine drugs in his bag. Later found packets "
        "1).ECSTASY PILLS (MDMA DRUG)-58 are blue in color approx 23 grams, "
        "2). ECSTASY PILLS (MDMA DRUG)-38 are in pink color approx 18 grams."
    )
    existing = [("MDMA", "MDMA"), ("Cocaine", "Cocaine")]
    confirmed, review, hits = find_missing_drug_mentions(text, existing, pattern, raw_to_std)
    assert confirmed == ["Ecstasy"]


# ---------------------------------------------------------------------------
# Already covered: not flagged at all.
# ---------------------------------------------------------------------------

def test_already_extracted_drug_not_flagged():
    pattern, raw_to_std = _vocab(("ganja", "Ganja"))
    text = "Seized 2 Kgs of Ganja from the accused."
    confirmed, review, hits = find_missing_drug_mentions(
        text, [("Ganja", "Ganja")], pattern, raw_to_std,
    )
    assert confirmed == [] and review == []


def test_existing_row_stored_under_alias_spelling_still_counts_as_covered():
    pattern, raw_to_std = _vocab(("ganja", "Ganja"), ("gunja", "Ganja"))
    text = "found 50kg of gunja in the bags."
    # Existing row was persisted under the raw alias spelling "gunja", not
    # the canonical "Ganja" -- must still count as already covered.
    confirmed, review, hits = find_missing_drug_mentions(
        text, [("gunja", None)], pattern, raw_to_std,
    )
    assert confirmed == [] and review == []


# ---------------------------------------------------------------------------
# False-positive guards: generic appearance terms excluded outright.
# ---------------------------------------------------------------------------

def test_white_powder_excluded_from_vocabulary():
    """Real case: text says a white powder's "name is known to be an
    intoxicant called Alprazolam" (already extracted) -- "white powder"
    alone must never independently claim a missing Cocaine."""
    assert "white powder" in EXCLUDED_RAW_NAMES
    assert "whitish color substance" in EXCLUDED_RAW_NAMES


def test_bare_toddy_excluded_but_adulterated_toddy_kept():
    assert "toddy" in EXCLUDED_RAW_NAMES
    assert "adulterated toddy" not in EXCLUDED_RAW_NAMES


def test_bare_toddy_occupation_mention_not_flagged():
    """Real case (crime 65fcbffd0ce7a40c25ea7ddd): accused's occupation is
    "Toddy Business"; the actual seized substance is Alprazolam (already
    extracted). "toddy" alone must not independently claim a missing drug."""
    pattern, raw_to_std = _vocab(("alprazolam", "Alprazolam"), ("adulterated toddy", "Toddy (Adulterated/Spurious)"))
    text = "occup: Toddy Business r/o Ghnapoor village. Seized 500 grams of Alprazolam."
    confirmed, review, hits = find_missing_drug_mentions(
        text, [("Alprazolam", "Alprazolam")], pattern, raw_to_std,
    )
    assert confirmed == [] and review == []
    assert "toddy" not in hits


# ---------------------------------------------------------------------------
# Local-evidence requirement: same-substance aliasing and consumption-only
# mentions are routed to review, not auto-confirmed.
# ---------------------------------------------------------------------------

def test_same_substance_described_two_ways_routed_to_review_not_confirmed():
    """Real case (crime 6366c55792b5355d8bbf4a8b): "...narcotic substance of
    Ganja in the form of Liquid i.e. Hashish Oil is in the possession..." --
    only Hashish Oil was ever weighed; "Ganja" here is a generic descriptor
    for the same seizure, not a second one. Hashish/Weed Oil already
    extracted; "Ganja" must go to review, not auto-recovery."""
    pattern, raw_to_std = _vocab(("ganja", "Ganja"), ("hashish oil", "Hashish/Weed Oil"))
    text = (
        "received reliable information that narcotic substance of Ganja in the "
        "form of Liquid i.e. Hashish Oil is in the possession of person for "
        "consuming and sale." + (" filler text about the investigation" * 10) +
        " Later weighed the Hashish Oil and it was 50 grams."
    )
    existing = [("Hashish Oil", "Hashish/Weed Oil")]
    confirmed, review, hits = find_missing_drug_mentions(text, existing, pattern, raw_to_std)
    assert "Ganja" in review
    assert "Ganja" not in confirmed


def test_consumption_only_habit_mention_routed_to_review():
    """Real case (crime 658f1dc4a5a4402b2c62566e): "addicted for consuming of
    ganja" is background/habit narrative with no nearby seizure quantity;
    the actually-extracted drug is Hashish/Weed Oil from elsewhere in text."""
    pattern, raw_to_std = _vocab(("ganja", "Ganja"), ("hash oil", "Hashish/Weed Oil"))
    text = (
        "For last 2 years he was addicted for consuming of ganja. About 4 months "
        "back he came to Hyderabad. " + ("x" * 300) +
        " Seized 100 grams of Hash Oil from his possession under panchanama."
    )
    existing = [("Hash Oil", "Hashish/Weed Oil")]
    confirmed, review, hits = find_missing_drug_mentions(text, existing, pattern, raw_to_std)
    assert "Ganja" in review
    assert "Ganja" not in confirmed


def test_pure_consumption_no_seizure_anywhere_routed_to_review():
    """Real case (crime 6872a82a480835c1ea2a018e): 11 accused, confession +
    urine test positive for Ganja, explicitly no physical seizure anywhere
    in the FIR. Correctly produces zero drug_extractions rows today; must
    not be force-recovered as a confirmed miss."""
    pattern, raw_to_std = _vocab(("ganja", "Ganja"))
    text = (
        "he is addicted to consume Ganja along with his friends by procuring it "
        "from Sandeep. drug testing kit... it was showing positive for the "
        "consumption of Ganja."
    )
    confirmed, review, hits = find_missing_drug_mentions(text, [], pattern, raw_to_std)
    assert confirmed == []
    assert review == ["Ganja"]


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
