"""Bounded extraction clients for drugs and known-accused enrichment.

Drug prompt: seizure and quantity only; explicit named substances only.
Accused prompt: existing CCTNS accused_id roster only; no discovery of new names.
Model defaults: temperature 0, Ollama chat, format=JSON-schema, bounded retries.
Clients are not used unless ETL3_AI_ENABLED=1.
"""
from __future__ import annotations

import json
import os
import re
import threading
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation
from pathlib import Path

from etl3.enrichment.kb import compact

PROMPT_PATH = Path(__file__).resolve().parent / "drug_extraction_prompt.txt"
ACCUSED_PROMPT_PATH = Path(__file__).resolve().parent / "accused_extraction_prompt.txt"
ALLOWED_FORMS = {"solid", "liquid", "count"}
ALLOWED_SCOPES = {"individual", "drug_total", "overall_total"}
ALLOWED_ACCUSED_STATUS = {"arrested", "absconding"}

# Rejection reasons recorded on ai_extraction_attempts.validation_errors
REASON_GENERIC_IGNORED = "GENERIC_IGNORED"
REASON_SOURCE_UNSUPPORTED = "SOURCE_UNSUPPORTED"
REASON_INVALID_SCHEMA = "INVALID_SCHEMA"
REASON_EMPTY_RESPONSE = "EMPTY_RESPONSE"
REASON_INVALID_QUANTITY = "INVALID_QUANTITY"
REASON_INVALID_UNIT = "INVALID_UNIT"
REASON_NON_DRUG_PROPERTY = "NON_DRUG_PROPERTY"

# Documented generics not always present on kb.drug_ignore_list. Used only as a
# secondary code-level guard for AI validation; KB ignore list remains primary
# for resolve_primary_name. Do not insert these into the database here.
CODE_LEVEL_GENERIC_TERMS = frozenset({
    "contraband",
    "substance",
    "material",
    "narcotic",
    "narcotic substance",
})

# Non-drug seized property that an extraction occasionally proposes as if it
# were a drug row (observed real cases: "Mobile Phones", "Cell Phone", "Cash",
# "Vehicle", "TVS Star City Bike", "Polythene Cover", "Laptop", shotgun
# cartridges). None of these are NDPS substances; they belong in the existing
# non-drug property module, never in drug_extractions. Matched by exact or
# compacted substring so brand/model text ("Realme Narzo 50-A") still hits the
# "cell phone" guard via the raw item's own descriptive name.
NON_DRUG_PROPERTY_TERMS = (
    "mobile phone", "mobile phones", "cell phone", "cell phones",
    "smart phone", "smartphone", "handset",
    "cash", "currency", "money",
    "vehicle", "motorcycle", "motor cycle", "scooty", "bike", "car", "auto",
    "cartridge", "cartridges", "shotgun cartridge", "gun", "pistol", "weapon",
    "knife", "laptop", "sim card", "sim cards", "document", "documents",
    "polythene cover", "polythene covers", "empty cover", "empty covers",
    "wallet", "chain", "gold ornament", "ornament", "watch",
)
# Matched as a whole word/phrase (word-boundary, not a bare substring) so
# this never misfires on a real drug name that happens to contain these
# letters, e.g. "gunja" / "gunjayi" (an OCR/phonetic variant of Ganja
# contains "gun") or "Carfentanil" (a real opioid; contains "car") -- \b
# requires an actual word break on both sides, which neither has.
_NON_DRUG_PROPERTY_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(t) for t in NON_DRUG_PROPERTY_TERMS) + r")\b",
    re.IGNORECASE,
)


def is_non_drug_property(name) -> bool:
    if name is None:
        return False
    text = str(name).strip()
    if not text:
        return False
    return bool(_NON_DRUG_PROPERTY_RE.search(text))


class AIExtractionError(Exception):
    def __init__(self, status, message, *, raw_response: str | None = None):
        super().__init__(message)
        self.status = status
        # Preserve model body for audit when parse/validate fails.
        self.raw_response = raw_response


# Ollama ``format`` JSON Schemas (structured outputs). Kept in sync with
# parse_drug_response / parse_accused_response contracts.
DRUG_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "drugs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "raw_drug_name": {"type": ["string", "null"]},
                    "raw_quantity": {"type": "number"},
                    "raw_unit": {"type": ["string", "null"]},
                    "primary_drug_name": {"type": ["string", "null"]},
                    "drug_form": {"type": "string", "enum": ["solid", "liquid", "count"]},
                    "seizure_worth": {"type": "number"},
                    "worth_scope": {
                        "type": "string",
                        "enum": ["individual", "drug_total", "overall_total"],
                    },
                    "is_commercial": {"type": "boolean"},
                    "confidence_score": {"type": ["integer", "null"]},
                    "extraction_metadata": {
                        "type": "object",
                        "properties": {
                            "source_sentence": {"type": "string"},
                        },
                        "required": ["source_sentence"],
                    },
                },
                "required": [
                    "raw_drug_name",
                    "raw_quantity",
                    "raw_unit",
                    "primary_drug_name",
                    "drug_form",
                    "seizure_worth",
                    "worth_scope",
                    "is_commercial",
                    "confidence_score",
                    "extraction_metadata",
                ],
            },
        }
    },
    "required": ["drugs"],
}

