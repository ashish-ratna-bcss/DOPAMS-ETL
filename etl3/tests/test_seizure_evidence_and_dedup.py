# -*- coding: utf-8 -*-
"""Regression tests for the no-seizure validation gate, within-response
duplicate collapsing, and multi-weight quantity recognition.

Fixtures are sanitized versions of the real verified crimes found during
the investigation (source_sentence text is the model's actual quoted
evidence, pulled from ai_extraction_attempts.parsed_response):
  - 6883467991d11d4489b9be5f: urine-test-only, 5 duplicate "Ganja" items,
    each quoting a different sentence of one confession, none describing
    a physical seizure.
  - 69d92814a8881aa1c235ca5f: mobile-phone-only seizure, consumption
    admission only, no drug physically recovered.
  - 67b063ce494f2f5516485070: genuine Hash oil seizure, 5 individually
    weighed boxes (6+8+7+7+7=35g), never stated as a literal "35".
  - 691720a85941dea536ae849a: genuine 26.22g Alprazolam seizure plus two
    purely-generic zero-quantity "police caught hold them" duplicates
    quoted from different co-accused confessions.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.enrichment.ai import (
    REASON_NO_PHYSICAL_SEIZURE,
    source_mentions_quantity,
    validate_ai_drug_item,
)
from etl3.enrichment.kb import DrugKB
from etl3.enrichment.project import ai_drug_rows

KB_ITEMS = [
    ("ganja", "Ganja"),
    ("alprazolam", "Alprazolam"),
    ("hash oil", "Hashish/Weed Oil"),
    ("heroin", "Heroin"),
    ("mdma", "MDMA"),
]
IGNORE_TERMS = ["pills", "tablets", "liquid", "white"]


def _kb():
    return DrugKB(KB_ITEMS, ignore_terms=IGNORE_TERMS)


# ---------------------------------------------------------------------------
# 1. Urine-test-only case (real crime 6883467991d11d4489b9be5f)
# ---------------------------------------------------------------------------
URINE_TEST_ONLY_TEXT = (
    "On 25.07.2025 at 08:30 hours Sri M.Shivanandam Goud, SI of Police, "
    "Rajapur PS received credible information that Lal Babu Yadav is "
    "consuming Ganja and habituated to consuming such related drugs. "
    "On enquiry he stated that he is not having any drug in his personal "
    "possession. He confessed he is addicted to consumption of Ganja and "
    "said he came searching for ganja, then an unknown person handed over "
    "the ganja to accused. He stated he is unable to stop thinking about "
    "consumption of Ganja. He is willing to be referred to the Government "
    "of Telangana authorized De-addiction centre under Sec 64A of the NDPS "
    "Act. A drug testing kit showed positive for the consumption of Ganja."
)
URINE_TEST_ONLY_ITEMS = [
    {
        "raw_drug_name": "Ganja", "primary_drug_name": "Ganja", "raw_quantity": 0.0,
        "raw_unit": "count", "seizure_worth": 0.0, "is_commercial": False,
        "confidence_score": 90,
        "source_sentence": "he stated that he is not having any drug in his personal possession.",
    },
    {
        "raw_drug_name": "Ganja", "primary_drug_name": "Ganja", "raw_quantity": 0.0,
        "raw_unit": "count", "seizure_worth": 0.0, "is_commercial": False,
        "confidence_score": 90,
        "source_sentence": "he is addicted to consuming Ganja by procuring from unknown persons.",
    },
    {
        "raw_drug_name": "Ganja", "primary_drug_name": "Ganja", "raw_quantity": 0.0,
        "raw_unit": "count", "seizure_worth": 0.0, "is_commercial": False,
        "confidence_score": 90,
        "source_sentence": "he is addicted to consumption of Ganja and said he came searching for ganja.",
    },
    {
        "raw_drug_name": "Ganja", "primary_drug_name": "Ganja", "raw_quantity": 0.0,
        "raw_unit": "count", "seizure_worth": 0.0, "is_commercial": False,
        "confidence_score": 90,
        "source_sentence": "he unable to stop thinking about consumption of Ganja.",
    },
    {
        "raw_drug_name": "Ganja", "primary_drug_name": "Ganja", "raw_quantity": 0.0,
        "raw_unit": "count", "seizure_worth": 0.0, "is_commercial": False,
        "confidence_score": 90,
        "source_sentence": "he is willing to be referred to the De-addiction centre under Sec 64A.",
    },
]


def test_urine_test_only_produces_no_rows():
    kb = _kb()
    rows = ai_drug_rows("C1", "V2", URINE_TEST_ONLY_ITEMS, kb.items, drug_kb_obj=kb, source_text=URINE_TEST_ONLY_TEXT)
    assert list(rows) == []
    assert all(r["reason"] == REASON_NO_PHYSICAL_SEIZURE for r in rows.rejections)
    assert len(rows.rejections) == 5


# ---------------------------------------------------------------------------
# 2 & 3. Consumption-only / mobile-phone-only (real crime 69d92814a8881aa1c235ca5f)
# ---------------------------------------------------------------------------
PHONES_ONLY_TEXT = (
    "On 09-04-2026 Sub-Inspector Ch. Madhusudhan received credible "
    "information that some persons were consuming Ganja at open place "
    "SPR Hills Ground, Borabanda. On observing them, all of them appeared "
    "drowsy and were behaving suspiciously. On further tactful questioning "
    "they revealed their identities. They all admitted that they had "
    "consumed ganja. On frisking the accused persons we have found the "
    "following 2 mobile phones from the possession of K. Rohith and "
    "B. Rahul, and marked the mobile phones as C1-C2, seized them."
)
PHONES_ONLY_ITEMS = [
    {
        "raw_drug_name": "Ganja", "primary_drug_name": "Ganja", "raw_quantity": 0.0,
        "raw_unit": "count", "seizure_worth": 0.0, "is_commercial": False,
        "confidence_score": 100,
        "source_sentence": (
            "On observing them, all of them appeared drowsy and were behaving "
            "suspiciously. On further tactful questioning they revealed their "
            "identities. They all admitted that they had consumed ganja."
        ),
    },
    {
        "raw_drug_name": "Mobile Phone", "primary_drug_name": None, "raw_quantity": 2.0,
        "raw_unit": "count", "seizure_worth": 0.0, "is_commercial": False,
        "confidence_score": 100,
        "source_sentence": "we have found the following 2 mobile phones ... and marked ... seized them.",
    },
]


def test_consumption_admission_and_phones_only_produces_no_drug_rows():
    kb = _kb()
    rows = ai_drug_rows("C2", "V2", PHONES_ONLY_ITEMS, kb.items, drug_kb_obj=kb, source_text=PHONES_ONLY_TEXT)
    assert list(rows) == []
    reasons = {r["raw_drug_name"]: r["reason"] for r in rows.rejections}
    assert reasons["Ganja"] == REASON_NO_PHYSICAL_SEIZURE
    assert reasons["Mobile Phone"] == "NON_DRUG_PROPERTY"


# ---------------------------------------------------------------------------
# 4. Genuine seizure with missing quantity (no consumption/urine language at all)
# ---------------------------------------------------------------------------
def test_genuine_seizure_missing_quantity_is_not_rejected():
    kb = _kb()
    text = "on checking found Ganja in the plastic bags carried by the accused."
    item = {
        "raw_drug_name": "Ganja", "primary_drug_name": "Ganja", "raw_quantity": None,
        "raw_unit": None, "seizure_worth": 0.0, "is_commercial": False,
        "confidence_score": 80, "source_sentence": text,
    }
    checked = validate_ai_drug_item(item, text, drug_kb_obj=kb)
    assert checked["reject_reason"] is None
    assert checked["raw_drug_name"] == "Ganja"


# ---------------------------------------------------------------------------
# 5. Physical seizure of unidentified white powder (no regression)
# ---------------------------------------------------------------------------
def test_unidentified_white_powder_still_classified_not_rejected():
    from etl3.enrichment.kb import UNIDENTIFIED_SUBSTANCE, resolve_primary_name
    items = [("white powder", UNIDENTIFIED_SUBSTANCE)]
    kb = DrugKB(items, ignore_terms=IGNORE_TERMS)
    text = "found 500 grams of white powder in a plastic cover."
    item = {
        "raw_drug_name": "white powder", "primary_drug_name": None, "raw_quantity": 500.0,
        "raw_unit": "grams", "seizure_worth": 0.0, "is_commercial": False,
        "confidence_score": 80, "source_sentence": text,
    }
    checked = validate_ai_drug_item(item, text, drug_kb_obj=kb)
    assert checked["reject_reason"] is None
    name, tier = resolve_primary_name(checked["raw_drug_name"], kb.items, drug_kb=kb)
    assert name == UNIDENTIFIED_SUBSTANCE


# ---------------------------------------------------------------------------
# 6. Repeated identical drug mentions, no seizure-evidence-gate trigger
#    (isolates the dedup mechanism from the seizure-evidence gate)
# ---------------------------------------------------------------------------
def test_duplicate_unmeasured_mentions_collapse_to_one_row():
    kb = _kb()
    text = (
        "the accused was found travelling with Ganja. Ganja was mentioned "
        "again in his second statement about the same trip."
    )
    items = [
        {
            "raw_drug_name": "Ganja", "primary_drug_name": "Ganja", "raw_quantity": 0.0,
            "raw_unit": None, "seizure_worth": 0.0, "is_commercial": False,
            "confidence_score": 70, "source_sentence": "the accused was found travelling with Ganja.",
        },
        {
            "raw_drug_name": "Ganja", "primary_drug_name": "Ganja", "raw_quantity": 0.0,
            "raw_unit": None, "seizure_worth": 0.0, "is_commercial": False,
            "confidence_score": 60,
            "source_sentence": "Ganja was mentioned again in his second statement about the same trip.",
        },
    ]
    rows = ai_drug_rows("C3", "V2", items, kb.items, drug_kb_obj=kb, source_text=text)
    # Two proposed items for the same drug, neither measured -> one row.
    # (The individual source_sentences are not a drug_extractions column;
    # both remain fully preserved, unmodified, in the original
    # ai_extraction_attempts.parsed_response audit record regardless of
    # this in-memory collapse.)
    assert len(rows) == 1
    assert rows[0]["primary_drug_name"] == "Ganja"


# ---------------------------------------------------------------------------
# 7. Separate genuine seizures of the same drug are never collapsed
#    (real crime 691720a85941dea536ae849a: 26.22g real + 2g real + 2 junk)
# ---------------------------------------------------------------------------
ALPRAZOLAM_TEXT = (
    "On 14.11.2025 four suspects were apprehended. SI transferred the "
    "Alprazolam into an empty cover marked M1 and weighed and found as "
    "26.22 Grams. Garlapati Purushotham Goud stated that he purchased 02 "
    "Grams of Alprazolam and mixed it in toddy and sold to common public. "
    "Then I questioned the accused whether they are having any contraband "
    "in their personal possession and they stated that they are not having "
    "any contraband in their personal possession."
)
ALPRAZOLAM_ITEMS = [
    {
        "raw_drug_name": "Alprazolam", "primary_drug_name": "Alprazolam", "raw_quantity": 26.22,
        "raw_unit": "Grams", "seizure_worth": 26.22, "is_commercial": False,
        "confidence_score": 0,
        "source_sentence": "SI transferred the Alprazolam into an empty cover marked M1 and weighed and found as 26.22 Grams.",
    },
    {
        "raw_drug_name": "Alprazolam", "primary_drug_name": "Alprazolam", "raw_quantity": 2.0,
        "raw_unit": "Grams", "seizure_worth": 2.0, "is_commercial": False,
        "confidence_score": 0,
        "source_sentence": "he purchased 02 Grams of Alprazolam and mixed it in toddy and sold to common public.",
    },
    {
        "raw_drug_name": "Alprazolam", "primary_drug_name": "Alprazolam", "raw_quantity": 0.0,
        "raw_unit": "Grams", "seizure_worth": 0.0, "is_commercial": False,
        "confidence_score": 0,
        "source_sentence": "the police came there, on seeing the police party they tried to escape from there but the police were caught hold them.",
    },
    {
        "raw_drug_name": "Alprazolam", "primary_drug_name": "Alprazolam", "raw_quantity": 0.0,
        "raw_unit": "Grams", "seizure_worth": 0.0, "is_commercial": False,
        "confidence_score": 0,
        "source_sentence": "the police came there, on seeing the police party they tried to escape from there but the police caught hold them.",
    },
]


def test_separate_genuine_seizures_preserved_junk_duplicates_dropped():
    kb = _kb()
    rows = ai_drug_rows("C4", "V2", ALPRAZOLAM_ITEMS, kb.items, drug_kb_obj=kb, source_text=ALPRAZOLAM_TEXT)
    quantities = sorted(r["raw_quantity"] for r in rows)
    assert quantities == [2.0, 26.22]
    assert len(rows) == 2
    # The 2 generic zero-quantity "caught hold them" duplicates are gone --
    # rejected by the seizure-evidence gate (denial boilerplate present in
    # the full text, no recovery language in their own quoted sentence).
    reasons = [r["reason"] for r in rows.rejections]
    assert reasons.count(REASON_NO_PHYSICAL_SEIZURE) == 2


# ---------------------------------------------------------------------------
# 8. Multi-drug FIR with different quantities, independently correct
# ---------------------------------------------------------------------------
def test_multi_drug_fir_independent_quantities():
    kb = _kb()
    text = "seized 5 kg of ganja and separately 2 grams of MDMA from the same accused."
    items = [
        {
            "raw_drug_name": "ganja", "primary_drug_name": "ganja", "raw_quantity": 5.0,
            "raw_unit": "kg", "seizure_worth": 0.0, "is_commercial": False,
            "confidence_score": 95, "source_sentence": "seized 5 kg of ganja.",
        },
        {
            "raw_drug_name": "MDMA", "primary_drug_name": "MDMA", "raw_quantity": 2.0,
            "raw_unit": "grams", "seizure_worth": 0.0, "is_commercial": False,
            "confidence_score": 95, "source_sentence": "separately 2 grams of MDMA from the same accused.",
        },
    ]
    rows = ai_drug_rows("C5", "V2", items, kb.items, drug_kb_obj=kb, source_text=text)
    by_name = {r["primary_drug_name"]: r for r in rows}
    assert by_name["Ganja"]["weight_kg"] == 5.0
    assert by_name["MDMA"]["weight_g"] == 2.0


# ---------------------------------------------------------------------------
# 9. Explicit stated total quantity
# ---------------------------------------------------------------------------
def test_explicit_total_quantity_recognized():
    text = "weighed all 41 packets; TOTAL 179.1 Grams of Ganja (including the cover)."
    assert source_mentions_quantity(179.1, text, unit="Grams") is True


# ---------------------------------------------------------------------------
# 10. Multiple individually weighed packets summing to the proposed total
#     (real crime 67b063ce494f2f5516485070: Hash oil, 6+8+7+7+7=35g)
# ---------------------------------------------------------------------------
def test_distinct_individually_weighed_packets_sum_recognized():
    sentence = (
        "I seized those five plastic boxes of Hash oil and marked them as "
        "P-1, P-2, P-3, P-4 & P-5. Further on weighing them it is found, "
        "P-1 is 6 grams, P-2 is 8 grams, P-3 is 7 grams, P-4 is 7 grams "
        "&P-5 is 7 grams."
    )
    assert source_mentions_quantity(35.0, sentence, unit="grams", source_sentence=sentence) is True
    # A single mention, or a value that isn't actually the sum, must not match.
    assert source_mentions_quantity(40.0, sentence, unit="grams", source_sentence=sentence) is False


def test_distinct_packet_sum_only_applies_within_quoted_sentence():
    # The full document may contain other, unrelated same-unit mentions
    # elsewhere; the sum must only be built from the item's own quoted
    # source_sentence, never the whole text.
    full_text = (
        "Earlier that year he had purchased 10 grams for personal use. "
        "I seized those five plastic boxes of Hash oil and marked them as "
        "P-1, P-2, P-3, P-4 & P-5. Further on weighing them it is found, "
        "P-1 is 6 grams, P-2 is 8 grams, P-3 is 7 grams, P-4 is 7 grams "
        "&P-5 is 7 grams."
    )
    item_sentence = (
        "P-1 is 6 grams, P-2 is 8 grams, P-3 is 7 grams, P-4 is 7 grams &P-5 is 7 grams."
    )
    assert source_mentions_quantity(35.0, full_text, unit="grams", source_sentence=item_sentence) is True
    # 45 would only be reachable by also pulling in the unrelated "10 grams"
    # mention from elsewhere in the document -- must not match.
    assert source_mentions_quantity(45.0, full_text, unit="grams", source_sentence=item_sentence) is False


# ---------------------------------------------------------------------------
# 11. Repeated / concurrent processing stays idempotent
# ---------------------------------------------------------------------------
def test_repeated_processing_is_idempotent():
    kb = _kb()
    rows_a = ai_drug_rows("C6", "V2", ALPRAZOLAM_ITEMS, kb.items, drug_kb_obj=kb, source_text=ALPRAZOLAM_TEXT)
    rows_b = ai_drug_rows("C6", "V2", ALPRAZOLAM_ITEMS, kb.items, drug_kb_obj=kb, source_text=ALPRAZOLAM_TEXT)
    ids_a = [r["extraction_id"] for r in rows_a]
    ids_b = [r["extraction_id"] for r in rows_b]
    assert ids_a == ids_b
    assert len(ids_a) == 2


# ---------------------------------------------------------------------------
# 12. Correct drug_finding_status behaviour when everything gets rejected
# ---------------------------------------------------------------------------
def test_all_rejected_named_items_yield_no_rows_for_status_fallthrough():
    # _finalize_drug_extraction (runner.py) treats `rows` empty + `drugs`
    # non-empty as "all_proposed_items_rejected" -> No drug Found. This
    # confirms ai_drug_rows produces exactly that empty-rows/non-empty-
    # rejections shape for a conclusively-no-seizure response, with zero
    # changes needed to the finding-status logic itself.
    kb = _kb()
    rows = ai_drug_rows("C7", "V2", URINE_TEST_ONLY_ITEMS, kb.items, drug_kb_obj=kb, source_text=URINE_TEST_ONLY_TEXT)
    assert list(rows) == []
    assert len(rows.rejections) == len(URINE_TEST_ONLY_ITEMS)
