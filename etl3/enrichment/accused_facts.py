"""Enrich existing CCTNS accused from brief facts.

Invariant: every output row has a CCTNS accused_id. Narrative-only names
never become accused. Existing DB person/accused values are never overwritten.
Missing evidence stays null. No persons_unified row is invented here.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Optional

from .rules import classify_accused_type, resolve_is_ccl

SOURCE_DB = "DB"
SOURCE_LLM = "LLM_FALLBACK"
SOURCE_CATEGORY = "source_category"
SOURCE_AGE = "age_rule"
SOURCE_EXPLICIT_CCL = "explicit_ccl"
SOURCE_FIR = "FIR_DERIVED"
SOURCE_CCL_NUMBER = "ccl_numbering"

PERSON_FIELDS = (
    "age",
    "alias_name",
    "gender",
    "occupation",
    "address",
    "phone_numbers",
)

_A_CODE_RE = re.compile(r"\bA\s*[-.]?\s*(\d+)\b", re.IGNORECASE)
_CCL_CODE_RE = re.compile(r"\bCCL\s*[-.]?\s*(\d+)\b", re.IGNORECASE)


def _text(value):
    if value is None:
        return None
    text = str(value).replace("\xa0", " ").strip()
    return text or None


def _present(value) -> bool:
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    return True


def _hash(payload) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _norm_code(value) -> Optional[str]:
    text = _text(value)
    if not text:
        return None
    ccl = _CCL_CODE_RE.search(text)
    if ccl:
        return f"CCL {int(ccl.group(1))}"
    match = _A_CODE_RE.search(text)
    if match:
        return f"A-{int(match.group(1))}"
    return text.upper()


def _parse_age(value):
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def classify_v1_accused_category(age):
    """V1 CCTNS type from that accused's age only. None when age unknown."""
    age_int = _parse_age(age)
    if age_int is None:
        return None
    if age_int < 18:
        return "CCL"
    return "Accused"


def derive_accused_code_from_fir(full_name, fir_text):
    """Return A-n only when the FIR explicitly ties that code to this name.

    Looks for an A-code in the short span immediately before the name
    (e.g. ``A-1 Ravi Kumar``). Does not invent a code and does not let one
    accused's window swallow the next A-code.
    """
    name = _text(full_name)
    text = _text(fir_text)
    if not name or not text:
        return None

    name_lower = name.lower()
    text_lower = text.lower()
    candidates = [name_lower]
    tokens = [t for t in re.split(r"\s+", name_lower) if len(t) > 2]
    if len(tokens) >= 2:
        candidates.append(" ".join(tokens))

    best = None
    for candidate in candidates:
        start_at = 0
        while True:
            pos = text_lower.find(candidate, start_at)
            if pos < 0:
                break
            prefix = text[max(0, pos - 24):pos]
            matches = list(_A_CODE_RE.finditer(prefix))
            if matches:
                code_num = int(matches[-1].group(1))
                # Prefer the leftmost name hit with a preceding code.
                if best is None or pos < best[0]:
                    best = (pos, f"A-{code_num}")
            start_at = pos + 1
    return best[1] if best else None


def resolve_v1_accused_code(db_code, full_name, fir_text):
    """Prefer CCTNS V1 code; else FIR A-code; else unresolved."""
    existing = _norm_code(db_code)
    if existing:
        return existing, SOURCE_DB
    derived = derive_accused_code_from_fir(full_name, fir_text)
    if derived:
        return derived, SOURCE_FIR
    return None, None