ACCUSED_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "accused": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "accused_code": {"type": ["string", "null"]},
                    "accused_id": {"type": ["string", "null"]},
                    "role_in_crime": {"type": ["string", "null"]},
                    "key_details": {"type": ["string", "null"]},
                    "age": {"type": ["integer", "null"]},
                    "alias_name": {"type": ["string", "null"]},
                    "gender": {"type": ["string", "null"]},
                    "occupation": {"type": ["string", "null"]},
                    "address": {"type": ["string", "null"]},
                    "phone_numbers": {"type": ["string", "null"]},
                    # Parser accepts only arrested|absconding|null; keep schema
                    # loose so Ollama JSON-schema engines accept null cleanly.
                    "status": {"type": ["string", "null"]},
                    "explicit_ccl": {"type": ["boolean", "null"]},
                },
                "required": [
                    "accused_code",
                    "accused_id",
                    "role_in_crime",
                    "key_details",
                    "age",
                    "alias_name",
                    "gender",
                    "occupation",
                    "address",
                    "phone_numbers",
                    "status",
                    "explicit_ccl",
                ],
            },
        }
    },
    "required": ["accused"],
}


def load_prompt():
    return PROMPT_PATH.read_text(encoding="utf-8")


def load_accused_prompt():
    return ACCUSED_PROMPT_PATH.read_text(encoding="utf-8")


def ai_settings():
    """Resolve Ollama settings from etl3.config.settings (environment only).

    No hardcoded endpoint or model fallback. When AI is enabled,
    OLLAMA_BASE_URL and OLLAMA_MODEL must be set or this raises RuntimeError.
    """
    from etl3.config.settings import resolve_ai_settings

    return resolve_ai_settings()


def _strip_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
        if cleaned.endswith("```"):
            cleaned = cleaned[: cleaned.rfind("```")].strip()
    return cleaned


def _is_malformed_json_error(exc: AIExtractionError) -> bool:
    return exc.status == "invalid" and str(exc).lower().startswith("malformed json")


def parse_drug_response(text: str) -> dict:
    """Validate model output. Raises AIExtractionError(status='invalid') on reject.

    An empty drugs list is valid and means the text had no seizure to store.
    Entries with a null/blank raw_drug_name never become a drug row (a name
    is never invented), but when such an item still carries real seizure
    evidence (a quantity, unit, or worth) it is kept in ``ambiguous`` for
    audit/manual-review rather than silently discarded -- this is what lets
    a crime be classified "Manual review" instead of "No drug Found" when
    the source describes a seizure of an only-suspected substance.
    """
    raw = "" if text is None else str(text)
    if not raw.strip():
        raise AIExtractionError("empty", "model returned an empty body", raw_response=raw)
    try:
        payload = json.loads(_strip_fences(raw))
    except json.JSONDecodeError as exc:
        raise AIExtractionError(
            "invalid", f"malformed json: {exc}", raw_response=raw,
        ) from exc
    if not isinstance(payload, dict) or "drugs" not in payload:
        raise AIExtractionError(
            "invalid", "response is not an object with drugs", raw_response=raw,
        )
    drugs = payload["drugs"]
    if drugs is None:
        drugs = []
    if not isinstance(drugs, list):
        raise AIExtractionError("invalid", "drugs is not a list", raw_response=raw)
    cleaned = []
    ambiguous = []
    for item in drugs:
        if not isinstance(item, dict):
            raise AIExtractionError(
                "invalid", "drug entry is not an object", raw_response=raw,
            )
        name = item.get("raw_drug_name")
        if name is None or not str(name).strip() or str(name).strip().lower() in ("null", "none"):
            # Explicit null / blank name: never invent one, so this item can
            # never become a drug row. If it still carries real evidence of
            # a seizure, record it as ambiguous for manual review instead of
            # dropping it with no trace (not a schema failure either way).
            try:
                amb_qty = float(item.get("raw_quantity") or 0)
            except (TypeError, ValueError):
                amb_qty = 0.0
            try:
                amb_worth = float(item.get("seizure_worth") or 0)
            except (TypeError, ValueError):
                amb_worth = 0.0
            amb_unit = item.get("raw_unit")
            if amb_qty > 0 or amb_worth > 0 or (amb_unit and str(amb_unit).strip()):
                meta = item.get("extraction_metadata")
                ambiguous.append({
                    "raw_quantity": amb_qty or None,
                    "raw_unit": str(amb_unit).strip() if amb_unit else None,
                    "seizure_worth": amb_worth or None,
                    "source_sentence": (meta or {}).get("source_sentence", "") if isinstance(meta, dict) else "",
                })
            continue
        form = str(item.get("drug_form") or "solid").lower().strip()
        if form not in ALLOWED_FORMS:
            raise AIExtractionError(
                "invalid", f"drug_form {form!r} is not allowed", raw_response=raw,
            )
        scope = str(item.get("worth_scope") or "individual").lower().strip()
        if scope not in ALLOWED_SCOPES:
            raise AIExtractionError(
                "invalid", f"worth_scope {scope!r} is not allowed", raw_response=raw,
            )
        try:
            qty = float(item.get("raw_quantity") or 0)
            worth = float(item.get("seizure_worth") or 0)
        except (TypeError, ValueError) as exc:
            raise AIExtractionError(
                "invalid", "quantity or worth is not numeric", raw_response=raw,
            ) from exc
        if qty < 0 or worth < 0:
            raise AIExtractionError(
                "invalid", "negative quantity or worth", raw_response=raw,
            )
        primary = item.get("primary_drug_name")
        if primary is not None and str(primary).strip().lower() in ("", "null", "none"):
            primary = None
        cleaned.append({
            "raw_drug_name": str(name).strip(),
            "raw_quantity": qty,
            "raw_unit": item.get("raw_unit") or None,
            "primary_drug_name": (str(primary).strip() if primary else None) or None,
            "drug_form": form,
            "seizure_worth": worth,
            "worth_scope": scope,
            "is_commercial": bool(item.get("is_commercial") or False),
            "confidence_score": item.get("confidence_score"),
            "source_sentence": ((item.get("extraction_metadata") or {}) if isinstance(item.get("extraction_metadata"), dict) else {}).get("source_sentence") or "",
        })
    return {"drugs": cleaned, "ambiguous": ambiguous}


