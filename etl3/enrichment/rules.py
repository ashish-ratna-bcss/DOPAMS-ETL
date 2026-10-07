"""Deterministic rules ported from the main-branch ETL.

Sources, kept behaviourally equivalent and not imported at runtime
(so ETL-3 never executes V1 or V2 jobs):

- section-wise-case-clarification/process_sections.py
- etl_case_status/case-status.sql
- domicile_classification/domicile_classifier.py
- brief_facts_ai/extractor_drugs.py standardize_units, commercial thresholds
- brief_facts_ai/db.py _resolve_drug_category
- etl-accused/etl_accused.py parse_accused_status
- brief_facts_ai/extractor_accused.py classify_accused_type (role text only)

Brief-facts accused enrichment runs only for existing CCTNS accused_id values.
Narrative-only names are never turned into accused rows.
"""
import re
from typing import Optional

INDIAN_STATES = {
    "andhra pradesh", "arunachal pradesh", "assam", "bihar", "chhattisgarh",
    "goa", "gujarat", "haryana", "himachal pradesh", "jharkhand", "karnataka",
    "kerala", "madhya pradesh", "maharashtra", "manipur", "meghalaya", "mizoram",
    "nagaland", "odisha", "punjab", "rajasthan", "sikkim", "tamil nadu",
    "telangana", "tripura", "uttar pradesh", "uttarakhand", "west bengal",
    "andaman and nicobar islands", "chandigarh",
    "dadra and nagar haveli and daman and diu", "delhi",
    "national capital territory", "jammu and kashmir", "ladakh", "lakshadweep",
    "puducherry",
}

CASE_STATUS_MAP = {
    "PT Cases": "PT",
    "Pending Trial": "PT",
    "UI Cases": "UI",
    "New": "UI",
    "Under Investigation": "UI",
    "Under Trial": "UI",
    "Chargesheet Created": "Chargesheeted",
    "compounded": "Compounded",
}

DRUG_FORM_SOLID = {
    "solid", "dry", "powder", "paste", "resin", "chunk", "crystal", "granule",
    "leaf", "dried", "compressed",
}
DRUG_FORM_LIQUID = {
    "liquid", "syrup", "oil", "solution", "tincture", "extract", "concentrate",
    "fluid", "injection",
}
DRUG_FORM_COUNT = {
    "tablet", "pill", "capsule", "paper", "blot", "seed", "strip", "sachet",
    "ampule", "vial", "bottle", "plant", "tree", "sapling", "seedling",
}

COMMERCIAL_QUANTITY_KG = {
    "ganja": 20.0, "charas": 1.0, "hashish": 1.0, "heroin": 0.250,
    "cocaine": 0.500, "opium": 2.5, "morphine": 0.250, "methamphetamine": 0.050,
    "amphetamine": 0.050, "mdma": 0.050, "ecstasy": 0.050, "ephedrine": 1.0,
    "pseudoephedrine": 1.0, "ketamine": 0.500, "mephedrone": 0.050,
    "codeine": 1.0, "buprenorphine": 0.050, "fentanyl": 0.050,
    "poppy straw": 50.0, "poppy husk": 50.0,
}
COMMERCIAL_QUANTITY_L = {
    "hash oil": 1.0, "hashish oil": 1.0, "hashish/weed oil": 1.0,
    "cannabis oil": 1.0, "liquid opium": 2.5,
}
COMMERCIAL_QUANTITY_COUNT = {
    "lsd": 100.0, "alprazolam": 1000.0, "tramadol": 1000.0, "diazepam": 1000.0,
    "nitrazepam": 1000.0, "clonazepam": 1000.0,
}

_SECTION_PATTERN = re.compile(
    r"\d+[A-Za-z]?(?:-[A-Za-z0-9]+)*(?:\([A-Za-z0-9]+\))*",
    re.IGNORECASE,
)
_CATEGORY_PRIORITY = {"small": 0, "intermediate": 1, "commercial": 2, "cultivation": 3}


