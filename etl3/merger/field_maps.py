"""
Declarative field mapping: for each unified entity and source_system, how to
turn a *_source.payload dict (the raw row as captured in Phase 3) into a row
for the corresponding *_unified table.

A map value is either:
  - a string: the payload key to read directly
  - a callable(payload) -> value: for anything that needs light transformation

Nothing here invents data. Every field either comes straight from the
source payload or is left NULL (nullable in ETL3_UNIFIED_SCHEMA.sql) when
the source genuinely doesn't carry an equivalent -- never guessed, never
defaulted to a non-null placeholder.
"""


def _get(key):
    return lambda payload: payload.get(key)


def _v1_crimes_residual(payload):
    """V1 fir fields with no dedicated crimes_unified column -- preserved in
    additional_json_data rather than discarded (payload preservation, even
    at the current-state layer)."""
    keep = {"attach_path", "dms_file_name"}
    return {k: payload.get(k) for k in keep if payload.get(k) is not None} or None


def _v2_crimes_residual(payload):
    keep = {"fir_type", "crime_type", "class_classification", "fir_copy"}
    extra = {k: payload.get(k) for k in keep if payload.get(k) is not None}
    existing = payload.get("additional_json_data") or {}
    if isinstance(existing, dict):
        extra.update(existing)
    return extra or None


CRIMES = {
    "V1": {
        "unified_pk": _get("fir_reg_num"),
        "map": {
            "fir_reg_num": "fir_reg_num",
            "fir_num": "fir_no",
            "fir_date": "reg_dt",
            "occurrence_year": "reg_year",
            "unit_district": "unit",
            "ps_name": "ps_name",
            "acts_sections": "section_of_law",
            "brief_facts": "fir_contents",
            "case_status": "fir_status",
            "additional_json_data": _v1_crimes_residual,
        },
        "modified_field": "updated_at",
        "created_field": "created_at",
    },
    "V2": {
        "unified_pk": _get("crime_id"),
        "map": {
            "fir_reg_num": "fir_reg_num",
            "fir_num": "fir_num",
            "fir_date": "fir_date",
            "ps_code": "ps_code",
            "acts_sections": "acts_sections",
            "brief_facts": "brief_facts",
            "case_status": "case_status",
            "major_head": "major_head",
            "minor_head": "minor_head",
            "io_name": "io_name",
            "io_rank": "io_rank",
            "additional_json_data": _v2_crimes_residual,
        },
        "modified_field": "date_modified",
        "created_field": "date_created",
    },
}

# persons_unified -- V2 directly from persons_source; V1 derived from
# accused_details (arrests_source payload), keyed on person_code (already a
# stable, FIR-prefixed identifier -- confirmed earlier this project, not
# subject to the natural-key churn that affects cctns_accused's accused_id).
PERSONS = {
    "V1": {  # payload is an accused_details row (arrests_source)
        "unified_pk": _get("person_code"),
        "map": {
            "full_name": "accused_name",
            "relative_name": "father_name",
            "gender": "gender",
            "age": "age",
            "occupation": None,  # accused_details has no occupation column (dossier does, but persons here are keyed by person_code which only accused_details carries)
            "caste": "caste",
            "nationality": "nationality",
            "present_address_text": "accused_present_address",
            "permanent_address_text": "accused_permanent_address",
            "phone_number": "mobile_1",
        },
        "modified_field": "updated_at",
        "created_field": "created_at",
    },
    "V2": {
        "unified_pk": _get("person_id"),
        "map": {
            "full_name": "full_name",
            "alias": "alias",
            "relative_name": "relative_name",
            "gender": "gender",
            "date_of_birth": "date_of_birth",
            "age": "age",
            "occupation": "occupation",
            "caste": "caste",
            "nationality": "nationality",
            "present_address_text": lambda p: p.get("present_house_no") or p.get("present_locality_village") or p.get("present_district"),
            "permanent_address_text": lambda p: p.get("permanent_house_no") or p.get("permanent_locality_village") or p.get("permanent_district"),
            "phone_number": "phone_number",
            "email_id": "email_id",
        },
        "modified_field": "date_modified",
        "created_field": "date_created",
    },
}

# accused_unified -- V1 from the full dossier (accused_source); V2 directly
# from accused_source.
ACCUSED = {
    "V1": {
        # unified_pk intentionally NOT accused_id (churns) -- see
        # current_state.py's V1 grouping logic, which computes the logical
        # key and passes the WINNING row's own accused_id here only as
        # source_record_id (kept for traceability), not as the grouping key.
        "map": {
            "accused_status": "fir_status",
            "is_arrested": "is_arrested",
            "arrested_date": "arrest_surrender_dt",
            "modus_operandi": "modus_operandi",
        },
        "modified_field": "updated_at",
        "created_field": "created_at",
    },
    "V2": {
        "unified_pk": _get("accused_id"),
        "map": {
            "accused_code": "accused_code",
            "accused_status": "accused_status",
            "is_ccl": "is_ccl",
        },
        "modified_field": "date_modified",
        "created_field": "date_created",
    },
}