def source_mentions_label(label, source_text) -> bool:
    """True when label appears in source (case-insensitive or compacted)."""
    if label is None or source_text is None:
        return False
    name = str(label).strip()
    text = str(source_text)
    if not name or not text.strip():
        return False
    if name.lower() in text.lower():
        return True
    nc = compact(name)
    if len(nc) >= 3 and nc in compact(text):
        return True
    return False


# Weight/volume unit -> factor to a common base unit (grams / millilitres).
# Spelling variants and abbreviations observed in Brief Facts text. Only
# units in these two tables get semantic (unit-aware) quantity matching;
# everything else (counts, "packets", worth-like numbers, no unit at all)
# keeps the original literal-substring heuristic.
_WEIGHT_UNIT_TO_GRAMS = {
    "mg": Decimal("0.001"), "mgs": Decimal("0.001"),
    "milligram": Decimal("0.001"), "milligrams": Decimal("0.001"),
    "g": Decimal("1"), "gm": Decimal("1"), "gms": Decimal("1"),
    "gram": Decimal("1"), "grams": Decimal("1"), "grm": Decimal("1"),
    "grms": Decimal("1"), "grsm": Decimal("1"), "gr": Decimal("1"),
    "kg": Decimal("1000"), "kgs": Decimal("1000"),
    "kilogram": Decimal("1000"), "kilograms": Decimal("1000"),
    "kilo": Decimal("1000"), "kilos": Decimal("1000"),
}
_VOLUME_UNIT_TO_ML = {
    "ml": Decimal("1"), "milliliter": Decimal("1"), "milliliters": Decimal("1"),
    "millilitre": Decimal("1"), "millilitres": Decimal("1"),
    "l": Decimal("1000"), "ltr": Decimal("1000"), "ltrs": Decimal("1000"),
    "liter": Decimal("1000"), "liters": Decimal("1000"),
    "litre": Decimal("1000"), "litres": Decimal("1000"),
}


def _unit_alternation(units) -> str:
    # Longest-first so e.g. "kilograms" is not cut short at "kilo".
    return "|".join(sorted((re.escape(u) for u in units), key=len, reverse=True))


_NUMBER_GROUP = r"\d[\d,]*(?:\.\d+)?"
# Punctuation observed between a number and its unit word in scanned/typed
# Brief Facts, e.g. "(1) Kg", "6- Kgs", "16-kgs.", "1. KG". Bounded to a few
# characters so this never bridges across an unrelated number.
_NUM_UNIT_GAP = r"[\s.,():&-]{0,5}"
_WEIGHT_MENTION_RE = re.compile(
    rf"({_NUMBER_GROUP}){_NUM_UNIT_GAP}({_unit_alternation(_WEIGHT_UNIT_TO_GRAMS)})\b",
    re.IGNORECASE,
)
_VOLUME_MENTION_RE = re.compile(
    rf"({_NUMBER_GROUP}){_NUM_UNIT_GAP}({_unit_alternation(_VOLUME_UNIT_TO_ML)})\b",
    re.IGNORECASE,
)
# Connector text allowed between chained split-unit mentions, e.g.
# "21 Kgs 499 grams", "1 Kg and 500 Grams", "15 Kgs, 631 grams",
# "16-kgs. 290-grams", "1 Kilo & 686 grams".
_CHAIN_GAP_RE = re.compile(r"^[\s,.;&-]*(?:and)?[\s,.;&-]*$", re.IGNORECASE)
_MAX_CHAIN_GAP_CHARS = 20
_QTY_TOLERANCE_REL = Decimal("0.001")
_QTY_TOLERANCE_ABS = Decimal("0.01")