def apply_v1_code_type_and_ccl_numbering(records, fir_text=None):
    """Fill V1 accused_code / category and renumber CCL codes for one crime.

    Adults keep A-n from DB or FIR. Minors become type CCL and receive
    sequential ``CCL 1``, ``CCL 2``, ... regardless of the FIR A-label.
    Age must be present for each accused; unknowns stay unresolved.
    """
    prepared = []
    for index, raw in enumerate(existing_accused_only(records)):
        row = dict(raw)
        row["_order"] = index
        code, code_source = resolve_v1_accused_code(
            row.get("accused_code"),
            row.get("full_name") or row.get("name"),
            fir_text,
        )
        row["accused_code"] = code
        row["_accused_code_source"] = code_source

        age = _parse_age(row.get("age"))
        if age is not None:
            row["age"] = age
        category = classify_v1_accused_category(age)
        if category:
            row["type"] = category
            row["_category_source"] = SOURCE_AGE
            row["is_ccl"] = category == "CCL"
            row["_is_ccl_source"] = SOURCE_AGE
        prepared.append(row)

    ccl_rows = [row for row in prepared if row.get("type") == "CCL"]

    def _ccl_sort_key(row):
        code = row.get("accused_code") or ""
        match = _A_CODE_RE.search(code)
        if match:
            return (0, int(match.group(1)), row["_order"])
        return (1, row["_order"])

    for seq, row in enumerate(sorted(ccl_rows, key=_ccl_sort_key), start=1):
        row["accused_code"] = f"CCL {seq}"
        row["_accused_code_source"] = SOURCE_CCL_NUMBER

    for row in prepared:
        row.pop("_order", None)
    return prepared


def map_v1_dossier_identity(payload):
    """Normalize V1 cctns_accused / accused_details field names to enrichment keys."""
    if not payload:
        return {}
    phone = (
        payload.get("mobile_1")
        or payload.get("telephone_residence")
        or payload.get("phone_number")
        or payload.get("phone_numbers")
    )
    address = (
        _text(payload.get("present_address"))
        or _text(payload.get("accused_present_address"))
        or _text(payload.get("permanent_address"))
        or _text(payload.get("accused_permanent_address"))
        or _text(payload.get("address"))
    )
    return {
        "full_name": _text(payload.get("accused_name") or payload.get("full_name") or payload.get("name")),
        "alias_name": _text(payload.get("alias_name") or payload.get("alias")),
        "age": _parse_age(payload.get("age")),
        "gender": _text(payload.get("gender")),
        "occupation": _text(
            payload.get("accused_occupation")
            or payload.get("occupation")
        ),
        "phone_numbers": _text(phone),
        "address": address,
        "accused_status": _text(payload.get("fir_status") or payload.get("accused_status") or payload.get("status")),
    }


def existing_accused_only(records):
    """Keep CCTNS accused rows that already have accused_id. Drop the rest."""
    kept = []
    for row in records or []:
        accused_id = _text(row.get("accused_id"))
        if not accused_id:
            continue
        kept.append(dict(row, accused_id=accused_id))
    return kept


def reject_narrative_only_names(extracted_names, existing_records):
    """Names found only in FIR text that are not in the CCTNS roster."""
    variants = set()
    for row in existing_accused_only(existing_records):
        for key in ("full_name", "alias_name", "name"):
            value = _text(row.get(key))
            if value:
                variants.add(value.lower())
    rejected = []
    for raw in extracted_names or []:
        name = _text(raw)
        if not name:
            continue
        if name.lower() not in variants:
            rejected.append(name)
    return rejected


def build_roster_for_prompt(existing_records):
    """Human-readable roster: codes, ids, names, and PRESENT/MISSING markers."""
    lines = []
    for row in existing_accused_only(existing_records):
        code = _text(row.get("accused_code")) or ""
        accused_id = row["accused_id"]
        name = _text(row.get("full_name")) or _text(row.get("name")) or ""
        markers = []
        for field in PERSON_FIELDS + ("status",):
            label = "PRESENT" if _present(row.get(field)) else "MISSING"
            markers.append(f"{field}={label}")
        if _present(row.get("is_ccl")):
            markers.append("is_ccl=PRESENT")
        else:
            markers.append("is_ccl=MISSING")
        lines.append(
            f"{code or '(no-code)'}: accused_id={accused_id}; name={name or '(none)'}; "
            + ", ".join(markers)
        )
    return "\n".join(lines)


def _index_extractions(extractions, allowed_ids, allowed_codes):
    """Map AI items onto existing accused only. Drop unknown ids/codes."""
    by_id = {}
    by_code = {}
    rejected = []
    for item in extractions or []:
        if not isinstance(item, dict):
            rejected.append({"reason": "not_object", "item": item})
            continue
        accused_id = _text(item.get("accused_id"))
        code = _norm_code(item.get("accused_code"))
        if accused_id and accused_id in allowed_ids:
            by_id[accused_id] = item
            continue
        if code and code in allowed_codes:
            by_code[code] = item
            continue
        rejected.append({
            "reason": "not_in_cctns_roster",
            "accused_id": accused_id,
            "accused_code": code,
        })
    return by_id, by_code, rejected


