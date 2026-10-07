"""Enrichment driver.

Reads *_source observations already stored in dopams_cctns. The geography
lookup reads geo_reference and geo_countries from V2 through the read-only
source connection. It does not update *_unified CCTNS columns.
"""
import json

from etl3.db import connections
from etl3.enrichment import kb as drug_kb
from etl3.enrichment.address import load_geo_kb
from etl3.enrichment.accused_facts import (
    apply_v1_code_type_and_ccl_numbering,
    build_roster_for_prompt,
    enrich_existing_accused,
    map_v1_dossier_identity,
    _compose_address,
)
from etl3.enrichment.ai import (
    AIExtractionError,
    OllamaAccusedClient,
    OllamaDrugClient,
    ai_settings,
    extract_accused_with_retry,
    extract_with_retry,
)
from etl3.enrichment.persist import as_json, record_ai_attempt, replace_provenance, upsert_rows
from etl3.enrichment.project import (
    ai_drug_rows,
    arrest_row,
    crime_row,
    drug_rows_from_sources,
    person_row,
    _hash,
)
from etl3.merger import current_state as cs

DRUG_COLUMNS = [
    "extraction_id", "crime_id", "source_system", "provenance", "source_record_id",
    "raw_drug_name", "primary_drug_name", "drug_form", "drug_category",
    "raw_quantity", "raw_unit", "weight_g", "weight_kg", "volume_ml", "volume_l",
    "count_total", "seizure_worth", "is_commercial", "confidence_score", "kb_match_tier",
    "input_hash",
]

ACCUSED_COLUMNS = [
    "accused_id", "source_system", "accused_category", "role_in_crime",
    "accused_type", "accused_type_method", "accused_code", "person_id",
    "age", "alias_name", "gender", "occupation", "address", "phone_numbers",
    "status", "is_ccl", "key_details", "field_sources", "input_hash",
]


def _payload(value):
    if isinstance(value, str):
        return json.loads(value)
    return value or {}


def _latest(conn, table, source_system, source_table=None):
    rows = cs.fetch_latest_by_record_id(conn, table, source_system, source_table)
    return [(record_id, _payload(payload)) for record_id, _run, _c, _m, payload, _i in rows]


def _pairs(conn, sql):
    with conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()


def _crime_enrichment(conn, run_id):
    rows = []
    known = {row[0] for row in _pairs(conn, "SELECT crime_id FROM crimes_unified")}
    for source_system in ("V1", "V2"):
        for record_id, payload in _latest(conn, "crimes_source", source_system):
            crime_id = payload.get("fir_reg_num") if source_system == "V1" else payload.get("crime_id")
            if crime_id not in known:
                continue
            rows.append(crime_row(source_system, crime_id, payload))
    return upsert_rows(
        conn, "crime_enrichment", "crime_id", rows,
        ["crime_id", "source_system", "class_classification", "classification_method",
         "case_status_raw", "case_status_normalized", "case_status_method", "input_hash"],
        run_id, "crime_enrichment",
    )


def _person_enrichment(conn, run_id, geo_kb=None):
    people = _pairs(
        conn,
        """
        SELECT person_id, source_system, source_record_id,
               full_name, alias, relative_name, nationality
        FROM persons_unified
        """,
    )
    v2_payloads = {
        record_id: payload
        for record_id, payload in _latest(conn, "persons_source", "V2")
    }
    rows = []
    for person_id, source_system, record_id, full_name, alias, relative_name, nationality in people:
        if source_system == "V2":
            payload = dict(v2_payloads.get(record_id) or {})
            if not payload.get("full_name"):
                payload["full_name"] = full_name
            if not payload.get("alias"):
                payload["alias"] = alias
            if not payload.get("relative_name"):
                payload["relative_name"] = relative_name
            if not payload.get("nationality"):
                payload["nationality"] = nationality
        else:
            payload = {
                "full_name": full_name,
                "alias": alias,
                "relative_name": relative_name,
                "nationality": nationality,
            }
        row = person_row(source_system, person_id, payload, geo_kb=geo_kb)
        if row:
            rows.append(row)
    return upsert_rows(
        conn, "person_enrichment", "person_id", rows,
        ["person_id", "source_system", "domicile_classification", "domicile_method",
         "surname", "relation_type", "gender_source", "address_resolution",
         "raw_full_name", "cleaned_full_name", "cleaned_given_name",
         "cleaned_alias", "cleaned_relative_name", "input_hash"],
        run_id, "person_enrichment",
    )


