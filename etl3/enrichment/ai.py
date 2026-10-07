"""Bounded extraction clients for drugs and known-accused enrichment.

Drug prompt: seizure and quantity only.
Accused prompt: existing CCTNS accused_id roster only; no discovery of new names.
Model defaults match core/llm_service.py get_llm('extraction'): temperature
0, Ollama chat, one retry. Clients are not used unless ETL3_AI_ENABLED=1.
"""
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

PROMPT_PATH = Path(__file__).resolve().parent / "drug_extraction_prompt.txt"
ACCUSED_PROMPT_PATH = Path(__file__).resolve().parent / "accused_extraction_prompt.txt"
ALLOWED_FORMS = {"solid", "liquid", "count"}
ALLOWED_SCOPES = {"individual", "drug_total", "overall_total"}
ALLOWED_ACCUSED_STATUS = {"arrested", "absconding"}


class AIExtractionError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def load_prompt():
    return PROMPT_PATH.read_text(encoding="utf-8")


def load_accused_prompt():
    return ACCUSED_PROMPT_PATH.read_text(encoding="utf-8")


def ai_settings():
    enabled = os.environ.get("ETL3_AI_ENABLED", "").strip().lower() in ("1", "true", "yes")
    return {
        "enabled": enabled,
        "model": os.environ.get("LLM_MODEL_EXTRACTION", ""),
        "host": os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/"),
        "timeout": int(os.environ.get("LLM_TIMEOUT", "300")),
        "limit": int(os.environ.get("ETL3_AI_LIMIT", "0")),
        "max_retries": 1,
    }


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
        if not name or not str(name).strip():
            raise AIExtractionError("invalid", "drug entry missing raw_drug_name")
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
        cleaned.append({
            "raw_drug_name": str(name).strip(),
            "raw_quantity": qty,
            "raw_unit": item.get("raw_unit") or None,
            "primary_drug_name": item.get("primary_drug_name") or None,
            "drug_form": form,
            "seizure_worth": worth,
            "worth_scope": scope,
            "is_commercial": bool(item.get("is_commercial") or False),
            "confidence_score": item.get("confidence_score"),
            "source_sentence": ((item.get("extraction_metadata") or {}) if isinstance(item.get("extraction_metadata"), dict) else {}).get("source_sentence") or "",
        })
    return {"drugs": cleaned}


def _ollama_chat(host, model, timeout, prompt: str) -> str:
    body = json.dumps({
        "model": model,
        "stream": False,
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
    if not model:
        raise AIExtractionError("error", "LLM_MODEL_EXTRACTION is not set")
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


def extract_with_retry(client, brief_facts: str, max_retries: int = 1):
    """Call the drug client, validate, and retry timeout/invalid/error once.

    Returns (parsed_dict, attempt_count). Does not write anywhere.
    """
    last = None
    attempts = 0
    for _ in range(max_retries + 1):
        attempts += 1
        try:
            raw = client.complete(brief_facts)
            return parse_drug_response(raw), attempts
        except AIExtractionError as exc:
            last = exc
    raise last


def extract_accused_with_retry(client, brief_facts, roster, allowed_accused_ids,
                               allowed_codes, max_retries: int = 1):
    """Call the known-accused client and keep only roster members."""
    last = None
    attempts = 0
    for _ in range(max_retries + 1):
        attempts += 1
        try:
            raw = client.complete(brief_facts, roster)
            return parse_accused_response(
                raw,
                allowed_accused_ids=allowed_accused_ids,
                allowed_codes=allowed_codes,
            ), attempts
        except AIExtractionError as exc:
            last = exc
    raise last
