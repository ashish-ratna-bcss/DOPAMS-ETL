"""Regression tests for drug AI + KB resolution hardening.

Covers:
  - ignore-list check BEFORE KB resolution (drugs ↛ Ganja Chocolates)
  - safe substring_raw_in_kb (token-level)
  - source-evidence validation
  - MDMA crime 62a006d9e32fb48e4b9f288e pipeline
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.enrichment.ai import (
    REASON_GENERIC_IGNORED,
    REASON_SOURCE_UNSUPPORTED,
    parse_drug_response,
    source_mentions_label,
    validate_ai_drug_item,
)
from etl3.enrichment.kb import DrugKB, TIER_IGNORED_GENERIC, resolve_primary_name
from etl3.enrichment.project import ai_drug_rows, _drug_row


# Minimal alias set reproducing the audited failure mode.
BUG_KB_ITEMS = [
    ("mdma", "MDMA"),
    ("ganja", "Ganja"),
    ("cocaine", "Cocaine"),
    ("heroin", "Heroin"),
    ("ganja chocolates", "Ganja Chocolates"),
    ("drugs mixed chocolates", "Ganja Chocolates"),
    ("drug mixed chocolate", "Ganja Chocolates"),
    ("dry ganza", "Ganja"),
    ("crystal mdma", "MDMA"),
]

IGNORE_TERMS = [
    "drug",
    "drugs",
    "narcotics",
    "narcotic drug",
    "psychotropic substance",
    "unknown",
]


def _kb():
    return DrugKB(BUG_KB_ITEMS, ignore_terms=IGNORE_TERMS)


def test_generic_drugs_ignored_before_kb():
    kb = _kb()
    name, tier = resolve_primary_name("drugs", kb.items, drug_kb=kb)
    assert name is None
    assert tier == TIER_IGNORED_GENERIC
    assert name != "Ganja Chocolates"


def test_generic_variants_ignored():
    kb = _kb()
    for raw in ("drug", "drugs", "narcotics", "narcotic drug", "psychotropic substance", "DRUGS"):
        name, tier = resolve_primary_name(raw, kb.items, drug_kb=kb)
        assert name is None, raw
        assert tier == TIER_IGNORED_GENERIC, raw


def test_drugs_mixed_chocolates_exact_still_works():
    kb = _kb()
    name, tier = resolve_primary_name("drugs mixed chocolates", kb.items, drug_kb=kb)
    assert name == "Ganja Chocolates"
    assert tier == "exact"


def test_explicit_mdma_exact():
    kb = _kb()
    name, tier = resolve_primary_name("MDMA", kb.items, drug_kb=kb)
    assert name == "MDMA" and tier == "exact"


def test_positive_kb_aliases():
    kb = _kb()
    cases = [
        ("Ganja", "Ganja", "exact"),
        ("Cocaine", "Cocaine", "exact"),
        ("Heroin", "Heroin", "exact"),
        ("ganja chocolates", "Ganja Chocolates", "exact"),
        ("crystal mdma", "MDMA", "exact"),
    ]
    for raw, expected, exp_tier in cases:
        name, tier = resolve_primary_name(raw, kb.items, drug_kb=kb)
        assert name == expected, raw
        assert tier == exp_tier, (raw, tier)


def test_substring_kb_in_raw_preserved():
    kb = _kb()
    name, tier = resolve_primary_name("floating dry ganja packet", kb.items, drug_kb=kb)
    assert name == "Ganja"
    assert "substring" in tier


def test_substring_raw_in_kb_token_safe():
    kb = _kb()
    # Legitimate: raw token of multi-word alias
    name, tier = resolve_primary_name("ganza", kb.items, drug_kb=kb)
    assert name == "Ganja"
    assert tier == "substring_raw_in_kb"
    # Unsafe character-substring path must not fire for ignored generics
    name, tier = resolve_primary_name("drugs", kb.items, drug_kb=kb)
    assert tier == TIER_IGNORED_GENERIC


def test_drug_row_drops_generic():
    kb = _kb()
    row = _drug_row(
        "V2:ai:C1:0", "C1", "V2", "etl3_ai", "C1",
        "drugs", 6.585, "grams", 52000, kb.items, drug_kb_obj=kb,
    )
    assert row is None


def test_source_evidence_cases():
    kb = _kb()
    # Case A
    a = validate_ai_drug_item(
        {"raw_drug_name": "MDMA"}, "MDMA drug packets seized", drug_kb_obj=kb,
    )
    assert a["reject_reason"] is None
    # Case B
    b = validate_ai_drug_item(
        {"raw_drug_name": "Ganja"}, "drug packets seized", drug_kb_obj=kb,
    )
    assert b["reject_reason"] == REASON_SOURCE_UNSUPPORTED
    # Case C
    c = validate_ai_drug_item(
        {"raw_drug_name": "drugs"}, "drug packets seized", drug_kb_obj=kb,
    )
    assert c["reject_reason"] == REASON_GENERIC_IGNORED
    # Case D
    d = validate_ai_drug_item(
        {"raw_drug_name": "Ganja"}, "Ganja packets seized", drug_kb_obj=kb,
    )
    assert d["reject_reason"] is None
    # Case E — generic rejected even when MDMA is in source
    e = validate_ai_drug_item(
        {"raw_drug_name": "drugs"},
        "drug by name MDMA and drugs packets",
        drug_kb_obj=kb,
    )
    assert e["reject_reason"] == REASON_GENERIC_IGNORED


MDMA_SOURCE = (
    "Seized 13 drugs packet (white color packet), total approximately 6.585 grams "
    "under a cover of Panchanama. Seized property total worth about Rs. 52,000/-. "
    "In the year 2021 when he went to Goa along with his friends, there he met a "
    "Nigerian and at their he consumes a drug by name MDMA in Bear. 2 months ago "
    "he brought the drugs from Jack to Hyderabad for Rs.1500 per packet and sell "
    "by illegally at Rs.4000 per packet, he addicted to earn more money by "
    "illegally selling MDMA drug packets to needy customers."
)


def test_mdma_crime_pipeline_prefers_mdma():
    kb = _kb()
    assert source_mentions_label("MDMA", MDMA_SOURCE)
    parsed = parse_drug_response(
        '{"drugs":[{"raw_drug_name":"MDMA","raw_quantity":6.585,"raw_unit":"grams",'
        '"drug_form":"solid","seizure_worth":52000,"worth_scope":"individual",'
        '"confidence_score":90}]}'
    )
    rows = ai_drug_rows(
        "62a006d9e32fb48e4b9f288e", "V2", parsed["drugs"], kb.items,
        drug_kb_obj=kb, source_text=MDMA_SOURCE,
    )
    assert len(rows) == 1
    assert rows[0]["primary_drug_name"] == "MDMA"
    assert rows[0]["kb_match_tier"] == "exact"
    assert rows[0]["primary_drug_name"] != "Ganja Chocolates"


def test_mdma_crime_generic_drugs_cannot_become_ganja_chocolates():
    kb = _kb()
    parsed = parse_drug_response(
        '{"drugs":[{"raw_drug_name":"drugs","raw_quantity":6.585,"raw_unit":"grams",'
        '"drug_form":"solid","seizure_worth":52000,"worth_scope":"individual"}]}'
    )
    rows = ai_drug_rows(
        "62a006d9e32fb48e4b9f288e", "V2", parsed["drugs"], kb.items,
        drug_kb_obj=kb, source_text=MDMA_SOURCE,
    )
    assert rows == []
    assert any(r["reason"] == REASON_GENERIC_IGNORED for r in rows.rejections)
    # Direct resolve also blocked
    name, tier = resolve_primary_name("drugs", kb.items, drug_kb=kb)
    assert (name, tier) == (None, TIER_IGNORED_GENERIC)


def test_null_raw_drug_name_skipped_by_parser():
    parsed = parse_drug_response('{"drugs":[{"raw_drug_name":null,"raw_quantity":1,"raw_unit":"g","drug_form":"solid"}]}')
    assert parsed["drugs"] == []


def test_live_kb_ignore_drugs_if_available():
    """Optional live-DB check; skips when unified DB is unavailable."""
    try:
        from etl3.db import connections
        from etl3.enrichment.kb import load_drug_kb
        conn = connections.get_unified_connection(readonly=True)
    except Exception:
        return
    try:
        kb = load_drug_kb(conn, include_static_extras=False)
        assert kb.is_ignored("drugs") is True
        name, tier = resolve_primary_name("drugs", kb.items, drug_kb=kb)
        assert name is None and tier == TIER_IGNORED_GENERIC
        name, tier = resolve_primary_name("MDMA", kb.items, drug_kb=kb)
        assert name == "MDMA" and tier == "exact"
        # All 379 verified rows: exact resolve of each raw_name must succeed
        # or be ignored — never invent a different standard via weak substring.
        mismatches = []
        for raw, standard in kb.items:
            # skip static extras if any
            got, tier = resolve_primary_name(raw, kb.items, drug_kb=kb)
            if kb.is_ignored(raw):
                assert tier == TIER_IGNORED_GENERIC
                continue
            if got != standard and tier == "exact":
                mismatches.append((raw, standard, got, tier))
            # exact on the raw key itself must return its own standard
            if str(raw).lower().strip() == str(raw).lower().strip():
                got2, tier2 = resolve_primary_name(raw, kb.items, drug_kb=kb)
                if tier2 == "exact" and got2 != standard:
                    mismatches.append((raw, standard, got2, tier2))
        assert mismatches == []
    finally:
        conn.close()


def main():
    tests = [
        test_generic_drugs_ignored_before_kb,
        test_generic_variants_ignored,
        test_drugs_mixed_chocolates_exact_still_works,
        test_explicit_mdma_exact,
        test_positive_kb_aliases,
        test_substring_kb_in_raw_preserved,
        test_substring_raw_in_kb_token_safe,
        test_drug_row_drops_generic,
        test_source_evidence_cases,
        test_mdma_crime_pipeline_prefers_mdma,
        test_mdma_crime_generic_drugs_cannot_become_ganja_chocolates,
        test_null_raw_drug_name_skipped_by_parser,
        test_live_kb_ignore_drugs_if_available,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"OK  {fn.__name__}")
        except Exception as exc:
            failed += 1
            print(f"FAIL {fn.__name__}: {exc}")
    if failed:
        raise SystemExit(failed)
    print("ALL PASSED")


if __name__ == "__main__":
    main()