def _accused_status_index(conn):
    index = {}
    for record_id, payload in _latest(conn, "accused_source", "V2"):
        crime_id = payload.get("crime_id")
        seq = payload.get("seq_num")
        if crime_id is not None and seq is not None:
            index[(str(crime_id), str(seq))] = payload.get("accused_status")
    return index


def _arrest_enrichment(conn, run_id):
    unified = {
        (source_system, source_record_id): arrest_id
        for arrest_id, source_system, source_record_id in _pairs(
            conn, "SELECT arrest_id, source_system, source_record_id FROM arrests_unified"
        )
    }
    status_index = _accused_status_index(conn)
    rows = []
    for record_id, payload in _latest(conn, "arrests_source", "V2"):
        arrest_id = unified.get(("V2", record_id))
        if not arrest_id:
            continue
        status = status_index.get((str(payload.get("crime_id")), str(payload.get("accused_seq_no"))))
        row = arrest_row("V2", arrest_id, payload, accused_status=status)
        if row:
            rows.append(row)
    for record_id, payload in _latest(conn, "arrests_source", "V1"):
        arrest_id = unified.get(("V1", record_id))
        if not arrest_id:
            continue
        row = arrest_row("V1", arrest_id, payload, accused_status=payload.get("fir_status"))
        if row:
            rows.append(row)
    return upsert_rows(
        conn, "arrest_enrichment", "arrest_id", rows,
        ["arrest_id", "source_system", "is_41a_crpc", "is_41a_explain_submitted",
         "date_of_issue_41a", "accused_type", "arrest_flag_method", "input_hash"],
        run_id, "arrest_enrichment",
    )


def _person_payloads(conn):
    """Person identity keyed by (source_system, person_id).

    V2 uses persons_source. V1 has no separate persons API table; identity is
    on the accused dossier / accused_details and already on persons_unified.
    """
    out = {}
    for source_system in ("V1", "V2"):
        for record_id, payload in _latest(conn, "persons_source", source_system):
            person_id = payload.get("person_id") or record_id
            if person_id:
                out[(source_system, person_id)] = payload

    # V1 persons_source is empty; load persons_unified for linked identity.
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT person_id, full_name, alias, age, gender, occupation,
                   phone_number, present_address_text, permanent_address_text
            FROM persons_unified
            WHERE source_system = 'V1'
            """
        )
        for row in cur.fetchall():
            person_id = row[0]
            if not person_id or ("V1", person_id) in out:
                continue
            out[("V1", person_id)] = {
                "person_id": person_id,
                "full_name": row[1],
                "alias": row[2],
                "age": row[3],
                "gender": row[4],
                "occupation": row[5],
                "phone_number": row[6],
                "present_address_text": row[7],
                "permanent_address_text": row[8],
            }
    return out


def _compose_existing_accused_record(source_system, accused_id, payload, unified_meta, person_payload):
    """One CCTNS accused row for brief-facts enrichment. accused_id is required."""
    person = person_payload or {}
    v1 = map_v1_dossier_identity(payload) if source_system == "V1" else {}
    address = (
        _compose_address(person)
        or _text_or_none(person.get("present_address_text"))
        or v1.get("address")
    )
    return {
        "accused_id": accused_id,
        "source_system": source_system,
        "accused_code": payload.get("accused_code") or unified_meta.get("accused_code"),
        "person_id": unified_meta.get("person_id") or payload.get("person_id"),
        "type": payload.get("type") or payload.get("accused_type"),
        "accused_status": (
            unified_meta.get("accused_status")
            or payload.get("accused_status")
            or payload.get("status")
            or v1.get("accused_status")
        ),
        "is_ccl": (
            unified_meta.get("is_ccl")
            if unified_meta.get("is_ccl") is not None
            else payload.get("is_ccl")
        ),
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
        "address": address,
        "status": (
            unified_meta.get("accused_status")
            or payload.get("accused_status")
            or payload.get("status")
            or v1.get("accused_status")
        ),
        "crime_id": unified_meta.get("crime_id"),
        "person_payload": person,
        "payload": payload,
    }


def _text_or_none(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _load_stored_accused_extractions(conn, accused_ids):
    """Rebuild FIR extraction dicts from stored enrichment so a settled replay keeps them."""
    if not accused_ids:
        return []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT accused_id, accused_code, role_in_crime, key_details, age, alias_name,
                   gender, occupation, address, phone_numbers, status, is_ccl, field_sources
            FROM accused_enrichment
            WHERE accused_id = ANY(%s)
            """,
            (list(accused_ids),),
        )
        stored = cur.fetchall()
    rebuilt = []
    for row in stored:
        (
            accused_id, accused_code, role_in_crime, key_details, age, alias_name,
            gender, occupation, address, phone_numbers, status, is_ccl, field_sources,
        ) = row
        sources = field_sources or {}
        if isinstance(sources, str):
            sources = json.loads(sources)
        item = {"accused_id": accused_id, "accused_code": accused_code, "explicit_ccl": None}
        for field, value in (
            ("role_in_crime", role_in_crime),
            ("key_details", key_details),
            ("age", age),
            ("alias_name", alias_name),
            ("gender", gender),
            ("occupation", occupation),
            ("address", address),
            ("phone_numbers", phone_numbers),
            ("status", status),
        ):
            if sources.get(field) == "LLM_FALLBACK" and value is not None:
                item[field] = value
        if sources.get("is_ccl") == "explicit_ccl" and is_ccl is True:
            item["explicit_ccl"] = True
        if any(k in item for k in (
            "role_in_crime", "key_details", "age", "alias_name", "gender",
            "occupation", "address", "phone_numbers", "status",
        )) or item["explicit_ccl"] is not None:
            rebuilt.append(item)
    return rebuilt


