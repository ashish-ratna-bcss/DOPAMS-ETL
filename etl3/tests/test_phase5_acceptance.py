"""Phase 5 final acceptance checks for the five sign-off areas.

Run with: python etl3/tests/test_phase5_acceptance.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.db import connections
from etl3.merger import field_maps
from etl3.merger.ps_enrichment import (
    build_hierarchy_index,
    classify_station,
    hierarchy_records,
    match_v1_station,
    normalize_ps_name,
    normalize_district,
    preserve_derived_crime_json,
    _resolution,
)
from etl3.merger.v1_accused_grouping import logical_key
from etl3.run_phase4_consolidation import UNIFIED_V2_ONLY, accused_relation_v2
from etl3.sync.catalog import MODULES
from etl3.sync.reconcile import classify_module


def check(label, fn):
    try:
        fn()
        print(f"[PASS] {label}")
    except Exception as e:
        print(f"[FAIL] {label}: {type(e).__name__}: {e}")
        raise


def test_fsl_is_excluded_from_unified_merge():
    names = [item[0] for item in UNIFIED_V2_ONLY]
    assert "fsl" not in names
    assert "fsl_unified" not in [item[1] for item in UNIFIED_V2_ONLY]
    spec = next(item for item in MODULES if item["module"] == "fsl_case_property")
    assert spec["excluded_from_unified"] is True
    assert classify_module(source_count=2006, observed_count=2006, collapse=False, excluded=True, unified_count=2006) == "INTENTIONALLY_EXCLUDED"
    assert classify_module(source_count=10, observed_count=9, collapse=False, excluded=True) == "UNRESOLVED"
    assert classify_module(source_count=10, observed_count=10, collapse=False, excluded=True, defect=True) == "INTENTIONALLY_EXCLUDED"


def test_ps_normalization_and_exact_district_match():
    for raw in ("Banjara Hills PS", "Banjara Hills P.S.", "  Banjara   Hills Police Station ", "banjara hills"):
        assert normalize_ps_name(raw) == "banjara hills", raw
    assert normalize_district("  Hyderabad  ") == "hyderabad"
    index = build_hierarchy_index([
        {"ps_name": "Central PS", "dist_name": "Hyderabad", "ps_code": "HYD01"},
        {"ps_name": "Central P.S.", "dist_name": "Warangal", "ps_code": "WGL01"},
    ])
    assert classify_station("Central", "Hyderabad", index) == ("exact", "HYD01")
    assert classify_station("CENTRAL PS", " warangal ", index) == ("exact", "WGL01")
    assert classify_station("Central", "Nizamabad", index) == ("unresolved", None)
    assert classify_station(None, "Hyderabad", index) == ("unresolved", None)
    assert classify_station("PS", "Hyderabad", index) == ("unresolved", None)
    ambiguous = build_hierarchy_index([
        {"ps_name": "Central PS", "dist_name": "Hyderabad", "ps_code": "A"},
        {"ps_name": "Central Police Station", "district_name": "Hyderabad", "ps_code": "B"},
    ])
    assert classify_station("Central", "Hyderabad", ambiguous) == ("ambiguous", None)


def _station_records():
    return hierarchy_records([
        {"ps_name": "Zahirabad Town PS", "dist_name": "Sangareddy", "ps_code": "SNG01", "dist_code": "2200"},
        {"ps_name": "Kazipet PS", "dist_name": "Warangal", "ps_code": "WGL01", "dist_code": "2100"},
        {"ps_name": "Khanapur PS", "dist_name": "Nirmal", "ps_code": "NRM01", "dist_code": "2300"},
        {"ps_name": "Khanapur PS", "dist_name": "Warangal", "ps_code": "WGL02", "dist_code": "2100"},
        {"ps_name": "RPS WARANGAL", "dist_name": "SRP GRP Secunderabad", "ps_code": "GRP01", "dist_code": "9001"},
        {"ps_name": "RPS SECUNDERABAD", "dist_name": "SRP GRP Secunderabad", "ps_code": "GRP02", "dist_code": "9001"},
        {"ps_name": "Nizamabad I Town PS", "dist_name": "Nizamabad CP", "ps_code": "NZB01", "dist_code": "2400"},
        {"ps_name": "Nizamabad VI Town PS", "dist_name": "Nizamabad CP", "ps_code": "NZB06", "dist_code": "2400"},
        {"ps_name": "Bhadrachalam Town PS", "dist_name": "Bhadradri Kothagudem", "ps_code": "BDH01", "dist_code": "2067500"},
    ])


def test_residual_station_rules():
    records = _station_records()

    status, code, unit, resolution = match_v1_station("Zaheerabad Town", "Sangareddy", records)
    assert status == "district_unit_only", status
    assert code is None
    assert unit == "2200"
    assert resolution["raw_ps_name"] == "Zaheerabad Town"
    assert resolution["hierarchy_ps_code"] is None

    # The district is known, so a near station name does not become a ps_code.
    status, code, unit, resolution = match_v1_station("Bhadrachalam TN", "Bhadradri Kothagudem", records)
    assert status == "district_unit_only", status
    assert code is None
    assert unit == "2067500"
    assert resolution["raw_ps_name"] == "Bhadrachalam TN"

    status, code, unit, resolution = match_v1_station("Kazipet", "GRP Secunderabad", records)
    assert status == "station_name_unique", status
    assert code == "WGL01"
    assert unit == "2100"
    assert resolution["raw_district"] == "GRP Secunderabad"
    assert resolution["hierarchy_district"] == "Warangal"

    status, code, unit, resolution = match_v1_station("Khanapur", "GRP Secunderabad", records)
    assert status == "station_name_not_unique", status
    assert code is None and unit is None
    assert resolution["raw_ps_name"] == "Khanapur"
    assert resolution["raw_district"] == "GRP Secunderabad"

    status, code, unit, resolution = match_v1_station("Warangal", "GRP Secunderabad", records)
    assert status == "fuzzy", status
    assert code == "GRP01"
    assert unit == "9001"
    assert resolution["raw_ps_name"] == "Warangal"
    assert resolution["raw_district"] == "GRP Secunderabad"
    assert resolution["hierarchy_ps_name"] == "RPS WARANGAL"

    status, code, unit, _resolution_body = match_v1_station("Nizamabad I TN", "Nizamabad", records)
    assert status == "unresolved", status
    assert code is None and unit is None

    status, code, unit, resolution = match_v1_station("Kazipet", "Warangal", records)
    assert status == "exact", status
    assert code == "WGL01"
    assert resolution["raw_district"] == "Warangal"
    assert resolution["unit_code"] == "2100"


def test_derived_ps_resolution_survives_source_replay():
    stored = {"attach_path": "a", "ps_resolution": _resolution("2011004")}
    replayed = preserve_derived_crime_json(stored, {"attach_path": "a"})
    assert replayed["ps_resolution"] == stored["ps_resolution"]
    assert replayed["attach_path"] == "a"
    changed = preserve_derived_crime_json(stored, {"attach_path": "b"})
    assert changed["attach_path"] == "b"
    assert changed["ps_resolution"]["ps_code"] == "2011004"
    assert preserve_derived_crime_json({"attach_path": "a"}, {"attach_path": "a"}) == {"attach_path": "a"}
    assert preserve_derived_crime_json(None, None) is None
    assert preserve_derived_crime_json(stored, None) == {"ps_resolution": stored["ps_resolution"]}


def test_blank_station_names_do_not_match_each_other():
    index = build_hierarchy_index([
        {"ps_name": "PS", "dist_name": "Hyderabad", "ps_code": "NOPE"},
        {"ps_name": None, "dist_name": "Hyderabad", "ps_code": "NOPE2"},
    ])
    assert index == {}
    assert classify_station("Police Station", "Hyderabad", index) == ("unresolved", None)


def test_v1_person_and_accused_stay_separate():
    payload = {
        "accused_id": "999",
        "person_code": "FIR1P1",
        "fir_reg_num": "FIR1",
        "accused_name": "Ravi",
        "father_name": "Kumar",
        "mobile_1": None,
        "gender": None,
        "age": None,
    }
    from etl3.merger.current_state import apply_field_map

    assert field_maps.PERSONS["V1"]["unified_pk"](payload) == "FIR1P1"
    assert field_maps.PERSONS["V1"]["unified_pk"](payload) != payload["accused_id"]
    person = apply_field_map(payload, field_maps.PERSONS["V1"]["map"])
    accused = apply_field_map(payload, field_maps.ACCUSED["V1"]["map"])
    assert person["full_name"] == "Ravi"
    assert person["phone_number"] is None
    assert "accused_id" not in person
    assert logical_key(payload) != payload["accused_id"]
    assert accused["accused_status"] is None
    partial = {"fir_reg_num": "FIR1", "person_code": None, "accused_name": None}
    assert field_maps.PERSONS["V1"]["unified_pk"](partial) is None
    assert apply_field_map(partial, field_maps.PERSONS["V1"]["map"])["full_name"] is None


def test_v2_accused_person_cases():
    known = {"P1"}
    linked = accused_relation_v2({"crime_id": "C1", "person_id": "P1", "accused_id": "A1"}, known)
    assert linked["person_id"] == "P1" and linked["missing_person"] is False
    null_row = accused_relation_v2({"crime_id": "C1", "person_id": None, "accused_id": "A2"}, known)
    assert null_row["person_id"] is None and null_row["unlinked_person_flag"] is True
    missing = accused_relation_v2({"crime_id": "C1", "person_id": "MISSING", "accused_id": "A3"}, known)
    assert missing["person_id"] is None and missing["missing_person"] is True
    second = accused_relation_v2({"crime_id": "C2", "person_id": "P1", "accused_id": "A4"}, known)
    assert second["person_id"] == "P1"


def test_multiple_accused_can_share_one_person():
    conn = connections.get_unified_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO crimes_unified
                    (crime_id, source_system, source_record_id, current_source_run_id, current_as_of)
                VALUES ('__p5acc_c', 'V2', '__p5acc_c', 'phase5', now())
                """
            )
            cur.execute(
                """
                INSERT INTO persons_unified
                    (person_id, source_system, source_record_id, full_name, current_source_run_id, current_as_of)
                VALUES ('__p5acc_p', 'V2', '__p5acc_p', 'Shared Person', 'phase5', now())
                """
            )
            cur.execute(
                """
                INSERT INTO accused_unified
                    (accused_id, source_system, source_record_id, crime_id, person_id,
                     current_source_run_id, current_as_of)
                VALUES
                    ('__p5acc_a1', 'V2', '__p5acc_a1', '__p5acc_c', '__p5acc_p', 'phase5', now()),
                    ('__p5acc_a2', 'V2', '__p5acc_a2', '__p5acc_c', '__p5acc_p', 'phase5', now())
                """
            )
            cur.execute(
                "UPDATE persons_unified SET full_name='Renamed' WHERE person_id='__p5acc_p'"
            )
            cur.execute(
                """
                SELECT count(*), min(a.person_id), min(p.full_name)
                FROM accused_unified a
                JOIN persons_unified p ON p.person_id=a.person_id
                WHERE a.accused_id IN ('__p5acc_a1', '__p5acc_a2')
                """
            )
            count, person_id, name = cur.fetchone()
        assert count == 2 and person_id == "__p5acc_p" and name == "Renamed"
    finally:
        conn.rollback()
        conn.close()


