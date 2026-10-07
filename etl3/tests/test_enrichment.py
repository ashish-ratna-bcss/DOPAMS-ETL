"""Enrichment rules, AI validation, and idempotent projection. No database required."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.enrichment.ai import AIExtractionError, extract_with_retry, parse_drug_response
from etl3.enrichment.kb import resolve_primary_name
from etl3.enrichment.names import clean_person_names
from etl3.enrichment.project import (
    accused_row,
    ai_drug_rows,
    arrest_row,
    crime_row,
    drug_rows_from_sources,
    person_row,
)
from etl3.enrichment.address import GeoKB, resolve_person_address
from etl3.enrichment.rules import (
    classify_domicile,
    classify_sections,
    commercial_for_group,
    normalize_case_status,
    parse_accused_status,
    resolve_drug_category,
    standardize_measurement,
)


def check(label, fn):
    try:
        fn()
        print(f"[PASS] {label}")
    except Exception as exc:
        print(f"[FAIL] {label}: {type(exc).__name__}: {exc}")
        raise


class _ScriptedClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def complete(self, brief_facts):
        self.calls += 1
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_sections():
    assert classify_sections("20(b)(ii)(C) NDPS") == "Commercial"
    assert classify_sections("27 NDPS") == "Small"
    assert classify_sections("20(a) NDPS") == "Cultivation"
    assert classify_sections("8(c) NDPS, 20(b)(ii)(B) NDPS") == "Intermediate"
    assert classify_sections("") is None
    assert classify_sections(None) is None
    assert classify_sections("not a section") is None


def test_case_status():
    assert normalize_case_status("PT Cases") == ("PT", "dictionary")
    assert normalize_case_status("Under Investigation") == ("UI", "dictionary")
    assert normalize_case_status("compounded") == ("Compounded", "dictionary")
    assert normalize_case_status("Chargesheeted") == ("Chargesheeted", "source_unchanged")
    assert normalize_case_status(None) == (None, "source_unchanged")
    assert normalize_case_status("") == ("", "source_unchanged")


def test_domicile():
    assert classify_domicile("Telangana", "India", None, None) == "native state"
    assert classify_domicile("Karnataka", "India", "Telangana", "India") == "inter state"
    assert classify_domicile(None, "United States", None, None) == "international"
    assert classify_domicile(None, "India", None, None) is None
    assert classify_domicile(None, None, None, None, "Indian") == "international"
    assert classify_domicile(None, "default", None, None) is None
    assert classify_domicile(None, None, None, None, None) is None


def test_units():
    grams = standardize_measurement(1000, "gm")
    assert grams["weight_g"] == 1000
    assert grams["weight_kg"] == 1
    assert grams["volume_ml"] is None
    kilos = standardize_measurement(2, "kg")
    assert kilos["weight_g"] == 2000 and kilos["weight_kg"] == 2
    ml = standardize_measurement(500, "ml")
    assert ml["volume_ml"] == 500 and ml["volume_l"] == 0.5
    litres = standardize_measurement(1.5, "litres")
    assert litres["volume_l"] == 1.5 and litres["volume_ml"] == 1500
    mg = standardize_measurement(500, "mg")
    assert mg["weight_g"] == 0.5
    missing = standardize_measurement(None, "kg")
    assert missing["weight_g"] is None and missing["weight_kg"] is None
    zero = standardize_measurement(0, "gm")
    assert zero["weight_g"] == 0 and zero["weight_kg"] == 0
    invalid = standardize_measurement("nope", "kg")
    assert invalid["weight_g"] is None
    unknown_solid = standardize_measurement(10, "bundles-of-leaf", drug_form="solid")
    assert unknown_solid["count_total"] == 10 or unknown_solid["weight_g"] == 10
    boundary = standardize_measurement(20, "kg", raw_drug_name="ganja")
    assert boundary["weight_kg"] == 20


def test_drug_category_and_commercial():
    assert resolve_drug_category("Ganja") == "Cannabis"
    assert resolve_drug_category("heroin") == "Opioid"
    assert resolve_drug_category(None) is None
    assert resolve_drug_category("unknown powder") == "Other"
    assert commercial_for_group("Ganja", [{"weight_kg": 20, "is_commercial": False}]) is True
    assert commercial_for_group("Ganja", [{"weight_kg": 19.999, "is_commercial": False}]) is False
    assert commercial_for_group("Ganja", [{"weight_kg": 1, "is_commercial": True}]) is True
    assert commercial_for_group("MDM", [{"weight_kg": 0.05, "is_commercial": None}]) is True


def test_kb():
    kb = [("ganja", "Ganja"), ("dry ganja", "Ganja"), ("hash oil", "Hash Oil"), ("ganja leaf", "Ganja")]
    assert resolve_primary_name("Ganja", kb) == ("Ganja", "exact")
    assert resolve_primary_name("dry ganja", kb) == ("Ganja", "exact")
    assert resolve_primary_name("floating dry ganja", kb)[0] == "Ganja"
    assert resolve_primary_name("xyzzy-no-drug", kb) == (None, "none")
    assert resolve_primary_name("", kb) == (None, "none")
    # shorter key appears first, so it wins the substring tier
    assert resolve_primary_name("ganja leaf extra", [("ganja", "Ganja"), ("ganja leaf", "Leaf")])[1] == "substring_kb_in_raw"


def test_accused_and_arrest():
    parsed = parse_accused_status("41A Cr.P.C issued on 02/03/2024")
    assert parsed["is_41a_crpc"] is True
    assert parsed["date_of_issue_41a"] == "2024-03-02"
    assert "is_41a_pending" in parse_accused_status("41A pending")
    assert parse_accused_status(None) == {}
    row = arrest_row("V2", "a1", {"is_41a_crpc": False}, accused_status="41A issued on 01/01/2020")
    assert row["is_41a_crpc"] is True
    assert row["arrest_flag_method"] == "status_text"
    assert "is_41a_pending" not in row
    kept = arrest_row("V2", "a1", {"is_41a_crpc": True, "date_of_issue_41a": "2020-01-01"}, accused_status=None)
    assert kept["arrest_flag_method"] == "source_column"
    assert arrest_row("V2", "a1", {}, accused_status=None) is None


def test_projection_does_not_invent():
    crime = crime_row("V1", "FIR1", {"section_of_law": None, "fir_status": None})
    assert crime["class_classification"] is None
    assert crime["classification_method"] == "non_recoverable"
    copied = crime_row("V2", "C1", {"class_classification": "Small", "case_status": "UI"})
    assert copied["class_classification"] == "Small"
    assert copied["classification_method"] == "source_column"
    assert person_row("V2", "P1", {}) is None
    person = person_row("V2", "P1", {"domicile_classification": "native state", "surname": "Rao"})
    assert person["domicile_method"] == "source_column"
    assert person["surname"] == "Rao"
    drugs = drug_rows_from_sources(
        [("acc1", {"fir_reg_num": "FIR1", "drug_type": "Ganja", "weight_gm": "0"})],
        [("prop1", {"crime_id": "C1", "additional_details": {"WEIGHT": "0", "WEIGHT_IN": "Gm"}})],
        [("ganja", "Ganja")],
        {"FIR1", "C1"},
    )
    assert drugs == []
    real = drug_rows_from_sources(
        [("acc1", {"fir_reg_num": "FIR1", "drug_type": "Ganja", "weight_gm": "1000"})],
        [],
        [("ganja", "Ganja")],
        {"FIR1"},
    )
    assert len(real) == 1
    assert real[0]["primary_drug_name"] == "Ganja"
    assert real[0]["drug_category"] == "Cannabis"
    assert real[0]["weight_kg"] == 1
    assert real[0]["provenance"] == "v1_dossier"
    missing_crime = drug_rows_from_sources(
        [("acc1", {"fir_reg_num": "MISSING", "drug_type": "Ganja", "weight_gm": "10"})],
        [],
        [("ganja", "Ganja")],
        set(),
    )
    assert missing_crime == []


def test_address_kb_confirms_known_geography_only():
    kb = GeoKB()
    kb.add_reference("Telangana", "Medchal Malkajgiri", "Quthbullapur", "Suraram")
    kb.add_country("India")
    kb.add_country("United States")
    found = resolve_person_address({
        "permanent_state_ut": "TS",
        "permanent_district": "Medchal Malkajgiri",
        "permanent_country": "India",
        "permanent_locality_village": "Suraram",
    }, kb)
    assert found["permanent"]["state"] == "Telangana"
    assert found["permanent"]["district"] == "Medchal Malkajgiri"
    assert found["permanent"]["country"] == "India"
    assert found["permanent"]["mandal"] == "Quthbullapur"
    assert resolve_person_address({"permanent_state_ut": "TS"}, GeoKB()) is None
    person = person_row("V2", "P9", {
        "permanent_state_ut": "Telangana",
        "permanent_country": "India",
        "surname": "Rao",
    }, geo_kb=kb)
    assert person["address_resolution"]["permanent"]["state"] == "Telangana"
    assert person["domicile_classification"] == "native state"
    untouched = person_row("V2", "P1", {"domicile_classification": "native state", "surname": "Rao"})
    assert "address_resolution" not in untouched


def test_name_cleanup_for_v1_and_v2():
    alias = clean_person_names("Ravi Kumar @ Raju")
    assert alias["raw_full_name"] == "Ravi Kumar @ Raju"
    assert alias["cleaned_full_name"] == "Ravi Kumar"
    assert alias["cleaned_alias"] == "Raju"

    relation = clean_person_names("Kiran s/o Ram, r/o Hyderabad, 32 yrs")
    assert relation["cleaned_full_name"] == "Kiran"
    assert relation["relation_type"] == "Father"
    assert relation["cleaned_relative_name"] == "Ram"

    kept = clean_person_names("Kiran s/o Ram", relative_name="Lakshmi", relation_type="Mother")
    assert kept["cleaned_relative_name"] == "Lakshmi"
    assert kept["relation_type"] == "Mother"

    surname = clean_person_names(None, given_name="Anil @ Babu", surname="Reddy @ extra")
    assert surname["cleaned_given_name"] == "Anil"
    assert surname["surname"] == "Reddy"
    assert surname["cleaned_full_name"] == "Anil Reddy"

    assert clean_person_names(None, surname="@alias")["surname"] is None
    assert clean_person_names(None) is None

    v1 = person_row("V1", "P-V1", {"full_name": "A1) Suresh (absconding)"})
    assert v1["source_system"] == "V1"
    assert v1["raw_full_name"] == "A1) Suresh (absconding)"
    assert v1["cleaned_full_name"] == "Suresh"
    assert v1["domicile_method"] == "non_recoverable"


def test_replay_hash_stable():
    first = crime_row("V2", "C1", {"class_classification": "Small", "case_status": "PT Cases"})
    second = crime_row("V2", "C1", {"class_classification": "Small", "case_status": "PT Cases"})
    assert first["input_hash"] == second["input_hash"]
    changed = crime_row("V2", "C1", {"class_classification": "Commercial", "case_status": "PT Cases"})
    assert changed["input_hash"] != first["input_hash"]
    accused = accused_row("V2", "A1", {"type": "Accused"})
    assert accused["accused_category"] == "Accused"
    assert accused["accused_type"] is None
    assert accused["role_in_crime"] is None
    assert accused["accused_id"] == "A1"
    again = accused_row("V2", "A1", {"type": "Accused"})
    assert accused["input_hash"] == again["input_hash"]
    assert accused_row("V2", "A1", {}) is None
    # Narrative extraction cannot invent a new accused_id.
    assert accused_row(None, None, {"type": "Accused"}) is None


def test_ai_validation():
    valid = parse_drug_response(
        '{"drugs":[{"raw_drug_name":"Ganja","raw_quantity":2,"raw_unit":"kg","drug_form":"solid","seizure_worth":100,"worth_scope":"individual"}]}'
    )
    assert valid["drugs"][0]["raw_drug_name"] == "Ganja"
    empty = parse_drug_response('```json\n{"drugs":[]}\n```')
    assert empty["drugs"] == []
    try:
        parse_drug_response("")
        raise AssertionError("empty body should fail")
    except AIExtractionError as exc:
        assert exc.status == "empty"
    try:
        parse_drug_response("not json")
        raise AssertionError("malformed should fail")
    except AIExtractionError as exc:
        assert exc.status == "invalid"
    try:
        parse_drug_response('{"drugs":[{"raw_quantity":1}]}')
        raise AssertionError("missing name should fail")
    except AIExtractionError as exc:
        assert exc.status == "invalid"
    try:
        parse_drug_response('{"note":"no drugs key"}')
        raise AssertionError("missing drugs key should fail")
    except AIExtractionError as exc:
        assert exc.status == "invalid"


def test_ai_retry_and_success_does_not_invent_on_empty():
    timeout = AIExtractionError("timeout", "timed out")
    client = _ScriptedClient([timeout, '{"drugs":[]}'])
    parsed, attempts = extract_with_retry(client, "brief facts", max_retries=1)
    assert attempts == 2
    assert parsed["drugs"] == []
    rows = ai_drug_rows("C1", "V2", parsed["drugs"], [("ganja", "Ganja")])
    assert rows == []
    failing = _ScriptedClient([AIExtractionError("invalid", "bad"), AIExtractionError("invalid", "bad")])
    try:
        extract_with_retry(failing, "text", max_retries=1)
        raise AssertionError("invalid should surface")
    except AIExtractionError as exc:
        assert exc.status == "invalid"
    assert failing.calls == 2
    good = parse_drug_response(
        '{"drugs":[{"raw_drug_name":"ganja","raw_quantity":20,"raw_unit":"kg","drug_form":"solid","is_commercial":false}]}'
    )
    built = ai_drug_rows("C9", "V2", good["drugs"], [("ganja", "Ganja")])
    assert built[0]["is_commercial"] is True
    assert built[0]["provenance"] == "etl3_ai"
    assert built[0]["drug_category"] == "Cannabis"


def main():
    check("sections", test_sections)
    check("case status", test_case_status)
    check("domicile", test_domicile)
    check("units", test_units)
    check("category and commercial", test_drug_category_and_commercial)
    check("knowledge base", test_kb)
    check("accused and arrest", test_accused_and_arrest)
    check("projection", test_projection_does_not_invent)
    check("address knowledge base", test_address_kb_confirms_known_geography_only)
    check("name cleanup", test_name_cleanup_for_v1_and_v2)
    check("replay hash", test_replay_hash_stable)
    check("ai validation", test_ai_validation)
    check("ai retry", test_ai_retry_and_success_does_not_invent_on_empty)
    print("all enrichment unit tests passed")


if __name__ == "__main__":
    main()