def _accused_enrichment(conn, run_id, accused_client=None, settings=None):
    """Enrich only existing CCTNS accused_id rows.

    Brief-facts AI fills missing fields when enabled. It never creates accused
    from narrative names and never invents a synthetic accused_id.
    """
    settings = settings or ai_settings()
    unified = {}
    for accused_id, source_system, source_record_id, crime_id, person_id, accused_code, accused_status, is_ccl in _pairs(
        conn,
        """
        SELECT accused_id, source_system, source_record_id, crime_id, person_id,
               accused_code, accused_status, is_ccl
        FROM accused_unified
        """,
    ):
        unified[(source_system, source_record_id)] = {
            "accused_id": accused_id,
            "crime_id": crime_id,
            "person_id": person_id,
            "accused_code": accused_code,
            "accused_status": accused_status,
            "is_ccl": is_ccl,
        }

    persons = _person_payloads(conn)
    by_crime = {}
    for source_system in ("V1", "V2"):
        for record_id, payload in _latest(conn, "accused_source", source_system):
            meta = unified.get((source_system, record_id))
            if not meta or not meta.get("accused_id"):
                continue
            person_id = meta.get("person_id") or payload.get("person_id")
            person_payload = persons.get((source_system, person_id)) if person_id else None
            record = _compose_existing_accused_record(
                source_system, meta["accused_id"], payload, meta, person_payload,
            )
            crime_id = record.get("crime_id")
            if not crime_id:
                continue
            by_crime.setdefault(crime_id, []).append(record)

    facts = _brief_facts(conn)
    ai_on = accused_client is not None or settings.get("enabled")
    if ai_on and accused_client is None:
        accused_client = OllamaAccusedClient(
            settings["host"], settings["model"], settings["timeout"],
        )

    rows = []
    ai_stats = {
        "processed": 0, "success": 0, "empty": 0, "failed": 0,
        "skipped": 0, "no_accused": 0,
    }
    for crime_id, records in by_crime.items():
        extractions = []
        fact_entry = facts.get(crime_id)
        fir_text = fact_entry[1] if fact_entry else None

        # V1: derive A-codes from FIR and age-based Accused/CCL before AI roster.
        if records and (records[0].get("source_system") == "V1"):
            records = apply_v1_code_type_and_ccl_numbering(records, fir_text)

        if ai_on and fact_entry and accused_client is not None:
            _source_system, text = fact_entry
            roster = build_roster_for_prompt(records)
            allowed_ids = [r["accused_id"] for r in records]
            allowed_codes = [r.get("accused_code") for r in records if r.get("accused_code")]
            digest = _hash({"kind": "accused", "brief_facts": text, "roster": roster})
            if _ai_already_settled(conn, crime_id, digest):
                ai_stats["skipped"] += 1
                extractions = _load_stored_accused_extractions(conn, allowed_ids)
            else:
                ai_stats["processed"] += 1
                try:
                    parsed, attempts = extract_accused_with_retry(
                        accused_client, text, roster, allowed_ids, allowed_codes,
                        max_retries=settings.get("max_retries", 1),
                    )
                except AIExtractionError as exc:
                    record_ai_attempt(
                        conn, crime_id, digest, settings.get("model"),
                        exc.status, 1, str(exc),
                    )
                    ai_stats["failed"] += 1
                    extractions = _load_stored_accused_extractions(conn, allowed_ids)
                else:
                    extractions = parsed.get("accused") or []
                    status = "empty" if not extractions else "success"
                    record_ai_attempt(
                        conn, crime_id, digest, settings.get("model"),
                        status, attempts, None,
                    )
                    if extractions:
                        ai_stats["success"] += 1
                    else:
                        ai_stats["empty"] += 1

        merged = enrich_existing_accused(records, extractions, fir_text=fir_text)
        rows.extend(merged["rows"])

    for crime_id in facts:
        if crime_id not in by_crime:
            ai_stats["no_accused"] += 1

    stats = upsert_rows(
        conn, "accused_enrichment", "accused_id", rows, ACCUSED_COLUMNS,
        run_id, "accused_enrichment",
    )
    stats["ai"] = ai_stats
    return stats


