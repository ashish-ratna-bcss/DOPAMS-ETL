"""
Unit tests for etl3/merger/field_maps.py -- no DB required. Run with:
    python etl3/tests/test_field_maps.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.merger.field_maps import compose_v2_address_text, PERSONS


def check(label, fn):
    try:
        fn()
        print(f"PASS {label}")
        return True
    except Exception as exc:
        print(f"FAIL {label}: {exc}")
        return False


def test_house_no_kept_verbatim_when_present():
    # house_no is usually already a full free-text address -- must not be
    # rewritten or appended to, even when other fields are also populated.
    payload = {
        "present_house_no": "12-3, Gandhi Nagar",
        "present_locality_village": "Gandhi Nagar",
        "present_district": "Hyderabad",
        "present_state_ut": "Telangana",
    }
    assert compose_v2_address_text(payload, "present") == "12-3, Gandhi Nagar"


def test_district_plus_state_composed_when_house_no_and_locality_missing():
    # The exact reported bug: only district+state_ut captured at source.
    # Previously this returned "ALWAR" alone, silently dropping the state.
    payload = {
        "present_house_no": None,
        "present_locality_village": None,
        "present_district": "ALWAR",
        "present_state_ut": "RAJASTHAN",
    }
    assert compose_v2_address_text(payload, "present") == "ALWAR, RAJASTHAN"


def test_mandal_and_state_composed_when_district_missing():
    # district absent but mandal+state present -- old OR-chain returned
    # None here (district was the last rung); mandal must now surface.
    payload = {
        "present_house_no": None,
        "present_locality_village": None,
        "present_area_mandal": "Nagaur",
        "present_district": None,
        "present_state_ut": "RAJASTHAN",
    }
    assert compose_v2_address_text(payload, "present") == "Nagaur, RAJASTHAN"


def test_full_hierarchy_composed_in_order():
    payload = {
        "present_house_no": None,
        "present_locality_village": "Domalguda",
        "present_area_mandal": "Secunderabad",
        "present_district": "Hyderabad",
        "present_state_ut": "Telangana",
        "present_country": "India",
    }
    assert compose_v2_address_text(payload, "present") == (
        "Domalguda, Secunderabad, Hyderabad, Telangana, India"
    )


def test_country_alone_not_surfaced():
    # Only country captured (the common "India" boilerplate default, no
    # locality/mandal/district/state) is treated the same as no data --
    # otherwise a genuine source gap would start looking like a real value.
    payload = {
        "present_house_no": None,
        "present_locality_village": None,
        "present_area_mandal": None,
        "present_district": None,
        "present_state_ut": None,
        "present_country": "India",
    }
    assert compose_v2_address_text(payload, "present") is None


def test_country_appended_only_when_other_part_present():
    payload = {
        "present_house_no": None,
        "present_district": "ALWAR",
        "present_state_ut": "RAJASTHAN",
        "present_country": "India",
    }
    assert compose_v2_address_text(payload, "present") == "ALWAR, RAJASTHAN, India"


def test_all_fields_null_returns_none():
    payload = {
        "present_house_no": None,
        "present_locality_village": None,
        "present_area_mandal": None,
        "present_district": None,
        "present_state_ut": None,
        "present_country": None,
    }
    assert compose_v2_address_text(payload, "present") is None


def test_empty_string_fields_treated_as_missing():
    payload = {
        "present_house_no": "  ",
        "present_locality_village": "",
        "present_district": "ALWAR",
        "present_state_ut": "RAJASTHAN",
    }
    assert compose_v2_address_text(payload, "present") == "ALWAR, RAJASTHAN"


def test_duplicate_segment_not_repeated():
    # Same text captured in two fields (e.g. locality re-entered as district)
    # must not appear twice in the composed string.
    payload = {
        "present_house_no": None,
        "present_locality_village": "Alwar",
        "present_district": "Alwar",
        "present_state_ut": "Rajasthan",
    }
    assert compose_v2_address_text(payload, "present") == "Alwar, Rajasthan"


def test_permanent_prefix_independent_of_present():
    payload = {
        "present_house_no": "12-3, Gandhi Nagar",
        "permanent_house_no": None,
        "permanent_locality_village": None,
        "permanent_district": "Nagaon",
        "permanent_state_ut": "Assam",
    }
    assert compose_v2_address_text(payload, "present") == "12-3, Gandhi Nagar"
    assert compose_v2_address_text(payload, "permanent") == "Nagaon, Assam"


def test_v2_persons_map_wired_to_compose_function():
    payload = {
        "present_house_no": None,
        "present_locality_village": None,
        "present_district": "ALWAR",
        "present_state_ut": "RAJASTHAN",
        "permanent_house_no": None,
        "permanent_locality_village": None,
        "permanent_district": None,
        "permanent_state_ut": None,
    }
    field_map = PERSONS["V2"]["map"]
    assert field_map["present_address_text"](payload) == "ALWAR, RAJASTHAN"
    assert field_map["permanent_address_text"](payload) is None


def test_v1_persons_map_untouched():
    # V1 persons map must still read the single source field verbatim --
    # this fix is scoped to V2 only.
    field_map = PERSONS["V1"]["map"]
    assert field_map["present_address_text"] == "accused_present_address"
    assert field_map["permanent_address_text"] == "accused_permanent_address"


def main():
    checks = [
        ("house_no kept verbatim when present", test_house_no_kept_verbatim_when_present),
        ("district+state composed when house_no/locality missing", test_district_plus_state_composed_when_house_no_and_locality_missing),
        ("mandal+state composed when district missing", test_mandal_and_state_composed_when_district_missing),
        ("full hierarchy composed in order", test_full_hierarchy_composed_in_order),
        ("country alone not surfaced", test_country_alone_not_surfaced),
        ("country appended only when other part present", test_country_appended_only_when_other_part_present),
        ("all fields null returns None", test_all_fields_null_returns_none),
        ("empty string fields treated as missing", test_empty_string_fields_treated_as_missing),
        ("duplicate segment not repeated", test_duplicate_segment_not_repeated),
        ("permanent prefix independent of present", test_permanent_prefix_independent_of_present),
        ("V2 persons map wired to compose function", test_v2_persons_map_wired_to_compose_function),
        ("V1 persons map untouched", test_v1_persons_map_untouched),
    ]
    passed = sum(1 for label, fn in checks if check(label, fn))
    print(f"{passed}/{len(checks)} passed")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
