"""Regression tests for the Unidentified Substance classification rule.

Appearance-only descriptions ("white powder", "white paste", ...) must not
be auto-classified as Cocaine when the source never names an actual drug.
They resolve to kb.UNIDENTIFIED_SUBSTANCE instead -- a real, non-null
identity that keeps the drug_extractions row (unlike ignored_generic, which
drops the row entirely) -- while an explicit identification stated
elsewhere in the same text still wins.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.enrichment.kb import DrugKB, TIER_IGNORED_GENERIC, UNIDENTIFIED_SUBSTANCE, resolve_primary_name
from etl3.enrichment.project import _drug_row, ai_drug_rows
from etl3.enrichment.rules import resolve_drug_category

# Reproduces the real KB shape: appearance-only aliases point at the
# sentinel, real named drugs point at themselves.
APPEARANCE_KB_ITEMS = [
    ("cocaine", "Cocaine"),
    ("ganja", "Ganja"),
    ("white powder", UNIDENTIFIED_SUBSTANCE),
    ("white paste", UNIDENTIFIED_SUBSTANCE),
    ("unknown white powder", UNIDENTIFIED_SUBSTANCE),
    ("white colour powder", UNIDENTIFIED_SUBSTANCE),
]
IGNORE_TERMS = [
    "pills", "tablets", "liquid", "white", "drugs", "substance",
    # Production kb.drug_ignore_list also blocks the bare single-word
    # tokens of every appearance-based multi-word alias (powder/paste/
    # colour/color/maddi/lime/item/unknown) -- mirrored here so this
    # fixture matches real behaviour.
    "powder", "paste", "colour", "color", "maddi", "lime", "item", "unknown",
]


def _kb():
    return DrugKB(APPEARANCE_KB_ITEMS, ignore_terms=IGNORE_TERMS)


def test_white_powder_without_named_drug_is_unidentified():
    kb = _kb()
    name, tier = resolve_primary_name("white powder", kb.items, drug_kb=kb)
    assert name == UNIDENTIFIED_SUBSTANCE
    assert tier == "exact"


def test_white_paste_without_named_drug_is_unidentified():
    kb = _kb()
    name, tier = resolve_primary_name("white paste", kb.items, drug_kb=kb)
    assert name == UNIDENTIFIED_SUBSTANCE
    assert tier == "exact"


def test_white_powder_explicitly_identified_as_cocaine():
    kb = _kb()
    row = _drug_row(
        "V2:ai:C1:0", "C1", "V2", "etl3_ai", "C1",
        "white powder", 500, "grams", 0, kb.items, drug_kb_obj=kb,
        preferred_primary="Cocaine",
    )
    assert row["primary_drug_name"] == "Cocaine"
    assert row["kb_match_tier"] == "exact"
    assert row["weight_g"] == 500


def test_another_named_drug_described_as_white_powder_appearance():
    # Explicit identification (e.g. Ganja, stated elsewhere) wins over the
    # appearance-only placeholder match -- not only Cocaine is special-cased.
    kb = _kb()
    row = _drug_row(
        "V2:ai:C2:0", "C2", "V2", "etl3_ai", "C2",
        "white powder", 10, "grams", 0, kb.items, drug_kb_obj=kb,
        preferred_primary="Ganja",
    )
    assert row["primary_drug_name"] == "Ganja"


def test_appearance_plus_suspected_narcotic_wording_stays_unidentified():
    kb = _kb()
    # "suspected to be a narcotic substance" never names a real drug, so
    # preferred_primary is absent/empty -- placeholder must stand.
    row = _drug_row(
        "V2:ai:C3:0", "C3", "V2", "etl3_ai", "C3",
        "white powder", 500, "grams", 0, kb.items, drug_kb_obj=kb,
        preferred_primary=None,
    )
    assert row["primary_drug_name"] == UNIDENTIFIED_SUBSTANCE
    assert row["primary_drug_name"] != "Cocaine"


def test_quantity_retained_when_identity_is_unidentified():
    kb = _kb()
    row = _drug_row(
        "V2:ai:C4:0", "C4", "V2", "etl3_ai", "C4",
        "white powder", 500, "grams", 125000, kb.items, drug_kb_obj=kb,
    )
    assert row["primary_drug_name"] == UNIDENTIFIED_SUBSTANCE
    assert row["weight_g"] == 500
    assert row["seizure_worth"] == 125000


def test_unidentified_substance_category_label():
    assert resolve_drug_category(UNIDENTIFIED_SUBSTANCE) == "Unidentified"
    assert resolve_drug_category("Cocaine") == "Stimulant"


def test_no_seizure_evidence_returns_no_row():
    kb = _kb()
    row = _drug_row(
        "V2:ai:C5:0", "C5", "V2", "etl3_ai", "C5",
        None, None, None, 0, kb.items, drug_kb_obj=kb,
    )
    assert row is None


def test_multiple_drugs_including_one_unidentified_in_same_fir():
    kb = _kb()
    parsed_drugs = [
        {
            "raw_drug_name": "ganja", "raw_quantity": 5.0, "raw_unit": "kg",
            "primary_drug_name": "ganja", "drug_form": "solid",
            "seizure_worth": 50000, "worth_scope": "individual",
            "is_commercial": False, "confidence_score": 95,
            "extraction_metadata": {"source_sentence": "5 kg of ganja seized"},
        },
        {
            "raw_drug_name": "white powder", "raw_quantity": 500.0, "raw_unit": "grams",
            "primary_drug_name": None, "drug_form": "solid",
            "seizure_worth": 0, "worth_scope": "individual",
            "is_commercial": False, "confidence_score": 80,
            "extraction_metadata": {"source_sentence": "500 grams of white powder seized"},
        },
    ]
    source_text = "5 kg of ganja and 500 grams of white powder were seized."
    rows = ai_drug_rows("C6", "V2", parsed_drugs, kb.items, drug_kb_obj=kb, source_text=source_text)
    names = sorted(r["primary_drug_name"] for r in rows)
    assert names == ["Ganja", UNIDENTIFIED_SUBSTANCE]


def test_bare_single_word_tokens_do_not_match_through_multiword_alias():
    # Every appearance-alias token (powder, paste, colour/color, maddi,
    # lime, item) is a whole token of a multi-word KB key, so without the
    # ignore-list guard, substring_raw_in_kb would match it bare -- exactly
    # the same failure class as pills/tablets/liquid/white. Confirmed
    # pre-session gap: bare "colour"/"color" resolved to Cocaine/Ecstasy
    # through unrelated KB aliases before kb.drug_ignore_list was hardened.
    kb = _kb()
    for raw in ("powder", "paste", "colour", "color", "maddi", "lime", "item"):
        name, tier = resolve_primary_name(raw, kb.items, drug_kb=kb)
        assert name is None, (raw, name, tier)
        assert tier == TIER_IGNORED_GENERIC, (raw, tier)


def test_bare_white_still_ignored_generic():
    kb = _kb()
    name, tier = resolve_primary_name("white", kb.items, drug_kb=kb)
    assert name is None
    assert tier == TIER_IGNORED_GENERIC