def _chargesheet_enrichment(conn, run_id):
    unified = {
        (source_system, source_module, source_record_id): charge_sheet_id
        for charge_sheet_id, source_system, source_module, source_record_id in _pairs(
            conn,
            """
            SELECT charge_sheet_id, source_system, source_module, source_record_id
            FROM chargesheets_unified
            """,
        )
    }
    rows = []
    specs = (
        ("V2", "chargesheets", "chargesheet_no"),
        ("V2", "charge_sheet_updates", "charge_sheet_no"),
        ("V1", "court", None),
    )
    for source_system, module, _ignored in specs:
        for record_id, payload in _latest(conn, "chargesheets_source", source_system, module):
            charge_sheet_id = unified.get((source_system, module, record_id))
            if not charge_sheet_id:
                continue
            nbw = payload.get("accused_requested_for_nbw")
            body = {
                "charge_sheet_id": charge_sheet_id,
                "source_system": source_system,
                "taken_on_file_date": payload.get("taken_on_file_date"),
                "taken_on_file_case_type": payload.get("taken_on_file_case_type"),
                "taken_on_file_court_case_no": payload.get("taken_on_file_court_case_no"),
                "accused_requested_for_nbw": as_json(nbw) if nbw is not None else None,
            }
            digest_body = dict(body)
            digest_body["accused_requested_for_nbw"] = nbw
            body["input_hash"] = _hash(digest_body)
            if all(body[k] is None for k in (
                "taken_on_file_date", "taken_on_file_case_type",
                "taken_on_file_court_case_no", "accused_requested_for_nbw",
            )):
                continue
            rows.append(body)
    return upsert_rows(
        conn, "chargesheet_enrichment", "charge_sheet_id", rows,
        ["charge_sheet_id", "source_system", "taken_on_file_date", "taken_on_file_case_type",
         "taken_on_file_court_case_no", "accused_requested_for_nbw", "input_hash"],
        run_id, "chargesheet_enrichment",
    )


def _hierarchy_enrichment(conn, run_id):
    known = {row[0] for row in _pairs(conn, "SELECT ps_code FROM hierarchy_unified")}
    rows = []
    for record_id, payload in _latest(conn, "hierarchy_source", "V2"):
        ps_code = payload.get("ps_code") or record_id
        if ps_code not in known:
            continue
        body = {
            "ps_code": ps_code,
            "sub_zone_code": payload.get("sub_zone_code"),
            "sub_zone_name": payload.get("sub_zone_name"),
            "adg_code": payload.get("adg_code"),
            "adg_name": payload.get("adg_name"),
        }
        if all(body[k] is None for k in ("sub_zone_code", "sub_zone_name", "adg_code", "adg_name")):
            continue
        body["input_hash"] = _hash(body)
        rows.append(body)
    return upsert_rows(
        conn, "hierarchy_enrichment", "ps_code", rows,
        ["ps_code", "sub_zone_code", "sub_zone_name", "adg_code", "adg_name", "input_hash"],
        run_id, "hierarchy_enrichment",
    )


