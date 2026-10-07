"""Known-accused brief-facts enrichment: existing CCTNS accused_id only."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from etl3.enrichment.ai import AIExtractionError, parse_accused_response
from etl3.enrichment.accused_facts import (
    apply_v1_code_type_and_ccl_numbering,
    classify_v1_accused_category,
    derive_accused_code_from_fir,
    enrich_existing_accused,
    existing_accused_only,
    map_v1_dossier_identity,
    reject_narrative_only_names,
    resolve_v1_accused_code,
)
from etl3.enrichment.project import accused_row
from etl3.enrichment.rules import classify_accused_type, resolve_is_ccl


def check(label, fn):
    try:
        fn()
        print(f"PASS {label}")
        return True
    except Exception as exc:
        print(f"FAIL {label}: {exc}")
        return False


def _base(accused_id="ACC-1", code="A-1", **extra):
    row = {
        "accused_id": accused_id,
        "source_system": "V2",
        "accused_code": code,
        "type": "Accused",
        "person_id": extra.pop("person_id", "P-1"),
        "full_name": extra.pop("full_name", "Ravi Kumar"),
    }
    row.update(extra)
    return row


def test_existing_accused_processed():
    result = enrich_existing_accused([_base()], [])
    assert len(result["rows"]) == 1
    assert result["rows"][0]["accused_id"] == "ACC-1"
    assert result["rows"][0]["accused_category"] == "Accused"


def test_no_accused_id_creates_nothing():
    assert existing_accused_only([{"full_name": "Only Name"}]) == []
    result = enrich_existing_accused([{"full_name": "Only Name", "type": "Accused"}], [{
        "accused_id": "SYNTH-1", "role_in_crime": "sold ganja",
    }])
    assert result["rows"] == []


def test_narrative_person_not_created():
    roster = [
        _base("ACC-1", "A-1", full_name="Ravi Kumar"),
        _base("ACC-2", "A-2", full_name="Suresh", person_id="P-2"),
    ]
    rejected = reject_narrative_only_names(["Prashanth", "Ravi Kumar"], roster)
    assert rejected == ["Prashanth"]
    result = enrich_existing_accused(roster, [
        {"accused_id": "ACC-1", "role_in_crime": "purchased the material"},
        {"accused_id": "SYNTH-PRASHANTH", "accused_code": "A-3",
         "role_in_crime": "supplied", "full_name": "Prashanth"},
    ])
    ids = {row["accused_id"] for row in result["rows"]}
    assert ids == {"ACC-1", "ACC-2"}
    assert any(r.get("reason") == "not_in_cctns_roster" for r in result["rejected_extractions"])


def test_existing_field_not_overwritten():
    result = enrich_existing_accused(
        [_base(age=40, occupation="Driver")],
        [{"accused_id": "ACC-1", "age": 32, "occupation": "Student"}],
    )
    row = result["rows"][0]
    assert row["age"] == 40
    assert row["occupation"] == "Driver"
    assert row["field_sources"]["age"] == "DB"
    assert row["field_sources"]["occupation"] == "DB"


def test_missing_field_extracted_from_fir():
    result = enrich_existing_accused(
        [_base(age=None, occupation="Driver")],
        [{"accused_id": "ACC-1", "age": 32, "role_in_crime": "transported the material"}],
    )
    row = result["rows"][0]
    assert row["age"] == 32
    assert row["occupation"] == "Driver"
    assert row["role_in_crime"] == "transported the material"
    assert row["field_sources"]["age"] == "LLM_FALLBACK"
    assert row["field_sources"]["role_in_crime"] == "LLM_FALLBACK"
    assert row["field_sources"]["occupation"] == "DB"


def test_missing_in_db_and_fir_stays_null():
    result = enrich_existing_accused([_base(age=None, occupation=None)], [])
    row = result["rows"][0]
    assert row["age"] is None
    assert row["occupation"] is None
    assert row["role_in_crime"] is None
    assert row["accused_type"] is None


def test_ai_cannot_invent_unsupported_values():
    # Empty extraction must not invent occupation/age/role.
    result = enrich_existing_accused([_base(age=None)], [{"accused_id": "ACC-1"}])
    row = result["rows"][0]
    assert row["age"] is None
    assert row["occupation"] is None
    assert row["role_in_crime"] is None
    assert row["accused_type"] is None
    assert classify_accused_type(None) is None
    assert classify_accused_type("") is None


def test_existing_person_id_used():
    row = accused_row(
        "V2", "ACC-1",
        {"type": "Accused", "person_id": "P101", "accused_code": "A-1"},
        person_payload={"full_name": "Ravi Kumar", "age": 30, "occupation": "Driver"},
    )
    assert row["person_id"] == "P101"
    assert row["age"] == 30
    assert row["occupation"] == "Driver"
    assert row["field_sources"]["person_id"] == "DB"


def test_null_person_id_still_processed_without_inventing_person():
    result = enrich_existing_accused(
        [_base(person_id=None, age=None)],
        [{"accused_id": "ACC-1", "age": 22, "role_in_crime": "caught with ganja"}],
    )
    row = result["rows"][0]
    assert row["accused_id"] == "ACC-1"
    assert row["person_id"] is None
    assert row["age"] == 22
    # Enrichment stores identity beside the accused; it does not invent persons_unified.


def test_null_person_insufficient_identity_stays_unresolved():
    result = enrich_existing_accused(
        [_base(person_id=None, full_name=None, age=None)],
        [{"accused_id": "ACC-1"}],
    )
    row = result["rows"][0]
    assert row["person_id"] is None
    assert row["age"] is None
    assert row["role_in_crime"] is None


def test_ccl_age_17_true():
    assert resolve_is_ccl(17) is True
    result = enrich_existing_accused([_base(age=17, is_ccl=None)], [])
    assert result["rows"][0]["is_ccl"] is True


def test_ccl_age_18_false():
    assert resolve_is_ccl(18) is False
    result = enrich_existing_accused([_base(age=25, is_ccl=None)], [])
    assert result["rows"][0]["is_ccl"] is False


def test_ccl_age_unavailable_null():
    assert resolve_is_ccl(None) is None
    result = enrich_existing_accused([_base(age=None, is_ccl=None)], [])
    assert result["rows"][0]["is_ccl"] is None


def test_one_minor_does_not_affect_another():
    roster = [
        _base("ACC-1", "A-1", age=17, person_id="P1", full_name="Minor One"),
        _base("ACC-2", "A-2", age=None, person_id="P2", full_name="Unknown Age"),
        _base("ACC-3", "A-3", age=25, person_id="P3", full_name="Adult Three"),
    ]
    result = enrich_existing_accused(roster, [])
    by_id = {row["accused_id"]: row for row in result["rows"]}
    assert by_id["ACC-1"]["is_ccl"] is True
    assert by_id["ACC-2"]["is_ccl"] is None
    assert by_id["ACC-3"]["is_ccl"] is False


def test_explicit_ccl_wording_only_for_that_accused():
    roster = [
        _base("ACC-1", "A-1", age=None, is_ccl=None, full_name="Ravi"),
        _base("ACC-2", "A-2", age=None, is_ccl=None, full_name="Suresh", person_id="P2"),
    ]
    result = enrich_existing_accused(roster, [
        {"accused_id": "ACC-1", "explicit_ccl": True},
        {"accused_id": "ACC-2", "explicit_ccl": None},
    ])
    by_id = {row["accused_id"]: row for row in result["rows"]}
    assert by_id["ACC-1"]["is_ccl"] is True
    assert by_id["ACC-1"]["field_sources"]["is_ccl"] == "explicit_ccl"
    assert by_id["ACC-2"]["is_ccl"] is None


def test_no_synthetic_accused_id():
    parsed = parse_accused_response(
        '{"accused":[{"accused_id":"SYNTH-9","accused_code":"A-9","role_in_crime":"sold"}]}',
        allowed_accused_ids={"ACC-1"},
        allowed_codes={"A-1"},
    )
    assert parsed["accused"] == []
    assert parsed["rejected"][0]["accused_id"] == "SYNTH-9"


def test_fir_only_person_never_becomes_accused():
    parsed = parse_accused_response(
        '{"accused":[{"accused_id":null,"accused_code":"A-3","role_in_crime":"supplier"}]}',
        allowed_accused_ids={"ACC-1"},
        allowed_codes={"A-1"},
    )
    assert parsed["accused"] == []
    result = enrich_existing_accused(
        [_base()],
        [{"accused_code": "A-3", "role_in_crime": "supplier"}],
    )
    assert len(result["rows"]) == 1
    assert result["rows"][0]["accused_id"] == "ACC-1"
    assert result["rows"][0]["role_in_crime"] is None


def test_parse_rejects_malformed():
    try:
        parse_accused_response("not-json", allowed_accused_ids={"ACC-1"})
        raise AssertionError("expected invalid")
    except AIExtractionError as exc:
        assert exc.status == "invalid"


def test_role_classification_from_extracted_text_only():
    result = enrich_existing_accused(
        [_base()],
        [{"accused_id": "ACC-1", "role_in_crime": "transported 5kg ganja"}],
    )
    assert result["rows"][0]["accused_type"] == "supplier"
    assert result["rows"][0]["accused_type_method"] == "LLM_FALLBACK"


def test_v1_dossier_field_mapping():
    mapped = map_v1_dossier_identity({
        "accused_name": "Ravi Kumar",
        "alias_name": "Raju",
        "age": "32",
        "accused_occupation": "Driver",
        "mobile_1": "9999999999",
        "present_address": "Hyderabad",
        "fir_status": "UI",
    })
    assert mapped["full_name"] == "Ravi Kumar"
    assert mapped["alias_name"] == "Raju"
    assert mapped["age"] == 32
    assert mapped["occupation"] == "Driver"
    assert mapped["phone_numbers"] == "9999999999"
    assert mapped["address"] == "Hyderabad"


def test_v1_code_from_db_preferred():
    code, source = resolve_v1_accused_code("A-2", "Ravi", "A-1 Ravi was caught")
    assert code == "A-2"
    assert source == "DB"


def test_v1_code_from_fir_when_db_missing():
    fir = "A-1 Ravi Kumar purchased ganja. A-2 Suresh escaped."
    assert derive_accused_code_from_fir("Ravi Kumar", fir) == "A-1"
    assert derive_accused_code_from_fir("Suresh", fir) == "A-2"
    assert derive_accused_code_from_fir("Prashanth", fir) is None
    code, source = resolve_v1_accused_code(None, "Ravi Kumar", fir)
    assert code == "A-1"
    assert source == "FIR_DERIVED"


def test_v1_no_hallucinated_code():
    assert derive_accused_code_from_fir("Unknown Person", "Somebody sold ganja.") is None
    code, source = resolve_v1_accused_code(None, "Unknown Person", "Somebody sold ganja.")
    assert code is None
    assert source is None


def test_v1_type_from_age():
    assert classify_v1_accused_category(16) == "CCL"
    assert classify_v1_accused_category(17) == "CCL"
    assert classify_v1_accused_category(18) == "Accused"
    assert classify_v1_accused_category(25) == "Accused"
    assert classify_v1_accused_category(None) is None


def test_v1_ccl_numbering_overrides_fir_a_codes():
    fir = "A1 Minor One age 16. A2 Minor Two age 17. A3 Adult Three age 25."
    records = [
        {
            "accused_id": "V1-1", "source_system": "V1", "full_name": "Minor One",
            "age": 16, "accused_code": None, "type": None,
        },
        {
            "accused_id": "V1-2", "source_system": "V1", "full_name": "Minor Two",
            "age": 17, "accused_code": None, "type": None,
        },
        {
            "accused_id": "V1-3", "source_system": "V1", "full_name": "Adult Three",
            "age": 25, "accused_code": None, "type": None,
        },
    ]
    prepared = apply_v1_code_type_and_ccl_numbering(records, fir)
    by_id = {row["accused_id"]: row for row in prepared}
    assert by_id["V1-1"]["type"] == "CCL"
    assert by_id["V1-1"]["accused_code"] == "CCL 1"
    assert by_id["V1-2"]["type"] == "CCL"
    assert by_id["V1-2"]["accused_code"] == "CCL 2"
    assert by_id["V1-3"]["type"] == "Accused"
    assert by_id["V1-3"]["accused_code"] == "A-3"

    result = enrich_existing_accused(records, [], fir_text=fir)
    out = {row["accused_id"]: row for row in result["rows"]}
    assert out["V1-1"]["accused_category"] == "CCL"
    assert out["V1-1"]["accused_code"] == "CCL 1"
    assert out["V1-1"]["is_ccl"] is True
    assert out["V1-2"]["accused_code"] == "CCL 2"
    assert out["V1-3"]["accused_category"] == "Accused"
    assert out["V1-3"]["accused_code"] == "A-3"
    assert out["V1-3"]["is_ccl"] is False


def test_v1_preserves_dossier_fields_over_fir():
    row = accused_row(
        "V1", "V1-ACC",
        {
            "accused_name": "Ravi Kumar",
            "age": 40,
            "accused_occupation": "Driver",
            "fir_status": "UI",
        },
        extraction={"accused_id": "V1-ACC", "age": 16, "occupation": "Student"},
        fir_text="A-1 Ravi Kumar aged 16 years",
    )
    assert row["age"] == 40
    assert row["occupation"] == "Driver"
    assert row["accused_category"] == "Accused"
    assert row["field_sources"]["age"] == "DB"
    assert row["field_sources"]["occupation"] == "DB"


def test_v1_age_unavailable_leaves_type_unresolved():
    result = enrich_existing_accused(
        [{
            "accused_id": "V1-X", "source_system": "V1", "full_name": "No Age",
            "age": None, "type": None, "accused_code": None,
        }],
        [],
        fir_text="A-1 No Age was present.",
    )
    row = result["rows"][0]
    assert row["accused_code"] == "A-1"
    assert row["accused_category"] is None
    assert row["is_ccl"] is None


def main():
    checks = [
        ("existing accused processed", test_existing_accused_processed),
        ("no accused_id creates nothing", test_no_accused_id_creates_nothing),
        ("narrative person not created", test_narrative_person_not_created),
        ("existing field not overwritten", test_existing_field_not_overwritten),
        ("missing field extracted", test_missing_field_extracted_from_fir),
        ("missing in both stays null", test_missing_in_db_and_fir_stays_null),
        ("no invented values", test_ai_cannot_invent_unsupported_values),
        ("existing person_id used", test_existing_person_id_used),
        ("null person_id still processed", test_null_person_id_still_processed_without_inventing_person),
        ("insufficient identity unresolved", test_null_person_insufficient_identity_stays_unresolved),
        ("age 17 ccl true", test_ccl_age_17_true),
        ("age 18+ ccl false", test_ccl_age_18_false),
        ("age unavailable ccl null", test_ccl_age_unavailable_null),
        ("one minor does not affect another", test_one_minor_does_not_affect_another),
        ("explicit ccl only for that accused", test_explicit_ccl_wording_only_for_that_accused),
        ("no synthetic accused_id", test_no_synthetic_accused_id),
        ("fir-only person never accused", test_fir_only_person_never_becomes_accused),
        ("malformed parse rejected", test_parse_rejects_malformed),
        ("role classification from text", test_role_classification_from_extracted_text_only),
        ("v1 dossier field mapping", test_v1_dossier_field_mapping),
        ("v1 code from db preferred", test_v1_code_from_db_preferred),
        ("v1 code from fir when db missing", test_v1_code_from_fir_when_db_missing),
        ("v1 no hallucinated code", test_v1_no_hallucinated_code),
        ("v1 type from age", test_v1_type_from_age),
        ("v1 ccl numbering overrides fir a-codes", test_v1_ccl_numbering_overrides_fir_a_codes),
        ("v1 preserves dossier fields", test_v1_preserves_dossier_fields_over_fir),
        ("v1 age unavailable type unresolved", test_v1_age_unavailable_leaves_type_unresolved),
    ]
    passed = sum(1 for label, fn in checks if check(label, fn))
    print(f"{passed}/{len(checks)} passed")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
