"""Bounded extraction clients for drugs and known-accused enrichment.

Drug prompt: seizure and quantity only; explicit named substances only.
Accused prompt: existing CCTNS accused_id roster only; no discovery of new names.
Model defaults: temperature 0, Ollama chat, format=json, one retry.
Clients are not used unless ETL3_AI_ENABLED=1.
"""
import json
import re
import urllib.error
import urllib.request
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


class AIExtractionError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


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


def parse_drug_response(text: str) -> dict:
    """Validate model output. Raises AIExtractionError(status='invalid') on reject.

    An empty drugs list is valid and means the text had no seizure to store.
    Entries with null/blank raw_drug_name are dropped (generic / unnamed).
    """
    if text is None or not str(text).strip():
        raise AIExtractionError("empty", "model returned an empty body")
    try:
        payload = json.loads(_strip_fences(str(text)))
    except json.JSONDecodeError as exc:
        raise AIExtractionError("invalid", f"malformed json: {exc}") from exc
    if not isinstance(payload, dict) or "drugs" not in payload:
        raise AIExtractionError("invalid", "response is not an object with drugs")
    drugs = payload["drugs"]
    if drugs is None:
        drugs = []
    if not isinstance(drugs, list):
        raise AIExtractionError("invalid", "drugs is not a list")
    cleaned = []
    for item in drugs:
        if not isinstance(item, dict):
            raise AIExtractionError("invalid", "drug entry is not an object")
        name = item.get("raw_drug_name")
        if name is None or not str(name).strip() or str(name).strip().lower() in ("null", "none"):
            # Explicit null / blank name: skip row (not a schema failure).
            continue
        form = str(item.get("drug_form") or "solid").lower().strip()
        if form not in ALLOWED_FORMS:
            raise AIExtractionError("invalid", f"drug_form {form!r} is not allowed")
        scope = str(item.get("worth_scope") or "individual").lower().strip()
        if scope not in ALLOWED_SCOPES:
            raise AIExtractionError("invalid", f"worth_scope {scope!r} is not allowed")
        try:
            qty = float(item.get("raw_quantity") or 0)
            worth = float(item.get("seizure_worth") or 0)
        except (TypeError, ValueError) as exc:
            raise AIExtractionError("invalid", "quantity or worth is not numeric") from exc
        if qty < 0 or worth < 0:
            raise AIExtractionError("invalid", "negative quantity or worth")
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
    return {"drugs": cleaned}


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


def source_mentions_quantity(quantity, source_text) -> bool:
    """True when the numeric quantity is evidenced in the source text."""
    if quantity is None or source_text is None:
        return False
    text = str(source_text)
    try:
        qty = float(quantity)
    except (TypeError, ValueError):
        return False
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
    if qty not in (None, "", 0, 0.0) and source_text and not source_mentions_quantity(qty, source_text):
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


def _ollama_chat(host, model, timeout, prompt: str) -> str:
    body = json.dumps({
        "model": model,
        "stream": False,
        "format": "json",
        "messages": [{"role": "user", "content": prompt}],
        "options": {"temperature": 0},
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{host}/api/chat",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except TimeoutError as exc:
        raise AIExtractionError("timeout", str(exc)) from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, TimeoutError):
            raise AIExtractionError("timeout", str(exc)) from exc
        raise AIExtractionError("error", str(exc)) from exc
    return (payload.get("message") or {}).get("content") or payload.get("response") or ""


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
        return _ollama_chat(self.host, self.model, self.timeout, prompt)


class OllamaAccusedClient:
    def __init__(self, host, model, timeout):
        self.host, self.model, self.timeout = _normalize_host_model(host, model, timeout)

    def complete(self, brief_facts: str, roster: str) -> str:
        prompt = (
            load_accused_prompt()
            .replace("{roster}", roster or "")
            .replace("{text}", brief_facts or "")
        )
        return _ollama_chat(self.host, self.model, self.timeout, prompt)


def parse_accused_response(text: str, allowed_accused_ids=None, allowed_codes=None) -> dict:
    """Validate known-accused enrichment JSON.

    Entries whose accused_id/code are outside the CCTNS roster are dropped.
    Empty accused list is valid when the roster was empty or the text added nothing.
    """
    if text is None or not str(text).strip():
        raise AIExtractionError("empty", "model returned an empty body")
    try:
        payload = json.loads(_strip_fences(str(text)))
    except json.JSONDecodeError as exc:
        raise AIExtractionError("invalid", f"malformed json: {exc}") from exc
    if not isinstance(payload, dict) or "accused" not in payload:
        raise AIExtractionError("invalid", "response is not an object with accused")
    items = payload["accused"]
    if items is None:
        items = []
    if not isinstance(items, list):
        raise AIExtractionError("invalid", "accused is not a list")

    allowed_ids = set(allowed_accused_ids or [])
    allowed_codes = {
        re.sub(r"\s+", "", str(c).upper()) for c in (allowed_codes or []) if c
    }
    cleaned = []
    rejected = []
    for item in items:
        if not isinstance(item, dict):
            raise AIExtractionError("invalid", "accused entry is not an object")
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
                raise AIExtractionError("invalid", "age is not an integer") from exc
            if age < 0 or age > 120:
                raise AIExtractionError("invalid", "age out of range")
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
    """Bounded exponential backoff between transient AI failures."""
    import time

    # attempt_index is 0-based after the first failure: 1s, 2s, 4s (cap 30s)
    delay = min(30.0, float(2 ** attempt_index))
    time.sleep(delay)


def extract_with_retry(client, brief_facts: str, max_retries: int = 1):
    """Call the drug client, validate, and retry transient failures.

    Transient statuses (timeout/error) use bounded exponential backoff.
    Permanent statuses (invalid/empty) are not retried.
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
            last = exc
            if exc.status in ("timeout", "error") and transient_failures < max_retries:
                _retry_sleep(transient_failures)
                transient_failures += 1
                continue
            raise last


def extract_accused_with_retry(client, brief_facts, roster, allowed_accused_ids,
                               allowed_codes, max_retries: int = 1):
    """Call the known-accused client and keep only roster members.

    Transient statuses (timeout/error) use bounded exponential backoff.
    Permanent statuses (invalid/empty) are not retried.
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
            last = exc
            if exc.status in ("timeout", "error") and transient_failures < max_retries:
                _retry_sleep(transient_failures)
                transient_failures += 1
                continue
            raise last