def _property_enrichment(conn, run_id):
    known = {row[0] for row in _pairs(conn, "SELECT property_id FROM properties_unified")}
    rows = []
    for record_id, payload in _latest(conn, "properties_source", "V2"):
        if record_id not in known:
            continue
        body = {
            "property_id": record_id,
            "property_status": payload.get("property_status"),
            "nature": payload.get("nature"),
            "place_of_recovery": payload.get("place_of_recovery"),
            "category": payload.get("category"),
            "estimate_value": payload.get("estimate_value"),
            "recovered_value": payload.get("recovered_value"),
        }
        body["input_hash"] = _hash(body)
        rows.append(body)
    return upsert_rows(
        conn, "property_enrichment", "property_id", rows,
        ["property_id", "property_status", "nature", "place_of_recovery", "category",
         "estimate_value", "recovered_value", "input_hash"],
        run_id, "property_enrichment",
    )


def _fsl_enrichment(conn, run_id):
    rows = []
    for record_id, payload in _latest(conn, "fsl_source", "V2"):
        body = {
            "case_property_id": payload.get("case_property_id") or record_id,
            "crime_id": payload.get("crime_id"),
            "mo_id": payload.get("mo_id"),
            "status": payload.get("status"),
            "fsl_no": payload.get("fsl_no"),
            "opinion": payload.get("opinion"),
            "report_received": payload.get("report_received"),
            "date_disposal": payload.get("date_disposal"),
            "details_disposal": payload.get("details_disposal"),
            "place_disposal": payload.get("place_disposal"),
        }
        body["input_hash"] = _hash(body)
        rows.append(body)
    return upsert_rows(
        conn, "fsl_enrichment", "case_property_id", rows,
        ["case_property_id", "crime_id", "mo_id", "status", "fsl_no", "opinion",
         "report_received", "date_disposal", "details_disposal", "place_disposal", "input_hash"],
        run_id, "fsl_enrichment",
    )


def _disposal_enrichment(conn, run_id):
    known = {row[0] for row in _pairs(conn, "SELECT disposal_id FROM disposal_unified")}
    rows = []
    for record_id, payload in _latest(conn, "disposal_source", "V2"):
        disposal_id = str(payload.get("id") or record_id)
        if disposal_id not in known:
            continue
        body = {
            "disposal_id": disposal_id,
            "disposal_type": payload.get("disposal_type"),
            "disposal": payload.get("disposal"),
            "case_status": payload.get("case_status"),
            "disposed_at": payload.get("disposed_at"),
        }
        body["input_hash"] = _hash(body)
        rows.append(body)
    return upsert_rows(
        conn, "disposal_enrichment", "disposal_id", rows,
        ["disposal_id", "disposal_type", "disposal", "case_status", "disposed_at", "input_hash"],
        run_id, "disposal_enrichment",
    )


def _drug_enrichment(conn, run_id, kb_items):
    known = {row[0] for row in _pairs(conn, "SELECT crime_id FROM crimes_unified")}
    v1 = _latest(conn, "accused_source", "V1")
    props = _latest(conn, "properties_source", "V2")
    built = drug_rows_from_sources(v1, props, kb_items, known)
    v1_rows = [row for row in built if row["provenance"] == "v1_dossier"]
    v2_rows = [row for row in built if row["provenance"] == "v2_property"]
    return {
        "v1_dossier": replace_provenance(conn, "v1_dossier", v1_rows, DRUG_COLUMNS, run_id),
        "v2_property": replace_provenance(conn, "v2_property", v2_rows, DRUG_COLUMNS, run_id),
    }


def _brief_facts(conn):
    facts = {}
    for source_system, column in (("V2", "brief_facts"), ("V1", "fir_contents")):
        for record_id, payload in _latest(conn, "crimes_source", source_system):
            crime_id = payload.get("crime_id") if source_system == "V2" else payload.get("fir_reg_num")
            text = payload.get(column)
            if crime_id and text and len(str(text).strip()) > 20:
                facts[crime_id] = (source_system, str(text))
    return facts


