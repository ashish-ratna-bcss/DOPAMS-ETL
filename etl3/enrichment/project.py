"""Build enrichment rows from source-observation payloads.

Nothing here writes, and nothing here fills a value the payload does not
support. A missing drug name stays missing. A missing section stays unclassified.
"""
import hashlib
import json
from datetime import date, datetime
from decimal import Decimal

from etl3.enrichment import kb as drug_kb
from etl3.enrichment.address import (
    resolve_name_country,
    resolve_nationality_label,
    resolve_person_address,
)
from etl3.enrichment.names import clean_person_names
from etl3.enrichment.rules import (
    INDIAN_STATES,
    apply_commercial_flags,
    classify_domicile,
    classify_sections,
    normalize_case_status,
    parse_accused_status,
    resolve_drug_category,
    standardize_measurement,
)


def _hash(payload) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _num(value):
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _text(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def crime_row(source_system, crime_id, payload):
    raw_status = payload.get("case_status") if source_system == "V2" else payload.get("fir_status")
    normalized, status_method = normalize_case_status(raw_status)
    source_class = _text(payload.get("class_classification")) if source_system == "V2" else None
    if source_class:
        classification = source_class
        class_method = "source_column"
    else:
        sections = payload.get("acts_sections") if source_system == "V2" else payload.get("section_of_law")
        classification = classify_sections(sections)
        class_method = "deterministic_sections" if classification else "non_recoverable"
    body = {
        "crime_id": crime_id,
        "source_system": source_system,
        "class_classification": classification,
        "classification_method": class_method,
        "case_status_raw": _text(raw_status),
        "case_status_normalized": normalized,
        "case_status_method": status_method,
    }
    body["input_hash"] = _hash(body)
    return body


def _slot_geo(address, slot):
    data = (address or {}).get(slot) or {}
    return data.get("state"), data.get("country"), data.get("district")


def person_row(source_system, person_id, payload, geo_kb=None):
    """Build person enrichment: domicile, resolved nationality, KB address.

    Rules:
    1. Foreign country in ``nationality`` field OR embedded in the name
       (``Name (Nigerian)``, ``Name, Nigeria``, …) → domicile ``international``
       and ``nationality`` column set to that country.
    2. No foreign country found → if present/permanent address maps to Indian
       geography via KB (or nationality is Indian/India), set ``nationality``
       to India and domicile ``native state`` / ``inter state`` from KB state.
    3. Pure accused name + Indian-only address → same as (2) using
       ``address_resolution`` (village/mandal/district/state from KB).
    4. Else keep V2 stored domicile when present; otherwise non_recoverable.

    ``persons_unified.nationality`` is never overwritten; the resolved value
    is stored on ``person_enrichment.nationality``.
    """
    address = resolve_person_address(payload, geo_kb)
    perm_state = _text(payload.get("permanent_state_ut"))
    perm_country = _text(payload.get("permanent_country"))
    present_state = _text(payload.get("present_state_ut"))
    present_country = _text(payload.get("present_country"))

    addr_perm_state, addr_perm_country, _ = _slot_geo(address, "permanent")
    addr_pres_state, addr_pres_country, _ = _slot_geo(address, "present")
    if not perm_state and addr_perm_state:
        perm_state = addr_perm_state
    if not present_state and addr_pres_state:
        present_state = addr_pres_state
    if not perm_country and addr_perm_country:
        perm_country = addr_perm_country
    if not present_country and addr_pres_country:
        present_country = addr_pres_country

    name_country = resolve_name_country(
        payload.get("full_name") or payload.get("name"), geo_kb,
    )
    field_nationality = resolve_nationality_label(payload.get("nationality"), geo_kb)

    matched_state = perm_state or present_state
    indian_address = False
    if matched_state:
        state_key = matched_state.lower()
        kb_indian = geo_kb is not None and geo_kb.canon_state(matched_state)
        if kb_indian or state_key == "telangana" or state_key in INDIAN_STATES:
            indian_address = True
    if (perm_country or present_country or "").lower() == "india":
        indian_address = True
    if address:
        for slot in ("permanent", "present"):
            slot_data = address.get(slot) or {}
            if (slot_data.get("country") or "").lower() == "india":
                indian_address = True
            st = slot_data.get("state")
            if st and (
                (geo_kb is not None and geo_kb.canon_state(st))
                or st.lower() == "telangana"
                or st.lower() in INDIAN_STATES
            ):
                indian_address = True

    foreign_name = bool(name_country) and name_country.lower() != "india"
    foreign_field = bool(field_nationality) and field_nationality.lower() != "india"

    stored = _text(payload.get("domicile_classification"))
    enriched_nationality = None
    domicile = None
    method = "non_recoverable"

    if foreign_name:
        enriched_nationality = name_country
        domicile, method = "international", "name_country"
    elif foreign_field:
        enriched_nationality = field_nationality
        domicile, method = "international", "nationality"
    else:
        # No foreign country in name or nationality field.
        if field_nationality == "India" or (name_country and name_country.lower() == "india"):
            enriched_nationality = "India"
        elif indian_address:
            enriched_nationality = "India"
        elif field_nationality:
            enriched_nationality = field_nationality

        if indian_address or enriched_nationality == "India":
            if indian_address and not (perm_country or present_country):
                perm_country = "India"
            domicile = classify_domicile(
                perm_state, perm_country or "India",
                present_state, present_country,
                enriched_nationality or "India",
            )
            if domicile:
                method = "deterministic"
            elif stored:
                domicile, method = stored, "source_column"
            else:
                domicile, method = None, "non_recoverable"
        elif stored:
            domicile, method = stored, "source_column"
        else:
            domicile = classify_domicile(
                perm_state, perm_country, present_state, present_country,
                enriched_nationality or field_nationality,
            )
            method = "deterministic" if domicile else "non_recoverable"

    surname_source = _text(payload.get("surname"))
    relation = _text(payload.get("relation_type"))
    gender_source = _text(payload.get("gender_source"))
    names = clean_person_names(
        payload.get("full_name"),
        given_name=payload.get("name"),
        surname=surname_source,
        alias=payload.get("alias"),
        relative_name=payload.get("relative_name"),
        relation_type=relation,
    )
    if (
        domicile is None and enriched_nationality is None
        and surname_source is None and relation is None
        and gender_source is None and address is None and names is None
    ):
        return None
    cleaned_surname = names["surname"] if names else surname_source
    cleaned_relation = names["relation_type"] if names else relation
    body = {
        "person_id": person_id,
        "source_system": source_system,
        "domicile_classification": domicile,
        "domicile_method": method,
        "nationality": enriched_nationality,
        "surname": cleaned_surname[:255] if cleaned_surname else None,
        "relation_type": cleaned_relation[:50] if cleaned_relation else None,
        "gender_source": gender_source[:30] if gender_source else None,
        "raw_full_name": None if names is None else names["raw_full_name"],
        "cleaned_full_name": None if names is None else names["cleaned_full_name"],
        "cleaned_given_name": None if names is None else names["cleaned_given_name"],
        "cleaned_alias": None if names is None or not names["cleaned_alias"] else names["cleaned_alias"][:255],
        "cleaned_relative_name": None if names is None else names["cleaned_relative_name"],
    }
    if address is not None:
        body["address_resolution"] = address
    body["input_hash"] = _hash(body)
    return body


def _boolish(value):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("y", "yes", "true", "1", "t"):
        return True
    if text in ("n", "no", "false", "0", "f"):
        return False
    return None


def arrest_row(source_system, arrest_id, payload, accused_status=None):
    parsed = parse_accused_status(accused_status)
    is_41a = _boolish(payload.get("is_41a_crpc"))
    explain = _boolish(payload.get("is_41a_explain_submitted"))
    issued = _text(payload.get("date_of_issue_41a"))
    method = "source_column"
    # is_41a_pending is parsed by the old ETL and then discarded. It is not stored.
    if is_41a is not True and parsed.get("is_41a_crpc"):
        is_41a = True
        method = "status_text"
    if not issued and parsed.get("date_of_issue_41a"):
        issued = parsed["date_of_issue_41a"]
        method = "status_text"
    accused_type = _text(payload.get("accused_type"))
    if is_41a is None and explain is None and issued is None and accused_type is None:
        return None
    body = {
        "arrest_id": arrest_id,
        "source_system": source_system,
        "is_41a_crpc": is_41a,
        "is_41a_explain_submitted": explain,
        "date_of_issue_41a": issued,
        "accused_type": accused_type,
        "arrest_flag_method": method,
    }
    body["input_hash"] = _hash(body)
    return body


def accused_row(source_system, accused_id, payload, person_payload=None,
                extraction=None, fir_text=None):
    """Enrich one existing CCTNS accused_id. Never invents an accused.

    Person fields come from the linked persons observation when present.
    FIR extraction fills only missing fields. Narrative-only names are ignored.
    V1 also derives accused_code / Accused|CCL from dossier age and FIR text.
    """
    from .accused_facts import accused_row_from_existing

    if not _text(accused_id):
        return None
    return accused_row_from_existing(
        source_system, accused_id, payload,
        person_payload=person_payload,
        extraction=extraction,
        fir_text=fir_text,
    )


def _drug_row(extraction_id, crime_id, source_system, provenance, source_record_id,
              raw_name, quantity, unit, worth, kb_items, drug_form=None, source_sentence="",
              drug_kb_obj=None, preferred_primary=None):
    """Build one drug_extractions row.

    Precedence for primary_drug_name:
      1. KB exact / safe normalization of source-supported raw_name
      2. preferred_primary only when already source-validated by caller
         and KB did not resolve (never overrides a real KB hit)
      3. unresolved (NULL) — never invent from a generic raw term

    Generic/ignored raw terms return kb_match_tier=ignored_generic with
    primary_drug_name NULL (row omitted when both name and quantity empty).
    """
    if raw_name is None and _num(quantity) in (None, 0.0):
        return None
    if drug_kb_obj is not None:
        standard, tier = drug_kb.resolve_primary_name(
            raw_name, drug_kb_obj.items, drug_kb=drug_kb_obj,
        ) if raw_name else (None, "none")
    else:
        standard, tier = drug_kb.resolve_primary_name(raw_name, kb_items) if raw_name else (None, "none")

    if tier == drug_kb.TIER_IGNORED_GENERIC:
        # Do not persist a specific invented primary for generic descriptors.
        # Drop the row entirely — quantity alone without a drug identity is
        # not a valid etl3_ai enrichment signal.
        return None

    primary = standard
    if primary is None and preferred_primary:
        # preferred_primary must already be source-supported by the caller.
        if drug_kb_obj is not None:
            pref_std, pref_tier = drug_kb.resolve_primary_name(
                preferred_primary, drug_kb_obj.items, drug_kb=drug_kb_obj,
            )
            if pref_tier == drug_kb.TIER_IGNORED_GENERIC:
                primary = None
            else:
                primary = pref_std or _text(preferred_primary)
                if pref_std:
                    tier = pref_tier
        else:
            primary = _text(preferred_primary)

    # Final guard: ignored primary (e.g. alcohol) drops the row.
    if drug_kb_obj is not None and primary is not None and drug_kb_obj.is_ignored(primary):
        return None

    measured = standardize_measurement(
        quantity, unit, drug_form=drug_form, raw_drug_name=raw_name,
        seizure_worth=worth or 0, source_sentence=source_sentence,
    )
    qty = _num(quantity)
    body = {
        "extraction_id": extraction_id,
        "crime_id": crime_id,
        "source_system": source_system,
        "provenance": provenance,
        "source_record_id": source_record_id,
        "raw_drug_name": _text(raw_name),
        "primary_drug_name": primary,
        "drug_form": measured["drug_form"],
        "drug_category": resolve_drug_category(primary),
        "raw_quantity": qty,
        "raw_unit": measured["raw_unit"],
        "weight_g": measured["weight_g"],
        "weight_kg": measured["weight_kg"],
        "volume_ml": measured["volume_ml"],
        "volume_l": measured["volume_l"],
        "count_total": measured["count_total"],
        "seizure_worth": _num(worth),
        "is_commercial": None,
        "confidence_score": None,
        "kb_match_tier": tier,
    }
    return body


def drug_rows_from_sources(v1_accused_payloads, v2_property_payloads, kb_items, known_crime_ids,
                           drug_kb_obj=None):
    """v1 items: (source_record_id, payload). v2 items: (property_id, payload)."""
    rows = []
    for record_id, payload in v1_accused_payloads:
        crime_id = _text(payload.get("fir_reg_num"))
        if not crime_id or crime_id not in known_crime_ids:
            continue
        name = _text(payload.get("drug_type")) or _text(payload.get("drug_desc"))
        gm = _num(payload.get("weight_gm"))
        kg = _num(payload.get("weight_kg"))
        if gm not in (None, 0.0):
            qty, unit = gm, "gm"
        elif kg not in (None, 0.0):
            qty, unit = kg, "kg"
        else:
            continue
        row = _drug_row(
            f"V1:dossier:{record_id}", crime_id, "V1", "v1_dossier", record_id,
            name, qty, unit, None, kb_items, drug_kb_obj=drug_kb_obj,
        )
        if row:
            rows.append(row)
    for property_id, payload in v2_property_payloads:
        crime_id = _text(payload.get("crime_id"))
        if not crime_id or crime_id not in known_crime_ids:
            continue
        extra = payload.get("additional_details") or {}
        if isinstance(extra, str):
            try:
                extra = json.loads(extra)
            except json.JSONDecodeError:
                extra = {}
        if not isinstance(extra, dict):
            extra = {}
        weight = _num(extra.get("WEIGHT"))
        if weight in (None, 0.0):
            continue
        name = _text(extra.get("SPECIFICATION_OF_DRUG")) or _text(extra.get("DRUG_PARTICULARS"))
        unit = _text(extra.get("WEIGHT_IN")) or "unknown"
        worth = _num(payload.get("estimate_value"))
        row = _drug_row(
            f"V2:property:{property_id}", crime_id, "V2", "v2_property", property_id,
            name, weight, unit, worth, kb_items, drug_kb_obj=drug_kb_obj,
        )
        if row:
            rows.append(row)
    apply_commercial_flags(rows)
    for row in rows:
        row["input_hash"] = _measurement_hash(row)
    return rows


class AiDrugRows(list):
    """List of drug rows plus ``rejections`` audit records."""

    def __init__(self, rows, rejections=None):
        super().__init__(rows)
        self.rejections = list(rejections or [])


def ai_drug_rows(crime_id, source_system, parsed_drugs, kb_items, drug_kb_obj=None,
                 source_text=None):
    """Turn a validated model payload into drug rows.

    Each AI drug must pass ignore-list + source-evidence validation before KB
    normalization. Empty / fully rejected input yields no rows.

    Returns AiDrugRows (list subclass) with ``.rejections`` for audit.
    """
    from etl3.enrichment.ai import validate_ai_drug_item

    rows = []
    rejections = []
    ordinal = 0
    for item in parsed_drugs or []:
        checked = validate_ai_drug_item(item, source_text, drug_kb_obj=drug_kb_obj)
        if checked.get("reject_reason"):
            rejections.append({
                "raw_drug_name": checked.get("raw_drug_name"),
                "reason": checked["reject_reason"],
            })
            continue
        row = _drug_row(
            f"{source_system}:ai:{crime_id}:{ordinal}",
            crime_id, source_system, "etl3_ai", crime_id,
            checked.get("raw_drug_name"), checked.get("raw_quantity"),
            checked.get("raw_unit"), checked.get("seizure_worth"), kb_items,
            drug_form=checked.get("drug_form"),
            source_sentence=checked.get("source_sentence") or "",
            drug_kb_obj=drug_kb_obj,
            preferred_primary=checked.get("primary_drug_name"),
        )
        if row is None:
            rejections.append({
                "raw_drug_name": checked.get("raw_drug_name"),
                "reason": "GENERIC_IGNORED",
            })
            continue
        if item.get("is_commercial") or checked.get("is_commercial"):
            row["is_commercial"] = True
        conf = _num(checked.get("confidence_score"))
        if conf is not None and conf >= 1:
            conf = round(conf / 100.0, 4)
        row["confidence_score"] = conf
        rows.append(row)
        ordinal += 1
    apply_commercial_flags(rows)
    for row in rows:
        row["input_hash"] = _measurement_hash(row)
    return AiDrugRows(rows, rejections)



def _measurement_hash(row):
    """Hash source measurements. KB labels are not part of the hash."""
    source_fields = {
        k: row.get(k) for k in (
            "extraction_id", "crime_id", "source_system", "provenance",
            "source_record_id", "raw_drug_name", "raw_quantity", "raw_unit",
            "weight_g", "weight_kg", "volume_ml", "volume_l", "count_total",
            "seizure_worth", "is_commercial", "drug_form",
        )
    }
    return _hash(source_fields)


def json_ready(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value
