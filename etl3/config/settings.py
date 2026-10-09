"""
ETL-3 configuration loader.

Loads etl3/config/.env (never committed -- see .gitignore) and resolves the
two source .env paths it points at. Nothing here connects to anything; it
only loads values. db/connections.py is where connections actually happen,
and where the safety checks live.

Ollama endpoint/model have NO hardcoded runtime fallbacks. Values come only
from the environment / .env (preferred: OLLAMA_BASE_URL, OLLAMA_MODEL;
aliases: OLLAMA_HOST, LLM_MODEL_EXTRACTION). When AI is enabled, both are
required; when AI is disabled they may be omitted.
"""
import os
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

CONFIG_DIR = Path(__file__).resolve().parent
ETL3_ROOT = CONFIG_DIR.parent

# Populate os.environ from .env without overriding an already-exported value.
load_dotenv(CONFIG_DIR / ".env", override=False)
_env = dotenv_values(CONFIG_DIR / ".env")

# Destination (read/write) -- the ONLY database ETL-3 ever writes to.
# Parallel enhanced build uses dopams_cctns_v2; baseline dopams_cctns stays
# read-only / untouched by this phase.
ALLOWED_UNIFIED_DBNAMES = frozenset({"dopams_cctns", "dopams_cctns_v2"})
_unified_dbname = (_env.get("UNIFIED_PG_DATABASE") or "").strip()
if _unified_dbname not in ALLOWED_UNIFIED_DBNAMES:
    raise RuntimeError(
        f"UNIFIED_PG_DATABASE must be one of {sorted(ALLOWED_UNIFIED_DBNAMES)}, "
        f"got {_unified_dbname!r}"
    )

UNIFIED_DB = {
    "host": _env["UNIFIED_PG_HOST"],
    "port": _env["UNIFIED_PG_PORT"],
    "dbname": _unified_dbname,
    "user": _env["UNIFIED_PG_USER"],
    "password": _env["UNIFIED_PG_PASSWORD"],
}
UNIFIED_SCHEMA = _env.get("UNIFIED_PG_SCHEMA", "public")

# Asserted by db/connections.py against current_database(). Allowlist above
# prevents writes to V1/V2/dev-2/any other database via a mistyped .env.
EXPECTED_UNIFIED_DBNAME = _unified_dbname

# Sources (read-only) -- paths to the existing V1/V2 ETL .env files, resolved
# relative to the etl3/ directory.
V1_SOURCE_ENV_PATH = (ETL3_ROOT / _env["V1_SOURCE_ENV_PATH"]).resolve()
V2_SOURCE_ENV_PATH = (ETL3_ROOT / _env["V2_SOURCE_ENV_PATH"]).resolve()

EXPECTED_V1_DBNAME = "cctns_v1"
EXPECTED_V2_DBNAME = "cctns-v2"


def _env_get(*names: str) -> str:
    """Resolve a config value from the process environment, then .env file.

    If a name is present in ``os.environ`` (even as an empty string), that
    wins — so tests can clear a variable without the .env file resurrecting
    it. Hardcoded host/model fallbacks are never used.
    """
    for name in names:
        if name in os.environ:
            return str(os.environ.get(name) or "").strip()
    for name in names:
        raw = _env.get(name) if _env else None
        if raw is not None and str(raw).strip():
            return str(raw).strip()
    return ""


def ai_enabled() -> bool:
    return _env_get("ETL3_AI_ENABLED").lower() in ("1", "true", "yes")


def ollama_base_url() -> str:
    """Configured Ollama base URL, or '' if unset. No hardcoded fallback."""
    return _env_get("OLLAMA_BASE_URL", "OLLAMA_HOST").rstrip("/")


def ollama_model() -> str:
    """Configured Ollama model, or '' if unset. No hardcoded fallback."""
    return _env_get("OLLAMA_MODEL", "LLM_MODEL_EXTRACTION")


def ai_mode() -> str:
    """AI workload mode: ``limited`` (diagnostic) or ``backfill`` (production).

    ``limited`` honours ETL3_AI_LIMIT as a per-invocation cap (tests / diagnostics).
    ``backfill`` ignores ETL3_AI_LIMIT and processes the full eligible backlog in
    bounded batches (ETL3_AI_BATCH_SIZE). Default is ``backfill`` so production
    is never silently capped by a leftover test limit.
    """
    raw = (_env_get("ETL3_AI_MODE") or "backfill").strip().lower()
    if raw in ("limited", "limit", "test", "diagnostic"):
        return "limited"
    if raw in ("backfill", "full", "production", "prod"):
        return "backfill"
    raise RuntimeError(
        f"ETL3_AI_MODE must be 'limited' or 'backfill', got {raw!r}"
    )


def resolve_ai_settings() -> dict:
    """Single source of truth for enrichment AI / Ollama settings.

    When ETL3_AI_ENABLED is on, OLLAMA_BASE_URL and OLLAMA_MODEL are required.
    When AI is off, Ollama values may be empty and no connection is attempted.

    Limit semantics:
      - mode=limited  → ETL3_AI_LIMIT caps new model calls per invocation (0=unlimited)
      - mode=backfill → ETL3_AI_LIMIT is ignored; ETL3_AI_BATCH_SIZE bounds
        commit/progress batches only
    """
    enabled = ai_enabled()
    host = ollama_base_url()
    model = ollama_model()
    if enabled:
        if not host:
            raise RuntimeError(
                "OLLAMA_BASE_URL is required when ETL3_AI_ENABLED=1 "
                "(no hardcoded endpoint fallback)"
            )
        if not model:
            raise RuntimeError(
                "OLLAMA_MODEL is required when ETL3_AI_ENABLED=1 "
                "(no hardcoded model fallback)"
            )
    timeout_raw = _env_get("LLM_TIMEOUT") or "300"
    limit_raw = _env_get("ETL3_AI_LIMIT") or "0"
    batch_raw = _env_get("ETL3_AI_BATCH_SIZE") or "25"
    retries_raw = _env_get("ETL3_AI_MAX_RETRIES") or "2"
    mode = ai_mode()
    limit = int(limit_raw)
    if mode == "backfill":
        # Production historical workload must not be capped by the test limit.
        limit = 0
    return {
        "enabled": enabled,
        "host": host,
        "model": model,
        "timeout": int(timeout_raw),
        "mode": mode,
        "limit": limit,
        "batch_size": max(1, int(batch_raw)),
        "max_retries": max(0, int(retries_raw)),
    }


# Snapshots for callers that only need the current configured values
# (empty string when unset — never a silent default host/model).
OLLAMA_BASE_URL = ollama_base_url()
OLLAMA_MODEL = ollama_model()
ETL3_AI_ENABLED = ai_enabled()