def _fill_missing(current, extracted, field, sources):
    """Keep DB value when present. Take LLM value only when DB is empty."""
    if _present(current):
        sources[field] = SOURCE_DB
        return current
    candidate = extracted.get(field) if extracted else None
    if field == "age" and candidate is not None and str(candidate).strip() != "":
        try:
            candidate = int(candidate)
        except (TypeError, ValueError):
            return None
    candidate = candidate if field == "age" else _text(candidate)
    if _present(candidate):
        sources[field] = SOURCE_LLM
        return candidate
    return None


def enrich_existing_accused(existing_records, extractions=None, fir_text=None):
    """Build enrichment rows for CCTNS accused only.

    ``existing_records`` must already be the crime's CCTNS accused list.
    ``extractions`` is the AI list keyed loosely by accused_id / accused_code.
    Empty extractions still produce category-only rows from CCTNS type.
    For V1, ``fir_text`` drives missing accused_code and age-based Accused/CCL.
    """
    records = existing_accused_only(existing_records)
    if not records:
        return {
            "rows": [],
            "rejected_extractions": list(extractions or []),
            "narrative_only_rejected": [],
        }

    # Index extractions against the pre-renumber roster (A-codes still match).
    pre_ids = {row["accused_id"] for row in records}
    pre_codes = {
        _norm_code(row.get("accused_code"))
        for row in records
        if _norm_code(row.get("accused_code"))
    }
    by_id, by_code, rejected = _index_extractions(extractions, pre_ids, pre_codes)

    def _extraction_for(row):
        accused_id = row["accused_id"]
        code = _norm_code(row.get("accused_code"))
        found = by_id.get(accused_id) or (by_code.get(code) if code else None)
        if found:
            return found
        for item in extractions or []:
            if isinstance(item, dict) and _text(item.get("accused_id")) == accused_id:
                return item
        return {}

    # Fill missing ages from FIR first so V1 CCL classification sees them.
    for row in records:
        extracted = _extraction_for(row)
        if not _present(row.get("age")) and extracted:
            age = _parse_age(extracted.get("age"))
            if age is not None:
                row["age"] = age
                row["_age_from_fir"] = True

    if any((_text(row.get("source_system")) or "V2") == "V1" for row in records):
        records = apply_v1_code_type_and_ccl_numbering(records, fir_text)

    rows = []
    for row in records:
        accused_id = row["accused_id"]
        extracted = _extraction_for(row)
        sources = {}

        category = _text(row.get("type")) or _text(row.get("accused_category")) or _text(row.get("accused_type_db"))
        category_source = row.get("_category_source") or (SOURCE_DB if category else None)
        if category and category_source:
            sources["accused_category"] = category_source

        values = {
            "accused_id": accused_id,
            "source_system": _text(row.get("source_system")) or "V2",
            "accused_code": _text(row.get("accused_code")),
            "person_id": _text(row.get("person_id")),
            "accused_category": category,
        }
        code_source = row.get("_accused_code_source")
        if values["accused_code"] and code_source:
            sources["accused_code"] = code_source
        elif values["accused_code"]:
            sources["accused_code"] = SOURCE_DB
        if values["person_id"]:
            sources["person_id"] = SOURCE_DB

        for field in PERSON_FIELDS:
            if field == "age" and row.get("_age_from_fir") and _present(row.get("age")):
                values["age"] = row.get("age")
                sources["age"] = SOURCE_LLM
                continue
            values[field] = _fill_missing(row.get(field), extracted, field, sources)

        db_status = _text(row.get("status") or row.get("accused_status"))
        if _present(db_status):
            values["status"] = db_status
            sources["status"] = SOURCE_DB
        else:
            llm_status = _text(extracted.get("status")) if extracted else None
            if llm_status and llm_status.lower() in ("arrested", "absconding"):
                values["status"] = llm_status.lower()
                sources["status"] = SOURCE_LLM
            else:
                values["status"] = None

        role = None
        if _present(extracted.get("role_in_crime")):
            role = _text(extracted.get("role_in_crime"))
            sources["role_in_crime"] = SOURCE_LLM
        values["role_in_crime"] = role

        key_details = None
        if _present(extracted.get("key_details")):
            key_details = _text(extracted.get("key_details"))
            sources["key_details"] = SOURCE_LLM
        values["key_details"] = key_details

        accused_type = None
        accused_type_method = SOURCE_CATEGORY
        if role:
            classified = classify_accused_type(
                role + ((" " + key_details) if key_details else "")
            )
            if classified:
                accused_type = classified
                accused_type_method = SOURCE_LLM
                sources["accused_type"] = SOURCE_LLM
        values["accused_type"] = accused_type
        values["accused_type_method"] = accused_type_method

        db_ccl = row.get("is_ccl")
        ccl_source = row.get("_is_ccl_source")
        if db_ccl is True or db_ccl is False:
            values["is_ccl"] = bool(db_ccl)
            sources["is_ccl"] = ccl_source or SOURCE_DB
        else:
            explicit = extracted.get("explicit_ccl")
            if explicit is not True and explicit is not False:
                explicit = None
            values["is_ccl"] = resolve_is_ccl(values.get("age"), explicit)
            if values["is_ccl"] is not None:
                if values.get("age") is not None and sources.get("age") in (SOURCE_DB, SOURCE_LLM):
                    sources["is_ccl"] = SOURCE_AGE
                elif explicit is True or explicit is False:
                    sources["is_ccl"] = SOURCE_EXPLICIT_CCL

        has_signal = category is not None or values.get("accused_code") is not None or any(
            values.get(k) is not None
            for k in (
                "role_in_crime", "accused_type", "age", "alias_name", "gender",
                "occupation", "address", "phone_numbers", "status", "is_ccl",
                "key_details",
            )
        )
        if not has_signal:
            continue

        values["field_sources"] = sources
        digest_body = {k: values[k] for k in values if k != "field_sources"}
        digest_body["field_sources"] = sources
        values["input_hash"] = _hash(digest_body)
        rows.append(values)

    return {
        "rows": rows,
        "rejected_extractions": rejected,
        "narrative_only_rejected": [],
    }