# arrests_unified -- V1 from accused_details (arrests_source); V2 directly.
ARRESTS = {
    "V1": {
        "unified_pk": _get("accused_id"),
        "map": {
            "is_arrested": "is_arrested",
            "arrested_date": "arrest_surrender_dt",
        },
        "modified_field": "updated_at",
        "created_field": "created_at",
    },
    "V2": {
        "unified_pk": _get("id"),
        "map": {
            "is_arrested": "is_arrested",
            "arrested_date": "arrested_date",
        },
        "modified_field": "date_modified",
        "created_field": "date_created",
    },
}

# chargesheets_unified -- V1 from court; V2 from chargesheets AND
# charge_sheet_updates (distinguished by source_table, both land here).
CHARGESHEETS = {
    "V1": {
        "unified_pk": _get("court_id"),
        "map": {
            "chargesheet_date": "chargesheet_dt",
            "court_name": "court_name",
            "court_case_num": "court_case_num",
            "court_disposal_date": "court_disposal_dt",
            "court_disposal_type": "court_disposal_type",
            "court_remarks": "court_remarks",
        },
        "modified_field": "updated_at",
        "created_field": "created_at",
    },
    "V2:chargesheets": {
        "unified_pk": _get("id"),
        "map": {
            "chargesheet_no": "chargesheet_no",
            "chargesheet_date": "chargesheet_date",
            "court_name": "court_name",
            "acts_sections": lambda p: p.get("acts_sections"),
            "accused_person_ids": "accused_person_ids",
        },
        "modified_field": "date_modified",
        "created_field": "date_created",
    },
    "V2:charge_sheet_updates": {
        "unified_pk": _get("id"),
        "map": {
            "chargesheet_no": "charge_sheet_no",
            "chargesheet_date": "charge_sheet_date",
            "court_case_num": "taken_on_file_court_case_no",
            "court_disposal_type": "charge_sheet_status",
        },
        "modified_field": "date_modified",
        "created_field": "date_created",
    },
}

# seizures_unified -- V1 derived from the accused dossier's embedded drug
# fields (one synthesized seizure per dossier row); V2 directly from
# mo_seizures.
SEIZURES = {
    "V1": {
        "unified_pk": _get("accused_id"),
        "map": {
            "drug_type": "drug_type",
            "description": "drug_desc",
            "quantity": lambda p: p.get("weight_gm") or p.get("weight_kg"),
            "quantity_unit": lambda p: "gm" if p.get("weight_gm") else ("kg" if p.get("weight_kg") else None),
            "status": "drug_status",
            "pos_address1": "area_operation",
        },
        "modified_field": "updated_at",
        "created_field": "created_at",
    },
    "V2": {
        "unified_pk": _get("mo_seizure_id"),
        "map": {
            "drug_type": "sub_type",
            "description": "description",
            "pos_address1": "pos_address1",
            "pos_latitude": "pos_latitude",
            "pos_longitude": "pos_longitude",
        },
        "modified_field": "date_modified",
        "created_field": "date_created",
    },
}

# V2-only entities, straightforward.
PROPERTIES = {
    "V2": {"unified_pk": _get("property_id"), "map": {}, "modified_field": "date_modified", "created_field": "date_created"},
}
FSL = {
    "V2": {"unified_pk": _get("case_property_id"), "map": {}, "modified_field": "date_modified", "created_field": "date_created"},
}
DISPOSAL = {
    "V2": {"unified_pk": _get("id"), "map": {}, "modified_field": "date_modified", "created_field": "date_created"},
}
INTERROGATION = {
    "V2": {
        "unified_pk": _get("interrogation_report_id"),
        # person_id deliberately NOT in map -- it's an FK (interrogation_unified
        # references persons_unified) and must be validated against what
        # actually exists before being set, not copied blindly (V2 has 11
        # known IR rows pointing at a missing person, confirmed earlier this
        # project). Handled via extra_fields_fn in the Phase 4 driver.
        "map": {},
        "modified_field": "date_modified", "created_field": "date_created",
    },
}
HIERARCHY = {
    "V2": {"unified_pk": _get("ps_code"), "map": {}, "modified_field": "date_modified", "created_field": "date_created"},
}
