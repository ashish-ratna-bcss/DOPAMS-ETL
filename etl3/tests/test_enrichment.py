"""Enrichment rules, AI validation, and idempotent projection. No database required."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.enrichment.ai import AIExtractionError, extract_with_retry, parse_drug_response
from etl3.enrichment.kb import resolve_primary_name
from etl3.enrichment.names import clean_person_names, collapse_exact_repetition
from etl3.enrichment.project import (
    accused_row,
    ai_drug_rows,
    arrest_row,
    crime_row,
    drug_rows_from_sources,
    person_row,
)
from etl3.enrichment.address import (
    GeoKB,
    _fuzzy_mandal,
    expand_state,
    resolve_address_text,
    resolve_name_country,
    resolve_nationality_label,
    resolve_person_address,
)
from etl3.enrichment.rules import (
    classify_domicile,
    classify_sections,
    commercial_for_group,
    indian_state_for_domicile,
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
    # Permanent address is used alone. A recognized Indian state is usable
    # when country is India or missing; India is not invented as a country.
    assert classify_domicile("Telangana", "India", None, None) == "native state"
    assert classify_domicile("  TS ", " india ", None, None) == "native state"
    assert classify_domicile("Maharashtra", "India", None, None) == "inter state"
    assert classify_domicile("Orissa", "India", None, None) == "inter state"
    assert classify_domicile("Pondicherry", "India", None, None) == "inter state"
    assert classify_domicile("Uttaranchal", "India", None, None) == "inter state"
    assert classify_domicile("Jammu & Kashmir", "India", None, None) == "inter state"
    assert classify_domicile("NCT of Delhi", "India", None, None) == "inter state"
    assert classify_domicile("jk", "India", None, None) == "inter state"
    assert classify_domicile("or", "India", None, None) == "inter state"
    # Present address does not override a usable permanent address.
    assert classify_domicile("Telangana", "India", "Maharashtra", "India") == "native state"
    assert classify_domicile("Karnataka", "India", "Telangana", "India") == "inter state"
    assert classify_domicile("Maharashtra", "India", "Telangana", "India") == "inter state"
    assert classify_domicile("Telangana", None, None, "United States") == "native state"
    # Permanent slot unusable → present slot alone. Do not mix fields.
    assert classify_domicile(None, None, "Telangana", "India") == "native state"
    assert classify_domicile(None, None, "Maharashtra", None) == "inter state"
    assert classify_domicile(None, None, None, "United States") == "international"
    assert classify_domicile(None, "India", "Telangana", None) == "native state"
    assert classify_domicile(None, "India", "Maharashtra", "India") == "inter state"
    assert classify_domicile(None, "India", None, "United States") == "international"
    assert classify_domicile(None, "United States", None, None) == "international"
    assert classify_domicile("Telangana", "United States", "Telangana", "India") == "international"
    assert classify_domicile(None, "India", None, None) is None
    assert classify_domicile("Narnia", "India", None, None) is None
    assert indian_state_for_domicile("Narnia") is None
    assert expand_state("Narnia") == "Narnia"
    # Indian nationality with no usable address is not international.
    assert classify_domicile(None, None, None, None, "Indian") is None
    assert classify_domicile("Telangana", None, None, None, "Indian") == "native state"
    assert classify_domicile("Telangana", None, None, None) == "native state"
    # Recognized foreign nationality wins over an Indian address.
    assert classify_domicile(
        "Maharashtra", "India", "Maharashtra", "India", "Nigeria",
    ) == "international"
    assert classify_domicile("Telangana", "India", None, None, "Nigerian") == "international"
    # Unrecognized nationality and placeholders are not foreign.
    for raw in ("Hindu", "Nil", "None", "Not Available", "Not Known", "Unknown", "Other", "Others", "N/A", "NA", "default"):
        assert resolve_nationality_label(raw) is None
        assert classify_domicile("Telangana", "India", None, None, raw) == "native state"
        assert classify_domicile(None, None, None, None, raw) is None
    assert resolve_nationality_label("Indian") == "India"
    assert resolve_nationality_label("Nigerian") == "Nigeria"
    assert resolve_nationality_label("Yemeni") == "Yemen"
    assert resolve_nationality_label("Yeman") == "Yemen"
    assert resolve_nationality_label("Federation of Nigeria") == "Nigeria"
    assert resolve_nationality_label("Tanzania, United Republic Of") == "Tanzania"
    assert resolve_nationality_label("United Kingdom (Uk)") == "United Kingdom"
    assert classify_domicile("Telangana", "India", None, None, "Yemeni") == "international"
    assert classify_domicile("Telangana", "India", None, None, "Tanzania, United Republic Of") == "international"
    assert resolve_nationality_label("") is None
    assert resolve_nationality_label(None) is None
    assert classify_domicile(None, "default", None, None) is None
    assert classify_domicile(None, None, None, None, None) is None
    # Muscat is a city/governorate in Oman, not a country -- explicit alias,
    # not a fuzzy match (confirmed against the live 223-row geo_countries
    # table: best trigram score 0.125, far below the 0.70 floor).
    assert resolve_nationality_label("Muscat") == "Oman"
    assert resolve_nationality_label("muscat") == "Oman"
    assert resolve_nationality_label("Omani") == "Oman"
    assert classify_domicile("Telangana", "India", None, None, "Muscat") == "international"
    assert classify_domicile(None, None, None, None, "Muscat") == "international"
    # Africa and West Indies are regions, not countries. No alias maps them
    # to one, and the nationality-field resolver never fuzzy-matches (only
    # exact alias/KB lookups), so an Indian address still wins through.
    for raw in ("Africa", "West Indies", "africa", "west indies"):
        assert resolve_nationality_label(raw) is None
        assert classify_domicile("Telangana", "India", None, None, raw) == "native state"
        assert classify_domicile(None, None, None, None, raw) is None


def test_domicile_person_precedence():
    native = person_row("V2", "P-TS", {
        "full_name": "Ravi Kumar",
        "permanent_state_ut": "  ts ",
        "permanent_country": " INDIA ",
        "present_state_ut": "Maharashtra",
        "present_country": "India",
    })
    assert native["domicile_classification"] == "native state"
    assert native["domicile_method"] == "deterministic"
    assert native["nationality"] == "India"

    inter = person_row("V2", "P-MH", {
        "full_name": "Ravi Kumar",
        "permanent_state_ut": "Orissa",
        "permanent_country": "India",
        "present_state_ut": "Telangana",
        "present_country": "India",
    })
    assert inter["domicile_classification"] == "inter state"
    assert inter["domicile_method"] == "deterministic"

    present_only = person_row("V2", "P-PR", {
        "full_name": "Ravi Kumar",
        "present_state_ut": "Maharashtra",
        "present_country": "India",
    })
    assert present_only["domicile_classification"] == "inter state"

    present_foreign = person_row("V2", "P-US", {
        "full_name": "Ravi Kumar",
        "nationality": "Indian",
        "present_country": "United States",
    })
    assert present_foreign["domicile_classification"] == "international"
    assert present_foreign["domicile_method"] == "deterministic"
    assert present_foreign["nationality"] == "India"

    india_only = person_row("V2", "P-IN-ONLY", {
        "full_name": "Ravi Kumar",
        "permanent_country": "India",
        "present_state_ut": "Telangana",
    })
    assert india_only["domicile_classification"] == "native state"
    assert india_only["domicile_method"] == "deterministic"

    no_address = person_row("V2", "P-NONE", {
        "full_name": "Ravi Kumar",
        "nationality": "Indian",
    })
    assert no_address["domicile_classification"] is None
    assert no_address["domicile_method"] == "non_recoverable"
    assert no_address["nationality"] == "India"

    for label in ("Hindu", "Nil", "Not Available", "Not Known"):
        blocked = person_row("V2", "P-BAD", {
            "full_name": "Ravi Kumar",
            "nationality": label,
        })
        assert blocked["domicile_classification"] is None
        assert blocked["nationality"] is None

    kept = person_row("V2", "P-STORED", {
        "full_name": "Ravi Kumar",
        "nationality": "Nil",
        "domicile_classification": "Native State",
    })
    assert kept["domicile_classification"] == "native state"
    assert kept["domicile_method"] == "source_column"

    replaced = person_row("V2", "P-REPL", {
        "full_name": "Ravi Kumar",
        "permanent_state_ut": "Telangana",
        "permanent_country": "India",
        "domicile_classification": "inter state",
    })
    assert replaced["domicile_classification"] == "native state"
    assert replaced["domicile_method"] == "deterministic"

    unknown_state = person_row("V2", "P-BADSTATE", {
        "full_name": "Ravi Kumar",
        "permanent_state_ut": "Narnia",
        "permanent_country": "India",
    })
    assert unknown_state["domicile_classification"] is None
    assert unknown_state["domicile_method"] == "non_recoverable"

    kb = GeoKB()
    kb.add_reference("Telangana", "Adilabad", None, None)
    kb.add_country("India")
    kb.add_country("Mali")
    kb.add_country("United States")
    from_kb = person_row("V2", "P-KB", {
        "full_name": "Ravi Kumar",
        "permanent_state_ut": "ZZNOTASTATE",
        "permanent_country": "India",
        "permanent_address_text": "Adilabad,Telangana",
    }, geo_kb=kb)
    assert from_kb["domicile_classification"] == "native state"
    assert from_kb["domicile_method"] == "deterministic"

    raw_wins = person_row("V2", "P-RAW", {
        "full_name": "Ravi Kumar",
        "permanent_state_ut": "Maharashtra",
        "permanent_country": "India",
        "permanent_address_text": "Adilabad,Telangana",
    }, geo_kb=kb)
    assert raw_wins["domicile_classification"] == "inter state"

    surname = person_row("V2", "P-MALI", {"full_name": "Kailash Mali"}, geo_kb=kb)
    assert surname["domicile_classification"] is None
    assert resolve_name_country("Kailash Mali", kb) is None

    muscat = person_row("V2", "P-MUSCAT", {
        "full_name": "Ravi Kumar",
        "permanent_state_ut": "Telangana",
        "permanent_country": "India",
        "nationality": "Muscat",
    })
    assert muscat["domicile_classification"] == "international"
    assert muscat["domicile_method"] == "nationality"
    assert muscat["nationality"] == "Oman"

    africa = person_row("V2", "P-AFRICA", {
        "full_name": "Ravi Kumar",
        "permanent_state_ut": "Telangana",
        "permanent_country": "India",
        "nationality": "Africa",
    })
    assert africa["domicile_classification"] == "native state"
    assert africa["domicile_method"] == "deterministic"
    assert africa["nationality"] == "India"

    west_indies_no_address = person_row("V2", "P-WI", {
        "full_name": "Ravi Kumar",
        "nationality": "West Indies",
    })
    assert west_indies_no_address["domicile_classification"] is None
    assert west_indies_no_address["nationality"] is None


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
    kb.add_reference("Telangana", "Adilabad", None, None)
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

    # V1 free-text District,State → KB district/state + native state domicile
    text_hit = resolve_address_text("Adilabad,Telangana", kb)
    assert text_hit["state"] == "Telangana"
    assert text_hit["district"] == "Adilabad"
    assert text_hit["country"] == "India"
    assert text_hit["path"] == "kb+text"
    v1 = person_row("V1", "201100102014467001", {
        "full_name": "Dube Rajani Kanth",
        "nationality": "Indian",
        "permanent_address_text": "Adilabad,Telangana",
        "present_address_text": "Adilabad,Telangana",
    }, geo_kb=kb)
    assert v1["domicile_classification"] == "native state"
    assert v1["domicile_method"] == "deterministic"
    assert v1["nationality"] == "India"
    assert v1["address_resolution"]["permanent"]["district"] == "Adilabad"
    assert v1["address_resolution"]["permanent"]["state"] == "Telangana"
    assert v1["address_resolution"]["permanent"]["country"] == "India"

    # Foreign nationality + Indian address → international; KB still maps address.
    kb.add_reference("Maharashtra", "Mumbai Suburban", "Vasai", "Nalasopara")
    kb.add_country("Nigeria")
    kb.add_country("Netherlands")
    foreign = person_row("V2", "63a12b0f9d874d46715aea0a", {
        "full_name": "ChijiokeUchechukwu",
        "nationality": "Nigeria",
        "permanent_state_ut": "MAHARASHTRA",
        "present_state_ut": "MAHARASHTRA",
        "permanent_district": "MUMBAI",
        "present_district": "MUMBAI",
        "permanent_locality_village": "Nalasopara",
        "present_locality_village": "Nalasopara",
        "permanent_address_text": "Nalasopara, Mumbai",
        "present_address_text": "Nalasopara, Mumbai",
    }, geo_kb=kb)
    assert foreign["domicile_classification"] == "international"
    assert foreign["domicile_method"] == "nationality"
    assert foreign["nationality"] == "Nigeria"
    assert foreign["address_resolution"]["permanent"]["state"] == "Maharashtra"
    assert foreign["address_resolution"]["permanent"]["district"] == "Mumbai Suburban"
    assert foreign["address_resolution"]["permanent"]["mandal"] == "Vasai"
    assert foreign["address_resolution"]["permanent"]["country"] == "India"

    assert resolve_name_country("Ifeanyi (Nigerian)", kb) == "Nigeria"
    assert resolve_name_country("Frankl ( Netherland, Europe)", kb) == "Netherlands"
    assert resolve_name_country("Khali of Nigeria", kb) == "Nigeria"
    assert resolve_name_country("Aisha - Nigeria", kb) == "Nigeria"
    assert resolve_name_country("Summy, Nigeria", kb) == "Nigeria"

    # Regression guard: "Mali"/"Israel"/"Sudan" are common Indian
    # surnames/given names that also happen to be countries already in
    # the KB. Confirmed against live data that reading them as a
    # nationality from the bare trailing word alone produced an actual
    # ~60% false-positive rate (14 "Mali" + 3 "Israel" + 1 "Sudan" rows
    # misclassified as foreign out of 30 real name_country rows). Only an
    # explicit marker (parens/comma/dash/"of") may ever read a name as
    # naming a nationality -- a bare collision never does, however common
    # the word is as a country name elsewhere.
    kb.add_country("Mali")
    kb.add_country("Israel")
    kb.add_country("Sudan")
    assert resolve_name_country("Pintu Sikandar Mali", kb) is None
    assert resolve_name_country("Kailash Mali", kb) is None
    assert resolve_name_country("Keerthi Israel", kb) is None
    assert resolve_name_country("Madhu Sudan goud T", kb) is None
    # The same word, explicitly marked, still resolves.
    assert resolve_name_country("Kailash (Mali)", kb) == "Mali"
    # Name says Nigerian even when nationality/source domicile say India / inter state.
    named = person_row("V2", "P-NG", {
        "full_name": "Ifeanyi (Nigerian)",
        "nationality": "India",
        "domicile_classification": "inter state",
        "permanent_state_ut": "TELANGANA",
        "permanent_country": "India",
    }, geo_kb=kb)
    assert named["domicile_classification"] == "international"
    assert named["domicile_method"] == "name_country"
    assert named["nationality"] == "Nigeria"

    # Pure Indian name + Indian address → India nationality + native/inter from KB.
    pure = person_row("V2", "P-IN", {
        "full_name": "Ravi Kumar",
        "permanent_state_ut": "TELANGANA",
        "permanent_district": "Sangareddy",
        "permanent_country": "India",
    }, geo_kb=kb)
    assert pure["nationality"] == "India"
    assert pure["domicile_classification"] == "native state"
    assert pure["domicile_method"] == "deterministic"


def test_fuzzy_mandal_tie_break_is_deterministic():
    # "Mahabubnagar Rural" and "Mahabubnagar Urban" score identically
    # (confirmed: 0.6842...) against the bare token "Mahabubnagar". Before
    # the fix, mandals_by_district stored them in a plain set(), so which
    # one won was decided by that process's string hash seed -- live data
    # showed the winner flip across separate enrichment runs on unchanged
    # records. sorted() makes the alphabetically-first tied candidate win,
    # every call, every process.
    kb = GeoKB()
    kb.add_reference("Telangana", "Mahabubnagar", "Mahabubnagar Urban", None)
    kb.add_reference("Telangana", "Mahabubnagar", "Mahabubnagar Rural", None)
    winner = _fuzzy_mandal(kb, "Telangana", "Mahabubnagar", "Mahabubnagar")
    assert winner == "Mahabubnagar Rural"
    for _ in range(50):
        assert _fuzzy_mandal(kb, "Telangana", "Mahabubnagar", "Mahabubnagar") == winner

    # Non-tied matching is unaffected: a clearly-closer candidate still wins
    # regardless of insertion order.
    kb2 = GeoKB()
    kb2.add_reference("Telangana", "Medak", "Zzzzzzzzzzz", None)
    kb2.add_reference("Telangana", "Medak", "Narsapur", None)
    assert _fuzzy_mandal(kb2, "Telangana", "Medak", "Narsapur") == "Narsapur"


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


def test_collapse_exact_repetition_confirmed_real_records():
    """Four real person_enrichment rows (source CSV export, 317 rows
    total) whose raw_full_name is the whole name typed/concatenated
    twice, confirmed still duplicated in cleaned_full_name as actually
    stored in the database before this fix."""
    assert collapse_exact_repetition(
        "Amodi Chukuwudi Muomagolu Amodi Chukuwudi Muomagolu",
    ) == "Amodi Chukuwudi Muomagolu"
    assert collapse_exact_repetition("Thomas Anagha Kalu Thomas Anagha Kalu") == "Thomas Anagha Kalu"
    assert collapse_exact_repetition("China China") == "China"
    assert collapse_exact_repetition("Chhaganlal Chhaganlal") == "Chhaganlal"

    for raw, expected in (
        ("Amodi Chukuwudi Muomagolu Amodi Chukuwudi Muomagolu", "Amodi Chukuwudi Muomagolu"),
        ("Thomas Anagha Kalu Thomas Anagha Kalu", "Thomas Anagha Kalu"),
        ("China China", "China"),
        ("Chhaganlal Chhaganlal", "Chhaganlal"),
    ):
        result = clean_person_names(raw)
        assert result["cleaned_full_name"] == expected
        # Raw source value is never touched by the collapse.
        assert result["raw_full_name"] == raw


def test_collapse_exact_repetition_never_touches_legitimate_names():
    """A repeated WORD inside a name, or a name that merely looks
    duplicate-ish, must survive untouched -- only an exact, whole-string
    tiling match is ever collapsed."""
    assert collapse_exact_repetition("Sri Sri Ravi Shankar") == "Sri Sri Ravi Shankar"
    assert collapse_exact_repetition("Rama Rama Rao") == "Rama Rama Rao"
    assert collapse_exact_repetition("Ravi Kumar") == "Ravi Kumar"
    assert collapse_exact_repetition("Jean-Jean Baptiste") == "Jean-Jean Baptiste"
    # Three-way repetition still collapses (same structural test, period=1).
    assert collapse_exact_repetition("Ravi Ravi Ravi") == "Ravi"
    # Case-insensitive match, but the FIRST occurrence's original casing survives.
    assert collapse_exact_repetition("RAVI kumar Ravi KUMAR") == "RAVI kumar"


def test_collapse_exact_repetition_idempotent_and_edge_inputs():
    once = collapse_exact_repetition("China China")
    twice = collapse_exact_repetition(once)
    assert once == twice == "China"
    for value in (None, "", "   ", "Solo"):
        assert collapse_exact_repetition(value) == value


def test_duplicate_name_full_pipeline_preserves_raw_and_other_fields():
    """End to end through clean_person_names: alias/relationship
    extraction still works correctly on an ALREADY-collapsed full_name,
    and the collapse never reaches given_name/surname -- confirmed
    against live data to risk erasing a legitimate short repeated-word
    name (e.g. "Dung Dung", a real tribal surname) that full_name's
    341-row evidence base does not cover."""
    result = clean_person_names(
        "China China", given_name="Dung Dung", surname="Syed Syed",
    )
    assert result["raw_full_name"] == "China China"
    assert result["cleaned_full_name"] == "China"
    assert result["cleaned_given_name"] == "Dung Dung"
    assert result["surname"] == "Syed Syed"

    with_alias = clean_person_names("Ravi Kumar Ravi Kumar @ Raju")
    assert with_alias["cleaned_full_name"] == "Ravi Kumar"
    assert with_alias["cleaned_alias"] == "Raju"


def test_trailing_hyphen_residue_trimmed():
    """A second, smaller real-data gap found while investigating the
    duplicate-name issue: a noise phrase stripped next to a bare hyphen
    (or a bare trailing hyphen already in the source) used to leave the
    hyphen dangling. Only the outer edge is trimmed -- an internal
    hyphenated surname is untouched."""
    assert clean_person_names("MADUKA UCHE ALLOY-Absconding")["cleaned_full_name"] == "MADUKA UCHE ALLOY"
    assert clean_person_names("Mr. Chris -")["cleaned_full_name"] == "Mr. Chris"
    assert clean_person_names("Smith-Jones")["cleaned_full_name"] == "Smith-Jones"


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
    # missing / null name is skipped (not a schema failure)
    assert parse_drug_response(
        '{"drugs":[{"raw_quantity":1,"raw_unit":"g","drug_form":"solid"}]}'
    )["drugs"] == []
    try:
        parse_drug_response('{"note":"no drugs key"}')
        raise AssertionError("missing drugs key should fail")
    except AIExtractionError as exc:
        assert exc.status == "invalid"


def test_ai_retry_and_success_does_not_invent_on_empty():
    timeout = AIExtractionError("timeout", "timed out")
    client = _ScriptedClient([timeout, '{"drugs":[]}'])
    parsed, attempts, raw = extract_with_retry(client, "brief facts", max_retries=1)
    assert attempts == 2
    assert parsed["drugs"] == []
    assert raw == '{"drugs":[]}'
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
    built = ai_drug_rows(
        "C9", "V2", good["drugs"], [("ganja", "Ganja")],
        source_text="seized ganja 20 kg under panchanama",
    )
    assert built[0]["is_commercial"] is True
    assert built[0]["provenance"] == "etl3_ai"
    assert built[0]["drug_category"] == "Cannabis"


def main():
    check("sections", test_sections)
    check("case status", test_case_status)
    check("domicile", test_domicile)
    check("domicile person precedence", test_domicile_person_precedence)
    check("units", test_units)
    check("category and commercial", test_drug_category_and_commercial)
    check("knowledge base", test_kb)
    check("accused and arrest", test_accused_and_arrest)
    check("projection", test_projection_does_not_invent)
    check("address knowledge base", test_address_kb_confirms_known_geography_only)
    check("name cleanup", test_name_cleanup_for_v1_and_v2)
    check("duplicate name collapse: confirmed real records", test_collapse_exact_repetition_confirmed_real_records)
    check("duplicate name collapse: legitimate names untouched", test_collapse_exact_repetition_never_touches_legitimate_names)
    check("duplicate name collapse: idempotent + edge inputs", test_collapse_exact_repetition_idempotent_and_edge_inputs)
    check("duplicate name: full pipeline preserves raw/other fields", test_duplicate_name_full_pipeline_preserves_raw_and_other_fields)
    check("trailing hyphen residue trimmed", test_trailing_hyphen_residue_trimmed)
    check("replay hash", test_replay_hash_stable)
    check("ai validation", test_ai_validation)
    check("ai retry", test_ai_retry_and_success_does_not_invent_on_empty)
    print("all enrichment unit tests passed")


if __name__ == "__main__":
    main()
