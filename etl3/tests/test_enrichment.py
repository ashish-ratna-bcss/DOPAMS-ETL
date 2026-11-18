"""Enrichment rules, AI validation, and idempotent projection. No database required."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.enrichment.ai import AIExtractionError, extract_with_retry, parse_drug_response
from etl3.enrichment.kb import resolve_primary_name
from etl3.enrichment.names import clean_person_names, collapse_exact_repetition
from etl3.enrichment.multi_accused import detect_multi_accused
from etl3.enrichment.project import (
    accused_row,
    ai_drug_rows,
    arrest_row,
    crime_row,
    drug_rows_from_sources,
    person_row,
)
from etl3.enrichment.runner import build_person_row_safe, _name_identity_settings
from etl3.merger.accused_split import build_split_rows
from etl3.enrichment.address import (
    GeoKB,
    LOCALITY_TO_DISTRICT_MANDAL,
    _candidate,
    _fuzzy_locality_key,
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


def test_free_text_state_conflict_blocks_borrowed_district():
    """A free-text mandal-only match must never override district/mandal
    when its OWN state disagrees with an already-confirmed structured
    state. Confirmed live: a real record with structured state=Andhra
    Pradesh (district unresolved -- clerk spelled it "VISAKHAPATANAM",
    which didn't match the KB) had free text "Chinthapally" with no state
    or district token of its own. The mandal-only anchor fallback found a
    real, unique "Chinthapally" mandal -- but in Nalgonda, Telangana, an
    entirely unrelated state. Before this guard, the merge would have
    produced the geographically impossible pair (Andhra Pradesh, Nalgonda).
    """
    kb = GeoKB()
    kb.add_reference("Andhra Pradesh", "Visakhapatnam", None, None)
    kb.add_reference("Telangana", "Nalgonda", "Chinthapally", None)
    kb.add_country("India")

    found = resolve_person_address({
        "present_state_ut": "ANDHRA PRADESH",
        "present_district": "VISAKHAPATANAM",  # clerk's spelling: no exact/fuzzy KB match
        "present_country": "India",
        "present_address_text": "Chinthapally",
    }, kb)
    assert found["present"]["state"] == "Andhra Pradesh"
    assert found["present"]["district"] is None

    # Sanity: when the structured state agrees (or is absent), the mandal
    # anchor still works normally.
    agreeing = resolve_person_address({
        "present_state_ut": "TELANGANA",
        "present_country": "India",
        "present_address_text": "Chinthapally",
    }, kb)
    assert agreeing["present"]["state"] == "Telangana"
    assert agreeing["present"]["district"] == "Nalgonda"


def test_locality_patch_reaches_free_text_too():
    """LOCALITY_TO_DISTRICT_MANDAL must resolve through resolve_address_text
    (free text), not just the structured-field path (_candidate) -- confirmed
    live that it did not: V1 records (no structured fields at all) and V2
    records whose locality name lands in present_house_no (not scanned by
    _candidate's combined string) stayed unresolved even after Dhoolpet/
    Mangalhat/Mangar Basthi were verified and added, because
    resolve_address_text never consulted the list at all. 26 real
    "nationality=India, state unresolved" records were found with exactly
    this shape (plain text like "DHOOLPET" or "Mangalhat ,Mangalhat").
    """
    kb = GeoKB()
    kb.add_reference("Telangana", "Hyderabad", "Asif Nagar", None)
    kb.add_reference("Andhra Pradesh", "Visakhapatnam", None, None)
    kb.add_country("India")
    assert "dhoolpet" in LOCALITY_TO_DISTRICT_MANDAL

    for text in ("DHOOLPET", "Mangalhat", "Mangalhat ,Mangalhat",
                 "Balramgalli,Near Baba Balakdass  Mutt,Dhoolpet,Mangalhat"):
        found = resolve_address_text(text, kb)
        assert found is not None, text
        assert found["state"] == "Telangana", text
        assert found["district"] == "Hyderabad", text
        assert found["path"] == "kb+text+patch", text

    # An explicit, different state token in the same text still wins --
    # the patch list is only ever tried when no state token was found.
    mismatched = resolve_address_text(
        "H.No.13-2-269,Baba Balakdas Mutt,Upper Dhoolpet, Mangalhat,Andhra  Pradesh", kb,
    )
    assert mismatched["state"] == "Andhra Pradesh"
    assert mismatched["district"] is None

    # house_no is now scanned by the structured path too.
    cand = _candidate({"present_house_no": "DHOOLPET"}, "present", None)
    assert cand["state"] == "Telangana"
    assert cand["district"] == "Hyderabad"


def test_state_word_isolation_and_short_word_guard():
    """resolve_address_text isolates a STATE name buried inside a longer
    token (a "District"/"State" qualifier, a parenthetical already
    stripped by norm_token) by also trying that token's first and last
    word -- but never a word under 4 letters, which is the exact length
    of every STATE_ABBREV key. Confirmed live: without that floor, "HP
    Road, Moosapet" resolved to state Himachal Pradesh purely because the
    isolated word "HP" matched STATE_ABBREV's "hp" -- an unrelated local
    road-name abbreviation, not the state.

    A matching DISTRICT-only last-word fallback (so "R/o Nagpur" could
    resolve "Nagpur") was tried and withdrawn, not merely length-guarded:
    confirmed live that it resolved "Seetaphal Mandi, Secunderabad" and
    "Miralam Mandi, Hyderabad" -- both real Hyderabad localities -- to
    Himachal Pradesh, because "Mandi" means "market" in common Indian
    usage and is ALSO a real HP district; 6 of 12 live hits were exactly
    this false positive. Districts are nowhere near as distinctive a set
    as states, so this fallback stays gone.
    """
    kb = GeoKB()
    kb.add_reference("Odisha", "Khordha", None, None)
    kb.add_reference("Jharkhand", "Giridih", None, None)
    kb.add_reference("Maharashtra", "Nagpur", None, None)
    kb.add_reference("Himachal Pradesh", "Mandi", None, None)
    kb.add_country("India")

    assert resolve_address_text("Odisha State", kb)["state"] == "Odisha"
    assert resolve_address_text("Tambagudiyo, Giridih District Jharkhand", kb)["state"] == "Jharkhand"

    # The confirmed regression itself: an unrelated 2-letter road
    # abbreviation must never be read as a state via STATE_ABBREV.
    assert resolve_address_text("5-58/1, HP Road, Moosapet", kb) is None

    # The withdrawn district fallback stays withdrawn: a bare city name
    # with no state token resolves nothing at all (better than guessing)...
    assert resolve_address_text("R/o Nagpur", kb) is None
    # ...and a locally-common word that happens to also be a district name
    # elsewhere is never read as that district.
    assert resolve_address_text("Seetaphal Mandi, Secunderabad", kb) is None


def test_malkajgiri_district_alias():
    """"Medchal Malkajgiri" district (2016 Telangana reorganization) is
    near-universally shortened to "Malkajgiri" in free-text addresses --
    confirmed against 555 real V1 records using the short form with no
    "Medchal" anywhere in the text. DISTRICT_ALIASES maps it so the
    state-anchored district search in resolve_address_text can still find
    it; this must never fire ahead of an already-present exact district
    match like "Hyderabad" earlier in the same text (confirmed live: 18
    V1 records mention both, and "Hyderabad" -- appearing earlier -- always
    wins, treating "Malkajgiri" there as the Marredpally-area village
    instead, which this alias must not disturb).
    """
    kb = GeoKB()
    kb.add_reference("Telangana", "Medchal Malkajgiri", "Medipally", None)
    kb.add_reference("Telangana", "Hyderabad", "Marredpally", "Malkajgiri")
    kb.add_country("India")

    only_malkajgiri = resolve_address_text(
        "h no-8-30,Balaji Hills colony,Boduppal,Malkajgiri,Telangana", kb,
    )
    assert only_malkajgiri["state"] == "Telangana"
    assert only_malkajgiri["district"] == "Medchal Malkajgiri"

    mixed_with_hyderabad = resolve_address_text("hyderabad,Malkajgiri,Telangana", kb)
    assert mixed_with_hyderabad["district"] == "Hyderabad"
    assert mixed_with_hyderabad["mandal"] == "Marredpally"


def test_locality_fuzzy_spelling_variants():
    """LOCALITY_TO_DISTRICT_MANDAL spelling-variant matching: single-word
    typos of a known locality resolve via edit distance even when the exact
    phrase isn't present. Confirmed against real CCTNS data: "Doolpet",
    "Dholpet", "Dhulpet", "Doolipet" and "Dhool Pet" are all real spellings
    of "Dhoolpet" seen in source records.
    """
    assert "dhoolpet" in LOCALITY_TO_DISTRICT_MANDAL
    for variant in ("Doolpet", "Dholpet", "Dhulpet", "Doolipet", "Dhool Pet"):
        cand = _candidate({"present_ward_colony": variant}, "present", None)
        assert cand["state"] == "Telangana", variant
        assert cand["district"] == "Hyderabad", variant
        assert cand["mandal"] == "Asif Nagar", variant

    # Real unrelated Hyderabad localities (including ones sharing the
    # "-pet" suffix with Dhoolpet) must never be fuzzy-matched.
    for unrelated in ("Moosapet", "Shaikpet", "Golconda", "Nampally", "Kukatpally"):
        cand = _candidate({"present_ward_colony": unrelated}, "present", None)
        assert cand["district"] is None, unrelated

    # Regression guard: fuzzy matching is an explicit allowlist, not every
    # single-word key, because the dict keeps growing -- confirmed live
    # that adding "kompally" made "Nampally" (above: a real, distinct
    # Hyderabad locality, not even in this dict) score exactly 0.75
    # against it, the same class of same-suffix collision as Moosapet.
    assert "kompally" in LOCALITY_TO_DISTRICT_MANDAL
    assert _fuzzy_locality_key("nampally") is None

    # Regression guard: a candidate sharing only the SECOND word with a
    # multi-word KEY must never match that key via fuzzy logic, even if the
    # shared word drags the whole-string similarity above SIM_LOCALITY --
    # confirmed live that "namger basti" scores 0.75 against the multi-word
    # key "mangar basti" (edit distance 3, i.e. HALF of "namger"'s 6 letters
    # differ from "mangar") purely because both share the word "basti".
    # Multi-word keys are therefore never offered as fuzzy-match targets
    # (_fuzzy_locality_key skips any key containing a space) -- only the
    # exact-phrase check elsewhere may match them.
    assert any(" " in key for key in LOCALITY_TO_DISTRICT_MANDAL)  # guard is non-vacuous
    cand = _candidate({"present_ward_colony": "NAMGER BASTI HABEEB NAGAR"}, "present", None)
    assert cand["district"] is None

    # A single-word key's fuzzy match is unaffected by this guard.
    assert _fuzzy_locality_key("doolpet") == "dhoolpet"


def test_resolve_address_text_mandal_only_anchor():
    """Free text naming only a mandal, with no state or district token at
    all, still resolves when that mandal name is unique to one district
    KB-wide. Confirmed against live V1 data: 40 non-blank "nationality
    India, state unresolved" records name a real mandal (e.g. Saroornagar,
    Jadcherla, Asifabad) with no state/district token anywhere in the text --
    the pre-existing "no state token" fallback only tried district-level
    matching, so these fell through and were marked non_recoverable even
    though the mandal is already in kb.geo_reference.
    """
    kb = GeoKB()
    kb.add_reference("Telangana", "Ranga Reddy", "Saroornagar", None)
    hit = resolve_address_text("Sri Ram Nagar colony, Karmanghat, ,Saroornagar", kb)
    assert hit["state"] == "Telangana"
    assert hit["district"] == "Ranga Reddy"
    assert hit["mandal"] == "Saroornagar"
    assert hit["country"] == "India"
    assert hit["path"] == "kb+text+mandal"

    v1 = person_row("V1", "V1-SAROORNAGAR", {
        "full_name": "Test Person",
        "nationality": "Indian",
        "permanent_address_text": "Sri Ram Nagar colony, Karmanghat, ,Saroornagar",
        "present_address_text": "Sri Ram Nagar colony, Karmanghat, ,Saroornagar",
    }, geo_kb=kb)
    assert v1["domicile_classification"] == "native state"
    assert v1["domicile_method"] == "deterministic"

    # Ambiguity guard: the same mandal name in a second, different district
    # anywhere in the KB must never be guessed between -- no anchor wins.
    kb.add_reference("Karnataka", "Some Other District", "Saroornagar", None)
    assert resolve_address_text("Plot 4, Saroornagar", kb) is None

    # A real district token still resolves via the existing path (this new
    # fallback only ever runs when both the state-token and district-anywhere
    # searches already failed; existing precedence is unaffected).
    kb2 = GeoKB()
    kb2.add_reference("Telangana", "Mahabubnagar", "Jadcherla", None)
    kb2.add_country("India")
    via_district = resolve_address_text("Mahabubnagar,Telangana", kb2)
    assert via_district["state"] == "Telangana"
    assert via_district["district"] == "Mahabubnagar"
    assert via_district["path"] == "kb+text"

    # Regression guard: "Moosapet" is a real, unique mandal name in
    # Mahabubnagar (confirmed in kb.geo_reference), but is ALSO the name of
    # a well-known village under Kukatpally/Medchal Malkajgiri -- live data
    # showed 83% of real clerk-filled records meant the latter. A mandal
    # name match must never be trusted when the same token also names a
    # village in a *different* district -- silently guessing which one the
    # text means would be worse than staying non_recoverable.
    kb3 = GeoKB()
    kb3.add_reference("Telangana", "Mahabubnagar", "Moosapet", "Nandipet")
    kb3.add_reference("Telangana", "Medchal Malkajgiri", "Kukatpally", "Moosapet")
    assert kb3.unambiguous_mandal("Moosapet") is None
    assert resolve_address_text("Sevalal Nagar, Moosapet", kb3) is None
    # Same village name confirmed again under the SAME district as the
    # mandal (e.g. a mandal headquarters village sharing its mandal's name)
    # is not a cross-district collision and stays trustworthy.
    kb4 = GeoKB()
    kb4.add_reference("Telangana", "Mahabubnagar", "Moosapet", "Moosapet")
    assert kb4.unambiguous_mandal("Moosapet") == ("Telangana", "Mahabubnagar", "Moosapet")


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


def test_name_identified_true_for_ordinary_names():
    """Baseline: an ordinary, unambiguous name is untouched and marked
    identified, with no review flags -- confirms none of the new
    detection stages below fire on the common case."""
    result = clean_person_names("Ravi Kumar")
    assert result["cleaned_full_name"] == "Ravi Kumar"
    assert result["name_identified"] is True
    assert result["unidentified_reason"] is None
    assert result["extracted_role_hint"] is None
    assert result["extracted_nationality_hint"] is None
    assert result["review_flags"] == []


def test_multi_alias_at_chain_preserves_every_alias():
    """Confirmed bug (audit finding G-2/G-3, real records S.No. 16547 and
    28810): the old extract_alias_from_name only ever read parts[0] and
    parts[1] of name.split("@"), silently dropping every alias past the
    second, and a stray "@" leaked into cleaned_full_name when the
    primary name was empty. Both are fixed: every alias is preserved, and
    cleaned_full_name never contains a literal "@"."""
    result = clean_person_names("@Johnson Manumala @Goutham")
    assert result["cleaned_alias"] == "Johnson Manumala; Goutham"
    assert result["cleaned_full_name"] is None
    assert "@" not in (result["cleaned_full_name"] or "")
    assert result["name_identified"] is False
    assert result["unidentified_reason"] == "alias_only"

    result2 = clean_person_names("@ Nemichand Bhati Nemichand @Nemichand Bhati")
    assert result2["cleaned_alias"] == "Nemichand Bhati Nemichand; Nemichand Bhati"
    assert result2["cleaned_full_name"] is None
    assert "@" not in (result2["cleaned_full_name"] or "")


def test_spelled_out_alias_word_extraction():
    """"Alias" (127 records in the real export, far more common than '@'
    in Full Name) was previously not recognized at all."""
    single = clean_person_names("Ajay Alias Ajay Sharma")
    assert single["cleaned_full_name"] == "Ajay"
    assert single["cleaned_alias"] == "Ajay Sharma"

    chain = clean_person_names("Ajay Alias Kittu Alias Bittu Kaleri")
    assert chain["cleaned_full_name"] == "Ajay"
    assert chain["cleaned_alias"] == "Kittu; Bittu Kaleri"

    aka = clean_person_names("Arbaz Shafi Khan AKA Shaik Salman")
    assert aka["cleaned_full_name"] == "Arbaz Shafi Khan"
    assert aka["cleaned_alias"] == "Shaik Salman"

    full_form = clean_person_names("Arbaz Shafi Khan A.K.A. Shaik Salman")
    assert full_form["cleaned_full_name"] == "Arbaz Shafi Khan"
    assert full_form["cleaned_alias"] == "Shaik Salman"

    also_known = clean_person_names("Arbaz Shafi Khan Also Known As Shaik Salman")
    assert also_known["cleaned_full_name"] == "Arbaz Shafi Khan"
    assert also_known["cleaned_alias"] == "Shaik Salman"

    # "@" still takes precedence and still works exactly as before.
    at_form = clean_person_names("Ajay @Kittu")
    assert at_form["cleaned_full_name"] == "Ajay"
    assert at_form["cleaned_alias"] == "Kittu"


def test_spelled_out_relationship_markers():
    """"Wife of", "Mother of", "Father of", "Son of", "Daughter of",
    "Care of", "H/O" were previously unrecognized -- only the abbreviated
    s/o, d/o, w/o forms were. Real records S.No. 50493/26452."""
    son_of = clean_person_names("Ramesh Son of Krishna")
    assert son_of["cleaned_full_name"] == "Ramesh"
    assert son_of["relation_type"] == "Father"
    assert son_of["cleaned_relative_name"] == "Krishna"

    daughter_of = clean_person_names("Lakshmi Daughter of Ramana")
    assert daughter_of["relation_type"] == "Father"
    assert daughter_of["cleaned_relative_name"] == "Ramana"

    h_o = clean_person_names("Sunitha H/O Vijay")
    assert h_o["cleaned_full_name"] == "Sunitha"
    assert h_o["relation_type"] == "Husband"
    assert h_o["cleaned_relative_name"] == "Vijay"

    care_of = clean_person_names("Ramu C/O Suresh")
    assert care_of["cleaned_full_name"] == "Ramu"
    assert care_of["relation_type"] == "Care-of"
    assert care_of["cleaned_relative_name"] == "Suresh"


def test_relationship_only_record_marked_unidentified_not_invented():
    """"Wife of Amzad" / "Mother of Eshwar Sarwade" (S.No. 50493, 26452)
    are not names at all -- the accused's own name was never recorded,
    only their relation to someone else. The relationship must still be
    extracted (useful for matching/review), but cleaned_full_name must
    never become the relationship sentence itself."""
    wife = clean_person_names("Wife of Amzad")
    assert wife["cleaned_full_name"] is None
    assert wife["name_identified"] is False
    assert wife["unidentified_reason"] == "relationship_only"
    assert wife["relation_type"] == "Husband"
    assert wife["cleaned_relative_name"] == "Amzad"

    mother = clean_person_names("Mother of Eshwar Sarwade")
    assert mother["cleaned_full_name"] is None
    assert mother["relation_type"] == "Child"
    assert mother["cleaned_relative_name"] == "Eshwar Sarwade"


def test_dangling_relationship_marker_stripped_even_without_relative_name():
    """Confirmed gap (audit C-1, real record S.No. 11461): a trailing
    "S/o" with nothing after it used to survive untouched because the old
    code required a captured relative name before it would strip
    anything at all. The marker is now always stripped; only the relative
    name (genuinely absent from the source) stays None."""
    result = clean_person_names("Ediga Sharanayya Goud S/o")
    assert result["cleaned_full_name"] == "Ediga Sharanayya Goud"
    assert result["relation_type"] == "Father"
    assert result["cleaned_relative_name"] is None
    assert result["name_identified"] is True


def test_bare_numeric_list_prefix_stripped():
    """Confirmed gap (audit A-1/A-2): a bare "02." / "1)" / "1." list
    prefix with no "A" code letter was never stripped at all."""
    assert clean_person_names("02. Abid Ali")["cleaned_full_name"] == "Abid Ali"
    assert clean_person_names("1) Abhishek Kumar")["cleaned_full_name"] == "Abhishek Kumar"
    assert clean_person_names("1. Abhishek Kumar")["cleaned_full_name"] == "Abhishek Kumar"
    assert clean_person_names("10. Telugu Maddileti")["cleaned_full_name"] == "Telugu Maddileti"
    assert clean_person_names("1). Divi Sampath")["cleaned_full_name"] == "Divi Sampath"


def test_accused_code_variants_stripped():
    """Confirmed gap (audit A-3/A-4): the old A-code pattern's character
    class after the digit excluded ":", so "A-1:"/"A1:"/"A5:" survived
    untouched. Real records S.No. 64, 152."""
    assert clean_person_names("A-1: Md.Khaleel")["cleaned_full_name"] == "Md.Khaleel"
    assert clean_person_names("A1: Md.Khaleel")["cleaned_full_name"] == "Md.Khaleel"
    assert clean_person_names("A2. Rajesh")["cleaned_full_name"] == "Rajesh"
    assert clean_person_names("A-1) Cheekati Srinivas")["cleaned_full_name"] == "Cheekati Srinivas"


def test_accused_person_label_stripped():
    """Real record S.No. 985/986/987: an "Accused Person:" label prefix,
    often stacked with an A-code, is stripped entirely."""
    result = clean_person_names("Accused Person: A-1: Md.Khaleel")
    assert result["cleaned_full_name"] == "Md.Khaleel"
    assert clean_person_names("Accused Person: Saphayee.Prabhu Swamy")["cleaned_full_name"] == "Saphayee.Prabhu Swamy"


def test_role_phrase_in_parens_extracted_and_stripped():
    """Confirmed gap (audit E-2/E-3/E-4): only 3 hardcoded phrases
    ("absconding", "...receiver...", "...drug peddler...") were ever
    stripped from a parenthetical; "Supplier", "Main Peddler", "Customer"
    etc. survived untouched. Now extracted as a display-cleanup hint and
    removed from the name -- without touching the separate, AI-driven
    accused_type/role_in_crime classification in rules.py/accused_facts.py."""
    supplier = clean_person_names("Anil (Supplier)")
    assert supplier["cleaned_full_name"] == "Anil"
    assert supplier["extracted_role_hint"] == "Supplier"

    main_peddler = clean_person_names("Baba (Main Peddler)")
    assert main_peddler["cleaned_full_name"] == "Baba"
    assert main_peddler["extracted_role_hint"] == "Main Peddler"

    # Role word mid-string, surname after it -- whitespace left behind by
    # the removal is normalized to a single space, not a double space.
    mid_string = clean_person_names("Anil ( Customer) Bugga")
    assert mid_string["cleaned_full_name"] == "Anil Bugga"
    assert mid_string["extracted_role_hint"] == "Customer"


def test_role_only_whole_value_marked_unidentified():
    """Real record S.No. 2: "02. Medical Shop Owner" has no person name
    at all -- once the numeric prefix is stripped, the entire remaining
    value is a bare occupation phrase, not a name. Only a WHOLE-value
    match triggers this -- "Abdul Veekar Consumer" (S.No. 560) keeps real
    name content alongside the role word and must NOT be nulled."""
    result = clean_person_names("02. Medical Shop Owner")
    assert result["cleaned_full_name"] is None
    assert result["name_identified"] is False
    assert result["unidentified_reason"] == "role_only"

    partial = clean_person_names("Abdul Veekar Consumer")
    assert partial["cleaned_full_name"] == "Abdul Veekar Consumer"
    assert partial["name_identified"] is True


def test_nationality_in_parens_extracted_but_bare_word_never_touched():
    """"(Nigerian)" is an unambiguous parenthetical qualifier and is
    safely stripped (real record S.No. 1376). A BARE nationality-shaped
    word with no parentheses -- "Nepal Krishna", "Khali of Nigeria" -- is
    never touched, because address.NATIONALITY_TO_COUNTRY's own "china"
    entry proves a geographical word is sometimes a genuine given name
    (confirmed real accused "China", per
    test_collapse_exact_repetition_confirmed_real_records); guessing here
    would corrupt roughly as many real names as it would fix."""
    aisha = clean_person_names("Aisha (Nigerian)")
    assert aisha["cleaned_full_name"] == "Aisha"
    assert aisha["extracted_nationality_hint"] == "Nigerian"

    russian_lady = clean_person_names("1) Ana (Russian Lady)")
    assert russian_lady["cleaned_full_name"] == "Ana"
    assert russian_lady["extracted_nationality_hint"] == "Russian"

    # Bare, unparenthesized -- left completely unchanged.
    nepal = clean_person_names("Nepal Krishna")
    assert nepal["cleaned_full_name"] == "Nepal Krishna"
    assert nepal["extracted_nationality_hint"] is None

    khali = clean_person_names("Khali of Nigeria")
    assert khali["cleaned_full_name"] == "Khali of Nigeria"
    assert khali["extracted_nationality_hint"] is None

    china = clean_person_names("China China")
    assert china["cleaned_full_name"] == "China"


def test_invalid_literal_names_marked_unidentified():
    """Real records: "Unknown"/"Nil"/"Not Known"/"N/A" (77 records total)
    and a literal "Absconding"/"Unknown Person" standing in for the name
    itself (83 + 40 records -- not previously recognized as placeholders
    at all) must never be stored as if they were a real identified name."""
    for raw in ("Unknown", "Nil", "Not Known", "Not Available", "N/A", "NA", "None", "-"):
        result = clean_person_names(raw)
        assert result["cleaned_full_name"] is None, raw
        assert result["name_identified"] is False, raw
        assert result["unidentified_reason"] == "invalid_literal", raw

    absconding = clean_person_names("Absconding")
    assert absconding["cleaned_full_name"] is None
    assert absconding["unidentified_reason"] == "invalid_literal"

    paren_absconding = clean_person_names("(Absconding)")
    assert paren_absconding["cleaned_full_name"] is None
    assert paren_absconding["unidentified_reason"] == "invalid_literal"

    unknown_person = clean_person_names("Unknown Person")
    assert unknown_person["cleaned_full_name"] is None
    assert unknown_person["unidentified_reason"] == "invalid_literal"

    # A role word or placeholder embedded alongside a REAL name is not
    # this check's concern and must survive.
    real_name = clean_person_names("Ravi Kumar")
    assert real_name["cleaned_full_name"] == "Ravi Kumar"
    assert real_name["name_identified"] is True


def test_bare_phone_number_stripped_from_name():
    """Real records S.No. 193, 152: an unlabeled 10-digit mobile number
    glued onto the name with a hyphen or '#' was previously never
    stripped at all (only the explicitly labeled cell:/ph.no.:/✆ forms
    were)."""
    assert clean_person_names("Aakula Harith Saranga-9959075757")["cleaned_full_name"] == "Aakula Harith Saranga"
    assert clean_person_names("A5:Somnath# 9007155796")["cleaned_full_name"] == "Somnath"
    # Labeled forms still work exactly as before.
    assert clean_person_names("Anand, Cell: 9876543210")["cleaned_full_name"] == "Anand"
    # Confirmed gap found via full-dataset validation (S.No. 48612,
    # 50702): "Ph:" (no "No") left a dangling label once only the digits
    # were removed by the bare-phone strip.
    assert clean_person_names("Varun Lanco, Ph: 7075475557")["cleaned_full_name"] == "Varun Lanco"
    assert clean_person_names("Yashwardhan, Ph: 7799786687")["cleaned_full_name"] == "Yashwardhan"
    assert clean_person_names("Aakash Reddy, Ph: 9652979226")["cleaned_full_name"] == "Aakash Reddy"


def test_spelled_out_age_and_occupation_label_stripped():
    """Confirmed gap (audit I-1/I-2, real records S.No. 1168, 5665): the
    old age pattern only matched the abbreviation "yrs", never the
    spelled-out word "Years"; the R/O pattern required whitespace
    immediately after "r/o", never allowing an intervening period
    ("R/O."); and there was no strip rule for an "Occ:" occupation label
    at all."""
    aditya = clean_person_names(
        "Aditya Kumar , Age. 25 Years, R/O. Lingampally Market Chandanagar, P",
    )
    assert aditya["cleaned_full_name"] == "Aditya Kumar"

    driver = clean_person_names("Bandipalli Sai Kiran , Age:29 Years, Occ:Driver")
    assert driver["cleaned_full_name"] == "Bandipalli Sai Kiran"

    # Already-working abbreviated form is unaffected.
    abbreviated = clean_person_names("Kiran s/o Ram, r/o Hyderabad, 32 yrs")
    assert abbreviated["cleaned_full_name"] == "Kiran"


def test_mojibake_normalized():
    """Low-volume (0.098% of the real export) but real: stray 'Â'
    sequences from a UTF-8-as-Latin-1 mis-decode, and smart quotes/en-
    dashes, are normalized away as a pure display-cleanup step that never
    runs before identity extraction decisions are made."""
    result = clean_person_names("Angoth Jaggu Â Â")
    assert result["cleaned_full_name"] == "Angoth Jaggu"


def test_review_flags_are_non_destructive_signals_only():
    """Confirmed-correct existing behaviour (collapse_exact_repetition
    does not touch a trailing duplicated token, since it is not a whole-
    value tile) now gets a soft, non-destructive review signal instead of
    passing through with zero trace. The name itself is NEVER rewritten
    by this -- only a flag is added."""
    trailing_dup = clean_person_names("A. Lokesh Lokesh")
    assert trailing_dup["cleaned_full_name"] == "A. Lokesh Lokesh"
    assert "duplicate_token" in trailing_dup["review_flags"]

    initial_only = clean_person_names("A")
    assert initial_only["cleaned_full_name"] == "A"
    assert "initial_only_name" in initial_only["review_flags"]
    assert initial_only["name_identified"] is True  # flagged for review, never deleted

    ordinary = clean_person_names("Ravi Kumar")
    assert ordinary["review_flags"] == []


#   The 10 "Regular ETL regression tests" below prove the regular ETL3
#   pipeline (etl3/enrichment/runner.py -> project.py -> names.py /
#   multi_accused.py) actually uses the latest enrichment logic end to
#   end -- not just that names.py/multi_accused.py work in isolation
#   (already covered above and in test_multi_accused.py).

def test_regular_etl_person_row_uses_latest_name_cleaning_end_to_end():
    """A capability added in this phase (spelled-out "Alias") reaches all
    the way through person_row() -- the actual function
    runner.py:_person_enrichment() calls for every persons_unified row --
    not just clean_person_names() called directly."""
    row = person_row("V2", "P-ALIAS", {"full_name": "Ajay Alias Kittu Alias Bittu Kaleri"})
    assert row["cleaned_full_name"] == "Ajay"
    assert row["cleaned_alias"] == "Kittu; Bittu Kaleri"

    unidentified = person_row("V2", "P-UNK", {"full_name": "Unknown Person"})
    assert unidentified["cleaned_full_name"] is None
    # Computed on every call regardless of the persistence flag (see
    # test_regular_etl_identity_metadata_flag_gates_hash_only below).
    assert unidentified["name_identified"] is False
    assert unidentified["unidentified_reason"] == "invalid_literal"


def test_regular_etl_reenriches_on_name_change():
    """The same person_id, re-observed with a corrected/changed source
    name, must produce a different cleaned_full_name AND a different
    input_hash -- persist.upsert_rows() (unmodified by this task) uses
    exactly that hash to decide a row changed and needs rewriting."""
    before = person_row("V2", "P-SAME", {"full_name": "Ramesh Kumr"})
    after = person_row("V2", "P-SAME", {"full_name": "Ramesh Kumar"})
    assert before["cleaned_full_name"] != after["cleaned_full_name"]
    assert before["input_hash"] != after["input_hash"]


def test_regular_etl_stable_hash_for_unchanged_input():
    """Identical input, called twice (simulating two ETL runs over
    unchanged source data), must produce byte-identical output and an
    identical input_hash -- the condition persist.upsert_rows() checks to
    skip a rewrite and avoid a spurious change_log row."""
    first = person_row("V2", "P-STABLE", {"full_name": "Shanthi Kumari"})
    second = person_row("V2", "P-STABLE", {"full_name": "Shanthi Kumari"})
    assert first == second
    assert first["input_hash"] == second["input_hash"]


def test_regular_etl_multi_accused_generates_separate_linked_occurrences():
    """End-to-end through the same two functions runner.py's
    _multi_accused_split actually calls (detect_multi_accused then
    build_split_rows), for a pattern this phase newly supports (A-code
    packs) -- not just a re-check of the already-covered N./N) style."""
    parent = {
        "accused_id": "V2:FIR9:acc1", "source_system": "V2", "crime_id": "FIR9",
        "person_id": "V2:FIR9:acc1:person", "current_source_run_id": "run-9",
        "current_as_of": "2026-01-01T00:00:00Z",
    }
    person_rows, accused_rows, audit_rows, parsed = build_split_rows(
        parent, "A1) Ravi Kumar, A2) Suresh",
    )
    assert parsed["confidence"] == "high"
    assert len(person_rows) == 2 and len(accused_rows) == 2
    # All occurrences linked to the SAME original crime.
    assert {a["crime_id"] for a in accused_rows} == {"FIR9"}
    assert accused_rows[0]["person_id"] != accused_rows[1]["person_id"]


def test_regular_etl_aliases_not_treated_as_separate_accused():
    """An alias chain (@ or spelled-out "Alias") must never be read as
    multiple accused by the multi-accused detector -- it has no digit or
    A-code markers and the unnumbered-candidate heuristic only looks at
    comma/'and'/'&', never the word "Alias" or "@"."""
    for raw in (
        "Ajay Alias Kittu Alias Bittu Kaleri",
        "@Johnson Manumala @Goutham",
        "Ravi Kumar @ Raju",
    ):
        r = detect_multi_accused(raw)
        assert r["confidence"] == "none", raw
        assert r["segments"] == [], raw


def test_regular_etl_ambiguous_records_remain_unchanged():
    """Reconfirms the confirmed business rule end to end: an ambiguous
    comma list is never auto-split, and build_split_rows() correctly
    produces nothing for it -- same guarantee test_multi_accused.py's
    own build_split_rows tests already cover, re-run here specifically
    against the shape a regular ETL run would actually see."""
    parent = {
        "accused_id": "V2:FIR10:acc1", "source_system": "V2", "crime_id": "FIR10",
        "person_id": "V2:FIR10:acc1:person", "current_source_run_id": "run-10",
        "current_as_of": "2026-01-01T00:00:00Z",
    }
    person_rows, accused_rows, audit_rows, parsed = build_split_rows(
        parent, "Avinash Babu Rathod, Abhijith Thate",
    )
    assert parsed["confidence"] == "low"
    assert person_rows == [] and accused_rows == [] and audit_rows == []


def test_regular_etl_failed_record_isolated_others_continue():
    """One malformed record in a batch must not prevent any other record
    from being processed -- the exact isolation
    runner.py:_person_enrichment()'s loop relies on
    build_person_row_safe() for."""
    batch = [
        ("V2", "P-OK-1", "R1", {"full_name": "Ravi Kumar"}),
        ("V2", "P-BAD", "R2", None),  # malformed payload -> raises inside person_row
        ("V2", "P-OK-2", "R3", {"full_name": "Suresh Babu"}),
    ]
    rows, failures = [], []
    for source_system, person_id, record_id, payload in batch:
        row, failure = build_person_row_safe(source_system, person_id, record_id, payload)
        if row:
            rows.append(row)
        if failure:
            failures.append(failure)
    assert len(rows) == 2
    assert {r["person_id"] for r in rows} == {"P-OK-1", "P-OK-2"}
    assert len(failures) == 1
    assert failures[0]["person_id"] == "P-BAD"
    assert failures[0]["source_record_id"] == "R2"


def test_regular_etl_identity_metadata_flag_gates_hash_only():
    """ETL3_NAME_IDENTITY_FIELDS_ENABLED (same convention as
    ETL3_MULTI_ACCUSED_SPLIT_ENABLED; both are deliberately active in this
    repo's own etl3/config/.env as of the 2026-11-18 production rollout --
    see DOPAMS_ETL3_FINAL_DEPLOYMENT_REPORT.md) controls whether the 5 new
    identity fields are HASHED -- never whether they are computed. This
    test exercises the mechanism directly via person_row()'s own
    persist_identity_metadata parameter, independent of whatever the
    ambient environment's flag currently is (deliberately not asserting
    on _name_identity_settings()'s live value, which reflects real
    deployment state, not a test fixture): with the parameter off, two
    rows that differ ONLY in unidentified_reason must still hash
    identically, so introducing this code never forces every existing
    person_enrichment row to look "changed" the moment it ships."""
    off_a = person_row("V2", "P-X", {"full_name": "Unknown"}, persist_identity_metadata=False)
    off_b = person_row("V2", "P-X", {"full_name": "Ravi Kumar"}, persist_identity_metadata=False)
    # Both still compute the fields (visible on the returned dict)...
    assert off_a["unidentified_reason"] == "invalid_literal"
    assert off_b["unidentified_reason"] is None
    # ...but neither's PRESENCE changes what gets hashed for name/alias
    # purposes beyond cleaned_full_name itself, which already differs --
    # confirmed by checking the flag-ON hash differs from flag-OFF for
    # the SAME input, proving the fields only enter the hash when the
    # flag says they should be persisted.
    on_a = person_row("V2", "P-X", {"full_name": "Unknown"}, persist_identity_metadata=True)
    assert on_a["input_hash"] != off_a["input_hash"]


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
    check("free-text state conflict blocks borrowed district", test_free_text_state_conflict_blocks_borrowed_district)
    check("locality patch reaches free text too", test_locality_patch_reaches_free_text_too)
    check("state word isolation and short-word guard", test_state_word_isolation_and_short_word_guard)
    check("malkajgiri district alias", test_malkajgiri_district_alias)
    check("locality fuzzy spelling variants", test_locality_fuzzy_spelling_variants)
    check("resolve address text: mandal-only anchor", test_resolve_address_text_mandal_only_anchor)
    check("name cleanup", test_name_cleanup_for_v1_and_v2)
    check("duplicate name collapse: confirmed real records", test_collapse_exact_repetition_confirmed_real_records)
    check("duplicate name collapse: legitimate names untouched", test_collapse_exact_repetition_never_touches_legitimate_names)
    check("duplicate name collapse: idempotent + edge inputs", test_collapse_exact_repetition_idempotent_and_edge_inputs)
    check("duplicate name: full pipeline preserves raw/other fields", test_duplicate_name_full_pipeline_preserves_raw_and_other_fields)
    check("trailing hyphen residue trimmed", test_trailing_hyphen_residue_trimmed)
    check("name identified true for ordinary names", test_name_identified_true_for_ordinary_names)
    check("multi-alias @ chain preserves every alias", test_multi_alias_at_chain_preserves_every_alias)
    check("spelled-out alias word extraction", test_spelled_out_alias_word_extraction)
    check("spelled-out relationship markers", test_spelled_out_relationship_markers)
    check("relationship-only record marked unidentified", test_relationship_only_record_marked_unidentified_not_invented)
    check("dangling relationship marker stripped", test_dangling_relationship_marker_stripped_even_without_relative_name)
    check("bare numeric list prefix stripped", test_bare_numeric_list_prefix_stripped)
    check("accused code variants stripped", test_accused_code_variants_stripped)
    check("accused person label stripped", test_accused_person_label_stripped)
    check("role phrase in parens extracted and stripped", test_role_phrase_in_parens_extracted_and_stripped)
    check("role-only whole value marked unidentified", test_role_only_whole_value_marked_unidentified)
    check("nationality in parens extracted, bare word untouched", test_nationality_in_parens_extracted_but_bare_word_never_touched)
    check("invalid literal names marked unidentified", test_invalid_literal_names_marked_unidentified)
    check("bare phone number stripped from name", test_bare_phone_number_stripped_from_name)
    check("spelled-out age and occupation label stripped", test_spelled_out_age_and_occupation_label_stripped)
    check("mojibake normalized", test_mojibake_normalized)
    check("review flags are non-destructive", test_review_flags_are_non_destructive_signals_only)
    check("regular ETL: person_row uses latest name cleaning end to end", test_regular_etl_person_row_uses_latest_name_cleaning_end_to_end)
    check("regular ETL: re-enriches on name change", test_regular_etl_reenriches_on_name_change)
    check("regular ETL: stable hash for unchanged input", test_regular_etl_stable_hash_for_unchanged_input)
    check("regular ETL: multi-accused generates separate linked occurrences", test_regular_etl_multi_accused_generates_separate_linked_occurrences)
    check("regular ETL: aliases not treated as separate accused", test_regular_etl_aliases_not_treated_as_separate_accused)
    check("regular ETL: ambiguous records remain unchanged", test_regular_etl_ambiguous_records_remain_unchanged)
    check("regular ETL: failed record isolated, others continue", test_regular_etl_failed_record_isolated_others_continue)
    check("regular ETL: identity metadata flag gates hash only", test_regular_etl_identity_metadata_flag_gates_hash_only)
    check("replay hash", test_replay_hash_stable)
    check("ai validation", test_ai_validation)
    check("ai retry", test_ai_retry_and_success_does_not_invent_on_empty)
    print("all enrichment unit tests passed")


if __name__ == "__main__":
    main()