def accused_row_from_existing(source_system, accused_id, payload, person_payload=None,
                              extraction=None, fir_text=None):
    """Project one existing accused into an enrichment row.

    ``payload`` is the CCTNS accused observation. ``person_payload`` is the
    linked persons observation when person_id is set. ``extraction`` is the
    optional FIR extraction for this accused only.
    """
    if not _text(accused_id):
        return None

    person = person_payload or {}
    v1 = map_v1_dossier_identity(payload) if source_system == "V1" else {}
    record = {
        "accused_id": accused_id,
        "source_system": source_system,
        "accused_code": payload.get("accused_code"),
        "person_id": payload.get("person_id"),
        "type": payload.get("type") or payload.get("accused_type"),
        "accused_status": (
            payload.get("accused_status") or payload.get("status") or v1.get("accused_status")
        ),
        "is_ccl": payload.get("is_ccl"),
        "full_name": (
            person.get("full_name") or v1.get("full_name")
            or payload.get("full_name") or payload.get("name")
        ),
        "alias_name": (
            person.get("alias") or person.get("alias_name")
            or v1.get("alias_name") or payload.get("alias")
        ),
        "age": (
            person.get("age") if person.get("age") is not None
            else v1.get("age") if v1.get("age") is not None
            else payload.get("age")
        ),
        "gender": person.get("gender") or v1.get("gender") or payload.get("gender"),
        "occupation": (
            person.get("occupation") or v1.get("occupation") or payload.get("occupation")
        ),
        "phone_numbers": (
            person.get("phone_number") or person.get("phone_numbers")
            or v1.get("phone_numbers") or payload.get("phone_number")
        ),
        "address": (
            _compose_address(person)
            or _text(person.get("present_address_text"))
            or v1.get("address")
            or _text(payload.get("address"))
        ),
        "status": payload.get("accused_status") or payload.get("status") or v1.get("accused_status"),
    }
    result = enrich_existing_accused(
        [record],
        [extraction] if extraction else [],
        fir_text=fir_text,
    )
    rows = result["rows"]
    return rows[0] if rows else None


def _compose_address(person):
    if not person:
        return None
    parts = []
    for key in (
        "present_house_no", "present_street_road_no", "present_ward_colony",
        "present_locality_village", "present_area_mandal", "present_district",
        "present_state_ut", "present_country",
    ):
        value = _text(person.get(key))
        if value:
            parts.append(value)
    return ", ".join(parts) if parts else None