def normalize_text(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    stripped = str(text).strip()
    if stripped == "" or stripped.lower() == "default":
        return None
    return stripped.lower()


def classify_domicile(perm_state, perm_country, pres_state, pres_country, nationality=None):
    """Same precedence as domicile_classifier.classify_domicile."""
    perm_st = normalize_text(perm_state)
    perm_co = normalize_text(perm_country)
    pres_st = normalize_text(pres_state)
    pres_co = normalize_text(pres_country)
    nat = normalize_text(nationality)

    effective_country = perm_co if perm_co is not None else pres_co
    if effective_country is None and nat is not None:
        effective_country = nat
    if effective_country is None:
        return None
    if effective_country != "india":
        return "international"
    effective_state = perm_st if perm_st is not None else pres_st
    if effective_state is None:
        return None
    if effective_state == "telangana":
        return "native state"
    if effective_state in INDIAN_STATES:
        return "inter state"
    return None


def normalize_case_status(raw):
    """Exact dictionary from etl_case_status/case-status.sql. Unmapped values stay as stored."""
    if raw is None:
        return None, "source_unchanged"
    text = str(raw)
    if text in CASE_STATUS_MAP:
        return CASE_STATUS_MAP[text], "dictionary"
    return text, "source_unchanged"


def extract_section_entities(text: Optional[str]):
    if not isinstance(text, str) or not text:
        return []
    text = re.sub(r"\br/w\b", " ", text, flags=re.IGNORECASE)
    entities = []
    parts = [part.strip() for part in text.split(",") if part.strip()]
    for part in parts:
        cleaned = re.sub(r"\bNDPSA{1,2}\b", " ", part, flags=re.IGNORECASE)
        for match in _SECTION_PATTERN.finditer(cleaned):
            token = match.group(0)
            token = re.sub(r"-", "", token)
            token = re.sub(r"[()]", "", token)
            entities.append(token.lower())
        for num in re.findall(r"\b(\d+)\b", cleaned):
            entities.append(num)
    seen = set()
    unique = []
    for entity in entities:
        if entity not in seen:
            seen.add(entity)
            unique.append(entity)
    return unique


def _normalize_section_item(item: str) -> str:
    return re.sub(r"[^0-9a-z]", "", str(item).strip().lower())


def _is_numbers_only(section: str) -> bool:
    if not section:
        return False
    normalized = re.sub(r"[^0-9a-z]", "", section.lower())
    return bool(re.match(r"^\d+$", normalized))


def classify_section_item(raw_item: str):
    code = _normalize_section_item(raw_item)
    if not code:
        return None
    if _is_numbers_only(raw_item):
        return "small"
    if code == "8c":
        return "small"
    if "20a" in code:
        return "cultivation"
    if re.match(r"^27[0-9a-z]*$", code):
        return "small"
    letters = re.findall(r"[a-z]", code)
    if not letters:
        return None
    last = letters[-1]
    if last in ("a", "b", "c"):
        main_letter = last
    else:
        if "a" in letters:
            main_letter = "a"
        elif "b" in letters:
            main_letter = "b"
        elif "c" in letters:
            main_letter = "c"
        else:
            return None
    return {"a": "small", "b": "intermediate", "c": "commercial"}[main_letter]


def classify_sections(text: Optional[str]):
    """Highest-priority label, capitalized, or None when nothing classifies."""
    entities = extract_section_entities(text)
    categories = [c for c in (classify_section_item(e) for e in entities) if c]
    if not categories:
        return None
    best = max(categories, key=lambda c: _CATEGORY_PRIORITY[c])
    return best.capitalize()


def resolve_drug_category(primary_name: Optional[str]):
    if not primary_name:
        return None
    name = primary_name.lower().strip()
    if name in ("ganja", "charas", "hashish", "hash oil", "bhang"):
        return "Cannabis"
    if name in (
        "heroin", "opium", "morphine", "codeine", "buprenorphine", "pentazocine",
        "poppy husk", "poppy straw",
    ):
        return "Opioid"
    if name in ("cocaine", "methamphetamine", "amphetamine", "mephedrone", "mdma", "ecstasy"):
        return "Stimulant"
    if name in ("alprazolam", "nitrazepam", "diazepam", "clonazepam", "zolpidem"):
        return "Sedative/Benzodiazepine"
    if name in ("lsd", "ketamine"):
        return "Hallucinogen"
    return "Other"


def _as_float(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def standardize_measurement(raw_quantity, raw_unit, drug_form=None, raw_drug_name=None,
                            seizure_worth=0.0, source_sentence=""):
    """Unit conversion from extractor_drugs.standardize_units, for one row.

    A missing quantity stays missing. A present zero is stored as zero grams,
    matching the old check_has_measurements fallback.
    """
    qty = _as_float(raw_quantity)
    unit = re.sub(r"[^a-z]", "", (raw_unit or "unknown").lower().strip())
    form = re.sub(r"[^a-z]", "", (drug_form or "unknown").lower().strip()) or "unknown"
    name = (raw_drug_name or "").lower().strip()
    worth = _as_float(seizure_worth) or 0.0
    out = {
        "raw_unit": (raw_unit or "")[:50] or None,
        "drug_form": (drug_form or "")[:50] or None,
        "weight_g": None, "weight_kg": None, "volume_ml": None, "volume_l": None,
        "count_total": None,
    }
    if qty is None:
        return out

    if unit in {"g", "gm", "gms", "gram", "grams", "grm", "grms", "gr"}:
        source_l = str(source_sentence or "").lower()
        rs_per_gram = (worth / qty) if qty > 0 else 0.0
        if qty > 0 and worth > 0 and rs_per_gram > 1000 and ("wg" in source_l or "w/g" in source_l):
            out["raw_unit"] = "KGs"
            out["weight_g"] = qty * 1000.0
            out["weight_kg"] = qty
        else:
            out["weight_g"] = qty
            out["weight_kg"] = qty / 1000.0
    elif unit in {"kg", "kgs", "kilogram", "kilograms", "kilo", "kilos"}:
        out["weight_g"] = qty * 1000.0
        out["weight_kg"] = qty
    elif unit in {"mg", "milligram", "milligrams"}:
        out["weight_g"] = qty / 1000.0
        out["weight_kg"] = qty / 1_000_000.0
    elif unit in {"l", "ltr", "ltrs", "liter", "liters", "litre", "litres"}:
        out["volume_l"] = qty
        out["volume_ml"] = qty * 1000.0
    elif unit in {"ml", "milliliter", "milliliters", "millilitre", "millilitres"}:
        out["volume_ml"] = qty
        out["volume_l"] = qty / 1000.0
    elif unit in {
        "no", "nos", "number", "numbers", "piece", "pieces", "pcs",
        "tablet", "tablets", "pill", "pills", "strip", "strips",
        "box", "boxes", "packet", "packets", "sachet", "sachets",
        "blot", "blots", "dot", "dots", "bottle", "bottles",
        "unit", "units", "count", "counts",
        "plant", "plants", "tree", "trees", "sapling", "saplings",
        "seedling", "seedlings", "bush", "bushes",
        "cover", "covers", "polythene", "wrap", "bundle", "bundles",
        "puri", "puris", "katta", "kattas", "pouch", "pouches",
        "vial", "vials", "ampule", "ampules", "ampoule", "ampoules",
        "injection", "injections", "capsule", "capsules",
    }:
        out["count_total"] = qty

    if qty > 0 and out["weight_g"] is None and out["volume_ml"] is None and out["count_total"] is None:
        if form in DRUG_FORM_SOLID:
            out["weight_g"] = qty
            out["weight_kg"] = qty / 1000.0
        elif form in DRUG_FORM_LIQUID:
            out["volume_ml"] = qty
            out["volume_l"] = qty / 1000.0
        else:
            out["count_total"] = qty

    liquid_names = {
        "hash oil", "hashish oil", "weed oil", "cannabis oil",
        "opium solution", "poppy husk solution", "codeine syrup",
        "cough syrup", "phensedyl", "corex",
    }
    if name in liquid_names or any(token in name for token in ("oil", "syrup", "solution")):
        out["drug_form"] = "liquid"

    if all(out[k] is None for k in ("weight_g", "weight_kg", "volume_l", "volume_ml", "count_total")):
        out["weight_g"] = 0.0
        out["weight_kg"] = 0.0

    if not out["drug_form"] or str(out["drug_form"]).lower() in ("unknown", "none", "null"):
        out["drug_form"] = "Unknown"
    return out


def commercial_for_group(primary_name, rows):
    """Return True when the group's totals meet the NDPS commercial threshold.

    rows are dicts with weight_kg, volume_l, count_total, is_commercial.
    An existing True flag propagates, matching _apply_commercial_quantity_check.
    """
    if any(r.get("is_commercial") for r in rows):
        return True
    key = (primary_name or "").lower().strip()
    if key == "mdm":
        key = "mdma"
    total_kg = sum(float(r.get("weight_kg") or 0) for r in rows)
    total_l = sum(float(r.get("volume_l") or 0) for r in rows)
    total_count = sum(float(r.get("count_total") or 0) for r in rows)
    if total_kg > 0 and key in COMMERCIAL_QUANTITY_KG and total_kg >= COMMERCIAL_QUANTITY_KG[key]:
        return True
    if total_l > 0 and key in COMMERCIAL_QUANTITY_L and total_l >= COMMERCIAL_QUANTITY_L[key]:
        return True
    if total_count > 0 and key in COMMERCIAL_QUANTITY_COUNT and total_count >= COMMERCIAL_QUANTITY_COUNT[key]:
        return True
    return False


def apply_commercial_flags(rows):
    groups = {}
    for row in rows:
        key = ((row.get("crime_id") or ""), (row.get("primary_drug_name") or "").lower().strip())
        groups.setdefault(key, []).append(row)
    for group in groups.values():
        flag = commercial_for_group(group[0].get("primary_drug_name"), group)
        if flag:
            for row in group:
                row["is_commercial"] = True
        elif row_missing_flag(group):
            for row in group:
                if row.get("is_commercial") is None:
                    row["is_commercial"] = False
    return rows


def row_missing_flag(group):
    return any(row.get("is_commercial") is None for row in group)


def parse_accused_status(status_str: Optional[str]) -> dict:
    """Substring rules from etl_accused.parse_accused_status. Does not invent a date."""
    if not status_str:
        return {}
    result = {}
    status_lower = status_str.lower()
    if "41a" in status_lower and "issued" in status_lower:
        result["is_41a_crpc"] = True
        date_match = re.search(r"(\d{2})/(\d{2})/(\d{4})", status_str)
        if date_match:
            day, month, year = date_match.groups()
            result["date_of_issue_41a"] = f"{year}-{month}-{day}"
    if "pending" in status_lower:
        result["is_41a_pending"] = True
    if "arrest" in status_lower:
        result["is_arrested"] = True
    if "abscond" in status_lower:
        result["is_absconding"] = True
    return result


def classify_accused_type(role_text: Optional[str]) -> Optional[str]:
    """Map explicitly extracted role text to a drug-role label.

    Returns None when the text has no recognised keyword. Does not invent a
    role from an empty string. Call only after role_in_crime was taken from
    the FIR for that existing accused.
    """
    if not role_text or not str(role_text).strip():
        return None

    t = str(role_text).lower()

    if any(k in t for k in [
        "selling", "sold", "retailer", "street dealer", "waiting for customers",
        "commission for selling", "sale of", "business", "intending to sell",
        "to sell", "distributing", "pushing", "hawking", "street sale",
        "spot sale", "trafficking", "peddling",
    ]):
        return "peddler"

    if any(k in t for k in [
        "habitually consuming", "consuming", "consumption", "consumption of",
        "smoking", "urine test", "tested positive", "for personal use",
        "for self use", "addict", "addicted", "purchased for consumption",
        "bought for consumption", "buy for consumption", "personal consumption",
        "consumed", "using drugs", "drug user", "under influence",
        "for own use", "for consumption",
    ]):
        return "consumer"

    if any(k in t for k in [
        "mastermind", "kingpin", "organizer", "planned the operation",
        "network leader", "head of", "directed", "controlled", "ringleader",
        "boss", "gang leader", "in-charge", "overseeing", "coordinating",
        "managing the operation",
    ]):
        return "organizer_kingpin"

    if any(k in t for k in [
        "supplied", "supplier", "distributor", "wholesaler", "bulk",
        "large quantity", "provided drugs to", "source of supply",
        "procured from", "procured", "transporting", "transport",
        "transporter", "carrying", "delivering", "courier", "driver",
        "dispatch", "shipment", "transit", "source of", "receiver",
        "recipient", "received from", "owner of crime vehicle",
        "owner of the vehicle", "crime vehicle", "brought from",
        "bought from", "intended to hand over", "hand over",
    ]):
        return "supplier"

    if any(k in t for k in [
        "manufactured", "production of", "producing", "growing", "cultivator",
        "farming", "cultivated", "grown", "grower", "farm", "cultivation",
        "producer",
    ]):
        return "manufacturer"

    if any(k in t for k in [
        "shelter", "safe house", "lodge owner", "rented room", "harboured",
        "concealed", "premises used", "hiding", "hiding place", "stash house",
        "storing at", "stored at", "kept at", "concealing",
    ]):
        return "harbourer"

    if any(k in t for k in [
        "financed", "finance", "funded", "funding", "investor", "invested",
        "money launder", "provided capital", "backer", "sponsored",
        "money for purchase", "provided money", "lender",
    ]):
        return "financier"

    if any(k in t for k in [
        "processed ganja", "converted", "refined", "chemical processing",
        "lab", "processing", "packaging", "packed", "repacked", "mixing",
        "adulteration", "weighing and packing",
    ]):
        return "processor"

    if any(k in t for k in [
        "caught with", "possession", "possession of", "found in possession",
        "purchasing", "purchased", "bought", "buy", "small scale",
    ]):
        return "peddler"

    return None


def resolve_is_ccl(age, explicit_for_this_accused=None):
    """CCL for one accused only.

    Age of that accused wins. An explicit CCL/juvenile/minor statement about
    that same accused may set True. Another accused's age never transfers.
    Missing evidence returns None (unresolved), never a guessed boolean.
    """
    if age is not None and str(age).strip() != "":
        try:
            age_int = int(age)
        except (TypeError, ValueError):
            age_int = None
        else:
            return age_int < 18
    if explicit_for_this_accused is True:
        return True
    if explicit_for_this_accused is False:
        return False
    return None