# Fraction quantities observed in real Brief Facts, e.g. "1½ Kgs", "3 ½
# Kgs", "1 1/2 Kg", "6(1/2)KG", "4½ KGs". Normalized to a decimal string
# BEFORE unit-mention scanning so the rest of the matcher is unaffected.
_UNICODE_FRACTION_VALUES = {"½": Decimal("0.5"), "¼": Decimal("0.25"), "¾": Decimal("0.75")}
_ASCII_FRACTION_VALUES = {"1/2": Decimal("0.5"), "1/4": Decimal("0.25"), "3/4": Decimal("0.75")}
_MIXED_UNICODE_FRACTION_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([½¼¾])")
_BARE_UNICODE_FRACTION_RE = re.compile(r"([½¼¾])")
_MIXED_ASCII_FRACTION_RE = re.compile(r"(\d+(?:\.\d+)?)\s*\(?\s*(1/2|1/4|3/4)\s*\)?")
_BARE_ASCII_FRACTION_RE = re.compile(r"(?<![\d./])(1/2|1/4|3/4)(?![\d/])")


def _normalize_fractions(text: str) -> str:
    text = _MIXED_UNICODE_FRACTION_RE.sub(
        lambda m: str(Decimal(m.group(1)) + _UNICODE_FRACTION_VALUES[m.group(2)]), text,
    )
    text = _BARE_UNICODE_FRACTION_RE.sub(
        lambda m: str(_UNICODE_FRACTION_VALUES[m.group(1)]), text,
    )
    text = _MIXED_ASCII_FRACTION_RE.sub(
        lambda m: str(Decimal(m.group(1)) + _ASCII_FRACTION_VALUES[m.group(2)]), text,
    )
    text = _BARE_ASCII_FRACTION_RE.sub(
        lambda m: str(_ASCII_FRACTION_VALUES[m.group(1)]), text,
    )
    return text


# "N <words> each <weight mention>" -- repeated identical-quantity items,
# e.g. "5 Ganja packets each one 100 grams", "19 packets (each packet wg
# about 10 grams)", "10 ... boxes each consisting 11 gms". The count must be
# within a short, same-sentence span of the literal word "each" immediately
# before the mention for this to count as one multiplicative expression.
_COUNT_BEFORE_EACH_RE = re.compile(
    rf"({_NUMBER_GROUP})(?:(?!each)[^.]){{0,40}}?\beach\b", re.IGNORECASE,
)
_MAX_EACH_LOOKBACK_CHARS = 80


def _each_multiplier_candidates(text, mentions):
    candidates = []
    for start, _end, value, _factor in mentions:
        window = text[max(0, start - _MAX_EACH_LOOKBACK_CHARS):start]
        match = None
        for match in _COUNT_BEFORE_EACH_RE.finditer(window):
            pass  # keep the last (closest-to-the-mention) match
        if match is None:
            continue
        count = _to_decimal(match.group(1).replace(",", ""))
        if count is not None and count > 0:
            candidates.append(value * count)
    return candidates