def test_live_relationship_counts_match_the_rules():
    conn = connections.get_unified_connection(readonly=True)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*) FROM accused_unified a
                WHERE a.source_system='V2' AND a.person_id IS NOT NULL
                  AND NOT EXISTS (SELECT 1 FROM persons_unified p WHERE p.person_id=a.person_id)
                """
            )
            assert cur.fetchone()[0] == 0
            cur.execute(
                """
                SELECT count(*) FROM accused_unified
                WHERE source_system='V2' AND person_id IS NULL
                """
            )
            assert cur.fetchone()[0] > 0
            cur.execute(
                """
                SELECT count(*) FROM persons_unified p
                WHERE NOT EXISTS (SELECT 1 FROM accused_unified a WHERE a.person_id=p.person_id)
                """
            )
            assert cur.fetchone()[0] > 0
            cur.execute("SELECT count(*) FROM fsl_unified")
            fsl_rows = cur.fetchone()[0]
        assert fsl_rows >= 0
    finally:
        conn.close()


def main():
    check("FSL case property is excluded from the unified merge", test_fsl_is_excluded_from_unified_merge)
    check("PS normalization and district match", test_ps_normalization_and_exact_district_match)
    check("residual V1 station rules", test_residual_station_rules)
    check("derived PS provenance survives a source replay", test_derived_ps_resolution_survives_source_replay)
    check("blank station names do not match", test_blank_station_names_do_not_match_each_other)
    check("V1 person and accused stay separate", test_v1_person_and_accused_stay_separate)
    check("V2 accused person cases", test_v2_accused_person_cases)
    check("multiple accused can share one person", test_multiple_accused_can_share_one_person)
    check("live relationship counts", test_live_relationship_counts_match_the_rules)


if __name__ == "__main__":
    main()