def _ai_already_settled(conn, crime_id, digest):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT status, attempt_count FROM ai_extraction_attempts
            WHERE crime_id = %s AND input_hash = %s
            """,
            (crime_id, digest),
        )
        rows = cur.fetchall()
    statuses = {status: count for status, count in rows}
    if "success" in statuses or "empty" in statuses or "invalid" in statuses:
        return True
    timeout_tries = statuses.get("timeout", 0) + statuses.get("error", 0)
    return timeout_tries >= 3


def run_ai_extraction(conn, run_id, kb_items, client=None, settings=None):
    """Optional AI pass. Disabled unless ETL3_AI_ENABLED=1 or a client is injected.

    A failed call records an attempt and does not delete existing drug rows.
    """
    settings = settings or ai_settings()
    if client is None and not settings["enabled"]:
        return {"status": "disabled"}
    if client is None:
        client = OllamaDrugClient(settings["host"], settings["model"], settings["timeout"])
    known = {row[0] for row in _pairs(conn, "SELECT crime_id FROM crimes_unified")}
    facts = _brief_facts(conn)
    limit = settings.get("limit") or 0
    processed = success = empty = failed = skipped = 0
    for crime_id, (source_system, text) in facts.items():
        if crime_id not in known:
            continue
        if limit and processed >= limit:
            break
        digest = _hash({"brief_facts": text})
        if _ai_already_settled(conn, crime_id, digest):
            skipped += 1
            continue
        processed += 1
        try:
            parsed, attempts = extract_with_retry(client, text, max_retries=settings.get("max_retries", 1))
        except AIExtractionError as exc:
            record_ai_attempt(conn, crime_id, digest, settings.get("model"), exc.status, 1, str(exc))
            failed += 1
            continue
        drugs = parsed["drugs"]
        status = "empty" if not drugs else "success"
        record_ai_attempt(conn, crime_id, digest, settings.get("model"), status, attempts, None)
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM drug_extractions WHERE provenance = 'etl3_ai' AND crime_id = %s",
                (crime_id,),
            )
        if drugs:
            rows = ai_drug_rows(crime_id, source_system, drugs, kb_items)
            upsert_rows(conn, "drug_extractions", "extraction_id", rows, DRUG_COLUMNS, run_id, "drug_extraction")
            success += 1
        else:
            empty += 1
    return {"status": "ran", "processed": processed, "success": success, "empty": empty,
            "failed": failed, "skipped": skipped}


def run_enrichment(conn, run_id, ai_client=None, accused_client=None):
    """Full enrichment from current observations. Safe to call again.

    Each family is committed on its own so a crash mid-run can restart
    without duplicating change_log rows for families that already finished.
    ``ai_client`` is the optional drug extractor. ``accused_client`` is the
    optional known-accused brief-facts enricher. Neither invents accused rows.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO enrichment_run_log (run_id, status) VALUES (%s, 'running')
            ON CONFLICT (run_id) DO UPDATE SET status = 'running', finished_at = NULL
            """,
            (run_id,),
        )
    conn.commit()
    kb_items = drug_kb.load_kb()
    v2 = connections.get_v2_source_connection()
    try:
        geo_kb = load_geo_kb(v2)
    finally:
        v2.close()
    stats = {}
    try:
        steps = (
            ("crimes", lambda: _crime_enrichment(conn, run_id)),
            ("persons", lambda: _person_enrichment(conn, run_id, geo_kb)),
            ("arrests", lambda: _arrest_enrichment(conn, run_id)),
            ("accused", lambda: _accused_enrichment(
                conn, run_id, accused_client=accused_client,
            )),
            ("chargesheets", lambda: _chargesheet_enrichment(conn, run_id)),
            ("hierarchy", lambda: _hierarchy_enrichment(conn, run_id)),
            ("properties", lambda: _property_enrichment(conn, run_id)),
            ("fsl", lambda: _fsl_enrichment(conn, run_id)),
            ("disposal", lambda: _disposal_enrichment(conn, run_id)),
            ("drugs", lambda: _drug_enrichment(conn, run_id, kb_items)),
            ("ai", lambda: run_ai_extraction(conn, run_id, kb_items, client=ai_client)),
        )
        for name, fn in steps:
            stats[name] = fn()
            conn.commit()
    except Exception as exc:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE enrichment_run_log
                SET finished_at = now(), status = 'failed', stats = %s
                WHERE run_id = %s
                """,
                (json.dumps({"error": str(exc), "completed": stats}), run_id),
            )
        conn.commit()
        raise
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE enrichment_run_log
            SET finished_at = now(), status = 'success', stats = %s
            WHERE run_id = %s
            """,
            (json.dumps(stats), run_id),
        )
    conn.commit()
    return stats
