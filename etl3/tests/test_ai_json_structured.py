"""Regression: malformed/truncated JSON vs valid drug/accused payloads."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.enrichment.ai import (
    ACCUSED_RESPONSE_SCHEMA,
    AIExtractionError,
    DRUG_RESPONSE_SCHEMA,
    extract_accused_with_retry,
    extract_with_retry,
    parse_accused_response,
    parse_drug_response,
)


VALID_DRUG = json.dumps({
    "drugs": [{
        "raw_drug_name": "Ganja",
        "raw_quantity": 1.5,
        "raw_unit": "Kg",
        "primary_drug_name": "Ganja",
        "drug_form": "solid",
        "seizure_worth": 30000,
        "worth_scope": "drug_total",
        "is_commercial": True,
        "confidence_score": 90,
        "extraction_metadata": {"source_sentence": "seized 1.5 Kg ganja worth Rs.30000"},
    }]
})

VALID_ACCUSED = json.dumps({
    "accused": [{
        "accused_code": "A-1",
        "accused_id": "abc123",
        "role_in_crime": "seller",
        "key_details": "caught with ganja",
        "age": 28,
        "alias_name": None,
        "gender": "male",
        "occupation": None,
        "address": None,
        "phone_numbers": None,
        "status": "arrested",
        "explicit_ccl": None,
    }]
})

# Representative truncated shapes from production invalid attempts (Oct 13).
TRUNCATED_ACCUSED_UNTERMINATED = (
    '{\n  "accused": [\n    {\n      "accused_code": "A1",\n'
    '      "accused_id": "635c97de53edd63ce7022976",\n'
    '      "role_in_crime": "Procured the drug through peddler'
)  # unterminated string

TRUNCATED_ACCUSED_MID = (
    VALID_ACCUSED[:-40]  # cut mid-object → Expecting value / unterminated
)


def test_valid_drug_parses():
    out = parse_drug_response(VALID_DRUG)
    assert len(out["drugs"]) == 1
    assert out["drugs"][0]["raw_drug_name"] == "Ganja"


def test_valid_accused_parses_roster_filter():
    out = parse_accused_response(
        VALID_ACCUSED, allowed_accused_ids=["abc123"], allowed_codes=["A-1"],
    )
    assert len(out["accused"]) == 1
    assert out["accused"][0]["status"] == "arrested"


def test_truncated_accused_raises_malformed_with_raw():
    try:
        parse_accused_response(TRUNCATED_ACCUSED_UNTERMINATED)
        raise AssertionError("expected malformed json")
    except AIExtractionError as exc:
        assert exc.status == "invalid"
        assert "malformed json" in str(exc).lower()
        assert exc.raw_response == TRUNCATED_ACCUSED_UNTERMINATED


def test_truncated_mid_payload_raises_malformed():
    try:
        parse_accused_response(TRUNCATED_ACCUSED_MID)
        raise AssertionError("expected malformed json")
    except AIExtractionError as exc:
        assert exc.status == "invalid"
        assert "malformed json" in str(exc).lower()
        assert exc.raw_response


def test_schemas_are_objects_with_required_roots():
    assert DRUG_RESPONSE_SCHEMA["required"] == ["drugs"]
    assert ACCUSED_RESPONSE_SCHEMA["required"] == ["accused"]
    assert DRUG_RESPONSE_SCHEMA["type"] == "object"
    assert ACCUSED_RESPONSE_SCHEMA["type"] == "object"


def test_malformed_json_is_retried_then_succeeds():
    calls = {"n": 0}

    class Client:
        def complete(self, brief_facts):
            calls["n"] += 1
            if calls["n"] == 1:
                return TRUNCATED_ACCUSED_UNTERMINATED
            return VALID_DRUG

    with mock.patch("etl3.enrichment.ai._retry_sleep", return_value=None):
        parsed, attempts, raw = extract_with_retry(Client(), "facts", max_retries=2)
    assert attempts == 2
    assert parsed["drugs"][0]["raw_drug_name"] == "Ganja"
    assert raw == VALID_DRUG


def test_schema_invalid_not_retried():
    """drug_form not allowed is permanent — do not burn retries."""
    bad = json.dumps({
        "drugs": [{
            "raw_drug_name": "X",
            "raw_quantity": 1,
            "raw_unit": "g",
            "primary_drug_name": None,
            "drug_form": "powder",
            "seizure_worth": 0,
            "worth_scope": "individual",
            "is_commercial": False,
            "confidence_score": 1,
            "extraction_metadata": {"source_sentence": "x"},
        }]
    })
    calls = {"n": 0}

    class Client:
        def complete(self, brief_facts):
            calls["n"] += 1
            return bad

    with mock.patch("etl3.enrichment.ai._retry_sleep", return_value=None):
        try:
            extract_with_retry(Client(), "facts", max_retries=2)
            raise AssertionError("expected invalid")
        except AIExtractionError as exc:
            assert exc.status == "invalid"
            assert "drug_form" in str(exc)
    assert calls["n"] == 1


def test_accused_malformed_retry_preserves_raw_on_final_failure():
    class Client:
        def complete(self, brief_facts, roster):
            return TRUNCATED_ACCUSED_UNTERMINATED

    with mock.patch("etl3.enrichment.ai._retry_sleep", return_value=None):
        try:
            extract_accused_with_retry(
                Client(), "facts", "roster", ["abc123"], ["A-1"], max_retries=1,
            )
            raise AssertionError("expected invalid")
        except AIExtractionError as exc:
            assert exc.status == "invalid"
            assert "malformed json" in str(exc).lower()
            assert exc.raw_response == TRUNCATED_ACCUSED_UNTERMINATED


def main():
    tests = [
        test_valid_drug_parses,
        test_valid_accused_parses_roster_filter,
        test_truncated_accused_raises_malformed_with_raw,
        test_truncated_mid_payload_raises_malformed,
        test_schemas_are_objects_with_required_roots,
        test_malformed_json_is_retried_then_succeeds,
        test_schema_invalid_not_retried,
        test_accused_malformed_retry_preserves_raw_on_final_failure,
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
    print("ALL AI JSON STRUCTURED TESTS PASSED")


if __name__ == "__main__":
    main()