def _to_decimal(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _approx_equal(a, b) -> bool:
    if a is None or b is None:
        return False
    tolerance = max(_QTY_TOLERANCE_ABS, abs(b) * _QTY_TOLERANCE_REL)
    return abs(a - b) <= tolerance


def _unit_mentions(text, unit_table, pattern):
    """(start, end, value_in_base_unit, unit_factor) for every match, in order."""
    mentions = []
    for match in pattern.finditer(text):
        value = _to_decimal(match.group(1).replace(",", ""))
        if value is None:
            continue
        factor = unit_table[match.group(2).lower()]
        mentions.append((match.start(), match.end(), value * factor, factor))
    return sorted(mentions, key=lambda m: m[0])


def _compound_chain_sums(text, mentions):
    """Sums of adjacent mentions whose unit strictly shrinks (kg then g, …).

    Only a short, punctuation/"and"-only gap between two mentions counts as
    one split-unit expression; unrelated nearby numbers never chain.
    """
    sums = []
    i = 0
    n = len(mentions)
    while i < n:
        total = mentions[i][2]
        j = i
        while j + 1 < n:
            gap = text[mentions[j][1]:mentions[j + 1][0]]
            if (
                len(gap) <= _MAX_CHAIN_GAP_CHARS
                and _CHAIN_GAP_RE.match(gap)
                and mentions[j + 1][3] < mentions[j][3]
            ):
                total += mentions[j + 1][2]
                j += 1
            else:
                break
        if j > i:
            sums.append(total)
        i = j + 1
    return sums


def _semantic_quantity_match(qty_value, unit_norm, text):
    """Unit-aware weight/volume match, or None when ``unit_norm`` isn't one.

    None tells the caller to fall back to the legacy literal-substring
    check (counts, worth-like figures, or no usable unit at all).
    """
    text = _normalize_fractions(text)
    if unit_norm in _WEIGHT_UNIT_TO_GRAMS:
        base = qty_value * _WEIGHT_UNIT_TO_GRAMS[unit_norm]
        mentions = _unit_mentions(text, _WEIGHT_UNIT_TO_GRAMS, _WEIGHT_MENTION_RE)
    elif unit_norm in _VOLUME_UNIT_TO_ML:
        base = qty_value * _VOLUME_UNIT_TO_ML[unit_norm]
        mentions = _unit_mentions(text, _VOLUME_UNIT_TO_ML, _VOLUME_MENTION_RE)
    else:
        return None
    candidates = (
        [m[2] for m in mentions]
        + _compound_chain_sums(text, mentions)
        + _each_multiplier_candidates(text, mentions)
    )
    return any(_approx_equal(base, c) for c in candidates)


def source_mentions_quantity(quantity, source_text, unit=None) -> bool:
    """True when the numeric quantity is evidenced in the source text.

    When ``unit`` is a recognised weight or volume unit, both the candidate
    and every weight/volume mention in ``source_text`` are converted to a
    common base unit (grams or millilitres) and compared numerically. This
    also recognises split/mixed-unit phrasing such as "21 Kgs 499 grams" or
    "1 Kg and 500 grams", which a model may combine into a single decimal
    (21.499 kg) that never appears as a literal substring of the source.

    Falls back to the original literal-substring heuristic for counts,
    worth-like numbers, or when no usable unit is supplied, so existing
    callers that omit ``unit`` keep their prior behaviour unchanged. An
    expression this cannot support (including genuinely ambiguous ones,
    e.g. a stated range) is rejected rather than guessed.
    """
    if quantity is None or source_text is None:
        return False
    text = str(source_text)
    try:
        qty = float(quantity)
    except (TypeError, ValueError):
        return False
    qty_dec = _to_decimal(qty)
    unit_norm = re.sub(r"[^a-z]", "", str(unit or "").lower().strip())
    if qty_dec is not None and unit_norm:
        semantic = _semantic_quantity_match(qty_dec, unit_norm, text)
        if semantic is not None:
            return semantic
    candidates = {str(quantity).strip(), str(qty), f"{qty:g}"}
    if qty == int(qty):
        candidates.add(str(int(qty)))
    # Indian-style grouping for large worth-like numbers is handled separately;
    # for quantities also accept comma forms of the integer part.
    if qty == int(qty) and abs(qty) >= 1000:
        n = int(qty)
        candidates.add(f"{n:,}")
        # 52,00,000 style is rare for grams; skip unless needed for worth.
    return any(c and c in text for c in candidates)


def is_code_level_generic(name) -> bool:
    if name is None:
        return False
    text = str(name).lower().strip()
    if text in CODE_LEVEL_GENERIC_TERMS:
        return True
    c = compact(text)
    return c in {compact(t) for t in CODE_LEVEL_GENERIC_TERMS}


def validate_ai_drug_item(item, source_text, drug_kb_obj=None):
    """Validate one parsed AI drug against ignore list + source evidence.

    Returns dict with either accepted fields or reject_reason.
    KB matching is NOT evidence that the source contained the drug.
    """
    raw = (item or {}).get("raw_drug_name")
    if raw is None or not str(raw).strip():
        return {"reject_reason": REASON_EMPTY_RESPONSE, "raw_drug_name": raw}

    raw_text = str(raw).strip()
    if drug_kb_obj is not None and drug_kb_obj.is_ignored(raw_text):
        return {"reject_reason": REASON_GENERIC_IGNORED, "raw_drug_name": raw_text}
    if is_code_level_generic(raw_text):
        return {"reject_reason": REASON_GENERIC_IGNORED, "raw_drug_name": raw_text}
    if is_non_drug_property(raw_text):
        return {"reject_reason": REASON_NON_DRUG_PROPERTY, "raw_drug_name": raw_text}

    if not source_mentions_label(raw_text, source_text):
        return {"reject_reason": REASON_SOURCE_UNSUPPORTED, "raw_drug_name": raw_text}

    primary = (item or {}).get("primary_drug_name")
    if primary is not None and str(primary).strip():
        p = str(primary).strip()
        if (drug_kb_obj is not None and drug_kb_obj.is_ignored(p)) or is_code_level_generic(p):
            primary = None
        elif not source_mentions_label(p, source_text):
            # Do not trust AI primary that is absent from source; keep raw only.
            primary = None
    else:
        primary = None

    qty = (item or {}).get("raw_quantity")
    unit = (item or {}).get("raw_unit")
    worth = (item or {}).get("seizure_worth")
    out = {
        "reject_reason": None,
        "raw_drug_name": raw_text,
        "primary_drug_name": primary,
        "raw_quantity": qty,
        "raw_unit": unit,
        "seizure_worth": worth,
        "drug_form": (item or {}).get("drug_form"),
        "is_commercial": (item or {}).get("is_commercial"),
        "confidence_score": (item or {}).get("confidence_score"),
        "source_sentence": (item or {}).get("source_sentence") or "",
        "worth_scope": (item or {}).get("worth_scope"),
    }
    if qty not in (None, "", 0, 0.0) and source_text and not source_mentions_quantity(qty, source_text, unit=unit):
        out["raw_quantity"] = None
        out["quantity_reject"] = REASON_INVALID_QUANTITY
    if unit and source_text:
        unit_s = str(unit).strip()
        # Allow common unit abbreviations without forcing exact token presence
        # when the expanded form appears (grams/gm/g).
        unit_ok = source_mentions_label(unit_s, source_text)
        if not unit_ok:
            aliases = {
                "grams": ("gram", "gms", "gm", "g"),
                "gram": ("grams", "gms", "gm", "g"),
                "gm": ("grams", "gram", "gms", "g"),
                "kg": ("kilogram", "kilograms", "kgs"),
                "packets": ("packet", "pkts", "pkt"),
            }
            for alt in aliases.get(unit_s.lower(), ()):
                if source_mentions_label(alt, source_text):
                    unit_ok = True
                    break
        if not unit_ok:
            out["raw_unit"] = None
            out["unit_reject"] = REASON_INVALID_UNIT
    if worth not in (None, "", 0, 0.0) and source_text:
        # Worth often appears as Rs. 52,000 / 52,00,000 — accept digit skeleton.
        digits = re.sub(r"\D", "", str(int(float(worth))) if float(worth) == int(float(worth)) else str(worth))
        text_digits = re.sub(r"\D", "", str(source_text))
        if digits and digits not in text_digits:
            out["seizure_worth"] = 0
    return out


def _ollama_think_enabled() -> bool:
    """Qwen3 defaults to chain-of-thought; extraction needs JSON only.

    Set OLLAMA_THINK=1 to allow thinking. Default off so historical backfill
    does not stall on long reasoning traces under a hard HTTP timeout.
    """
    import os

    raw = (os.environ.get("OLLAMA_THINK") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _is_oom_error(message: str) -> bool:
    text = (message or "").lower()
    if "out of memory" in text or "cuda out of memory" in text:
        return True
    if "insufficient memory" in text or "failed to allocate" in text:
        return True
    # Match OOM as a token, not substrings like "room" / "bloom".
    return bool(re.search(r"(^|[^a-z])oom([^a-z]|$)", text))


# Bound in-flight Ollama chats. Size comes from ETL3_AI_PARALLEL (default 1).
_OLLAMA_GATE = None
_OLLAMA_GATE_N = None
_OLLAMA_GATE_LOCK = threading.Lock()
_THINK_FLAG_LOGGED = False
_LAST_LATENCY_SEC = None
_LAST_LATENCY_LOCK = threading.Lock()


def configure_ollama_parallel(n: int) -> int:
    """Set max concurrent /api/chat calls from this process (env-driven)."""
    global _OLLAMA_GATE, _OLLAMA_GATE_N
    slots = max(1, int(n))
    with _OLLAMA_GATE_LOCK:
        if _OLLAMA_GATE is None or _OLLAMA_GATE_N != slots:
            _OLLAMA_GATE = threading.BoundedSemaphore(slots)
            _OLLAMA_GATE_N = slots
            print(f"[ollama] client_parallel={slots}", flush=True)
        return slots


def _ollama_gate():
    """Return the shared semaphore; lazy-init from env if not configured yet."""
    global _OLLAMA_GATE, _OLLAMA_GATE_N
    with _OLLAMA_GATE_LOCK:
        if _OLLAMA_GATE is not None:
            return _OLLAMA_GATE
        try:
            from etl3.config.settings import resolve_ai_settings

            slots = int(resolve_ai_settings().get("parallel") or 1)
        except Exception:
            slots = 1
        slots = max(1, int(slots))
        _OLLAMA_GATE = threading.BoundedSemaphore(slots)
        _OLLAMA_GATE_N = slots
        print(f"[ollama] client_parallel={slots}", flush=True)
        return _OLLAMA_GATE


def last_inference_latency_sec():
    with _LAST_LATENCY_LOCK:
        return _LAST_LATENCY_SEC


def _num_predict_limit() -> int | None:
    """Optional completion token cap from ETL3_AI_NUM_PREDICT.

    Unset / empty / 0 → no cap (required for multi-accused JSON). A low cap
    like 512 truncates accused payloads mid-string and causes malformed JSON.
    """
    raw = (os.environ.get("ETL3_AI_NUM_PREDICT") or "").strip()
    if not raw:
        return None
    try:
        n = int(raw)
    except ValueError:
        return None
    return n if n > 0 else None


def _ollama_chat(host, model, timeout, prompt: str, *, response_format=None) -> str:
    global _THINK_FLAG_LOGGED, _LAST_LATENCY_SEC
    import time

    fmt = response_format if response_format is not None else "json"
    options = {"temperature": 0}
    num_predict = _num_predict_limit()
    if num_predict is not None:
        options["num_predict"] = num_predict
    payload = {
        "model": model,
        "stream": False,
        "format": fmt,
        "messages": [{"role": "user", "content": prompt}],
        "options": options,
    }
    # Ollama qwen3 accepts top-level think=false to skip reasoning tokens.
    think_on = _ollama_think_enabled()
    if not think_on:
        payload["think"] = False
    if not _THINK_FLAG_LOGGED:
        fmt_kind = (
            "json_schema"
            if isinstance(fmt, dict)
            else ("json" if fmt == "json" else type(fmt).__name__)
        )
        print(
            f"[ollama] model={model!r} think={think_on} "
            f"payload_think_key={payload.get('think', '<absent>')} "
            f"format={fmt_kind} num_predict={num_predict or 'unlimited'} "
            f"timeout_s={timeout}",
            flush=True,
        )
        _THINK_FLAG_LOGGED = True
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{host}/api/chat",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    gate = _ollama_gate()
    gate.acquire()
    try:
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
        except TimeoutError as exc:
            with _LAST_LATENCY_LOCK:
                _LAST_LATENCY_SEC = time.monotonic() - t0
            raise AIExtractionError("timeout", str(exc)) from exc
        except urllib.error.HTTPError as exc:
            with _LAST_LATENCY_LOCK:
                _LAST_LATENCY_SEC = time.monotonic() - t0
            body_text = ""
            try:
                body_text = exc.read().decode("utf-8", "replace")[:500]
            except Exception:
                pass
            msg = f"HTTP {exc.code}: {body_text or exc.reason}"
            if _is_oom_error(msg):
                raise AIExtractionError("error", f"oom_non_retryable: {msg}") from exc
            raise AIExtractionError("error", msg) from exc
        except urllib.error.URLError as exc:
            with _LAST_LATENCY_LOCK:
                _LAST_LATENCY_SEC = time.monotonic() - t0
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, TimeoutError):
                raise AIExtractionError("timeout", str(exc)) from exc
            msg = str(exc)
            if _is_oom_error(msg):
                raise AIExtractionError("error", f"oom_non_retryable: {msg}") from exc
            raise AIExtractionError("error", msg) from exc
        with _LAST_LATENCY_LOCK:
            _LAST_LATENCY_SEC = time.monotonic() - t0
    finally:
        gate.release()
    return (
        (response_payload.get("message") or {}).get("content")
        or response_payload.get("response")
        or ""
    )


def _normalize_host_model(host, model, timeout):
    if not host:
        raise AIExtractionError(
            "error",
            "OLLAMA_BASE_URL is not set (required for AI clients)",
        )
    if not model:
        raise AIExtractionError(
            "error",
            "OLLAMA_MODEL is not set (required for AI clients)",
        )
    host = host.rstrip("/")
    if host.endswith("/api"):
        host = host[:-4]
    return host, model, timeout


class OllamaDrugClient:
    def __init__(self, host, model, timeout):
        self.host, self.model, self.timeout = _normalize_host_model(host, model, timeout)

    def complete(self, brief_facts: str) -> str:
        prompt = load_prompt().replace("{text}", brief_facts or "")
        return _ollama_chat(
            self.host, self.model, self.timeout, prompt,
            response_format=DRUG_RESPONSE_SCHEMA,
        )


class OllamaAccusedClient:
    def __init__(self, host, model, timeout):
        self.host, self.model, self.timeout = _normalize_host_model(host, model, timeout)

    def complete(self, brief_facts: str, roster: str) -> str:
        prompt = (
            load_accused_prompt()
            .replace("{roster}", roster or "")
            .replace("{text}", brief_facts or "")
        )
        return _ollama_chat(
            self.host, self.model, self.timeout, prompt,
            response_format=ACCUSED_RESPONSE_SCHEMA,
        )


def parse_accused_response(text: str, allowed_accused_ids=None, allowed_codes=None) -> dict:
    """Validate known-accused enrichment JSON.

    Entries whose accused_id/code are outside the CCTNS roster are dropped.
    Empty accused list is valid when the roster was empty or the text added nothing.
    """
    raw = "" if text is None else str(text)
    if not raw.strip():
        raise AIExtractionError("empty", "model returned an empty body", raw_response=raw)
    try:
        payload = json.loads(_strip_fences(raw))
    except json.JSONDecodeError as exc:
        raise AIExtractionError(
            "invalid", f"malformed json: {exc}", raw_response=raw,
        ) from exc
    if not isinstance(payload, dict) or "accused" not in payload:
        raise AIExtractionError(
            "invalid", "response is not an object with accused", raw_response=raw,
        )
    items = payload["accused"]
    if items is None:
        items = []
    if not isinstance(items, list):
        raise AIExtractionError("invalid", "accused is not a list", raw_response=raw)

    allowed_ids = set(allowed_accused_ids or [])
    allowed_codes = {
        re.sub(r"\s+", "", str(c).upper()) for c in (allowed_codes or []) if c
    }
    cleaned = []
    rejected = []
    for item in items:
        if not isinstance(item, dict):
            raise AIExtractionError(
                "invalid", "accused entry is not an object", raw_response=raw,
            )
        accused_id = (item.get("accused_id") or "").strip() or None
        raw_code = (item.get("accused_code") or "").strip() or None
        code_norm = None
        if raw_code:
            match = re.search(r"A\s*[-.]?\s*(\d+)", raw_code, flags=re.IGNORECASE)
            code_norm = f"A-{int(match.group(1))}" if match else raw_code.upper()
        in_roster = False
        if accused_id and accused_id in allowed_ids:
            in_roster = True
        elif code_norm and code_norm in allowed_codes:
            in_roster = True
        if allowed_ids or allowed_codes:
            if not in_roster:
                rejected.append({"accused_id": accused_id, "accused_code": raw_code})
                continue
        status = item.get("status")
        if status is not None:
            status = str(status).strip().lower()
            if status not in ALLOWED_ACCUSED_STATUS:
                status = None
        age = item.get("age")
        if age is not None and str(age).strip() != "":
            try:
                age = int(age)
            except (TypeError, ValueError) as exc:
                raise AIExtractionError(
                    "invalid", "age is not an integer", raw_response=raw,
                ) from exc
            if age < 0 or age > 120:
                raise AIExtractionError(
                    "invalid", "age out of range", raw_response=raw,
                )
        else:
            age = None
        explicit_ccl = item.get("explicit_ccl")
        if explicit_ccl is not True and explicit_ccl is not False:
            explicit_ccl = None
        cleaned.append({
            "accused_id": accused_id,
            "accused_code": raw_code,
            "role_in_crime": (str(item["role_in_crime"]).strip() if item.get("role_in_crime") else None) or None,
            "key_details": (str(item["key_details"]).strip() if item.get("key_details") else None) or None,
            "age": age,
            "alias_name": (str(item["alias_name"]).strip() if item.get("alias_name") else None) or None,
            "gender": (str(item["gender"]).strip() if item.get("gender") else None) or None,
            "occupation": (str(item["occupation"]).strip() if item.get("occupation") else None) or None,
            "address": (str(item["address"]).strip() if item.get("address") else None) or None,
            "phone_numbers": (str(item["phone_numbers"]).strip() if item.get("phone_numbers") else None) or None,
            "status": status,
            "explicit_ccl": explicit_ccl,
        })
    return {"accused": cleaned, "rejected": rejected}


def _retry_sleep(attempt_index: int) -> None:
    """Bounded exponential backoff + jitter between transient AI failures."""
    import random
    import time

    # attempt_index is 0-based after the first failure: ~1s, ~2s, ~4s (cap 30s)
    base = min(30.0, float(2 ** attempt_index))
    jitter = random.uniform(0.0, base * 0.5)
    time.sleep(base + jitter)


def extract_with_retry(client, brief_facts: str, max_retries: int = 1):
    """Call the drug client, validate, and retry transient / truncated JSON.

    Retried (bounded): timeout, transport error, malformed JSON.
    Not retried: empty body, schema/field validation failures, GPU OOM.
    Returns (parsed_dict, attempt_count, raw_response).
    """
    last = None
    attempts = 0
    raw = ""
    transient_failures = 0
    while True:
        attempts += 1
        try:
            raw = client.complete(brief_facts)
            return parse_drug_response(raw), attempts, raw
        except AIExtractionError as exc:
            if getattr(exc, "raw_response", None) is None and raw:
                exc.raw_response = raw
            last = exc
            if _is_oom_error(str(exc)):
                # Do not burn the GPU with immediate retries on OOM.
                raise AIExtractionError(
                    "error", f"oom_non_retryable: {exc}", raw_response=exc.raw_response,
                ) from exc
            retryable = exc.status in ("timeout", "error") or _is_malformed_json_error(exc)
            if retryable and transient_failures < max_retries:
                _retry_sleep(transient_failures)
                transient_failures += 1
                continue
            raise last


def extract_accused_with_retry(client, brief_facts, roster, allowed_accused_ids,
                               allowed_codes, max_retries: int = 1):
    """Call the known-accused client and keep only roster members.

    Retried (bounded): timeout, transport error, malformed JSON.
    Not retried: empty body, schema/field validation failures, GPU OOM.
    Returns (parsed_dict, attempt_count, raw_response).
    """
    last = None
    attempts = 0
    raw = ""
    transient_failures = 0
    while True:
        attempts += 1
        try:
            raw = client.complete(brief_facts, roster)
            return parse_accused_response(
                raw,
                allowed_accused_ids=allowed_accused_ids,
                allowed_codes=allowed_codes,
            ), attempts, raw
        except AIExtractionError as exc:
            if getattr(exc, "raw_response", None) is None and raw:
                exc.raw_response = raw
            last = exc
            if _is_oom_error(str(exc)):
                raise AIExtractionError(
                    "error", f"oom_non_retryable: {exc}", raw_response=exc.raw_response,
                ) from exc
            retryable = exc.status in ("timeout", "error") or _is_malformed_json_error(exc)
            if retryable and transient_failures < max_retries:
                _retry_sleep(transient_failures)
                transient_failures += 1
                continue
            raise last
