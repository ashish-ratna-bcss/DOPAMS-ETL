"""Ensure AI/Ollama keys exist in etl3/config/.env without printing secrets."""
from __future__ import annotations

from pathlib import Path

ENV_PATH = Path("/home/eagle/dopams-cctns-ai/etl3/config/.env")
UPDATES = {
    "OLLAMA_BASE_URL": "http://10.12.1.124:11434",
    "OLLAMA_MODEL": "qwen3:8b",
    "ETL3_AI_ENABLED": "1",
    "ETL3_AI_MODE": "backfill",
    "ETL3_AI_LIMIT": "0",
    "ETL3_AI_BATCH_SIZE": "5",
    "ETL3_AI_MAX_RETRIES": "2",
    "ETL3_AI_REQUEST_DELAY_SEC": "3",
    "ETL3_AI_HEALTH_COOLDOWN_SEC": "60",
    "OLLAMA_THINK": "0",
    "LLM_TIMEOUT": "180",
    "UNIFIED_PG_DATABASE": "dopams_cctns_v2",
}


def main() -> None:
    text = ENV_PATH.read_text(encoding="utf-8") if ENV_PATH.exists() else ""
    lines = text.splitlines()
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            out.append(line)
            continue
        key, _val = line.split("=", 1)
        if key in UPDATES:
            out.append(f"{key}={UPDATES[key]}")
            seen.add(key)
        else:
            out.append(line)
    for key, val in UPDATES.items():
        if key not in seen:
            out.append(f"{key}={val}")
    # Drop accidental duplicate keys (keep first occurrence).
    deduped: list[str] = []
    seen_keys: set[str] = set()
    for line in out:
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key = line.split("=", 1)[0]
            if key in seen_keys:
                continue
            seen_keys.add(key)
        deduped.append(line)
    ENV_PATH.write_text("\n".join(deduped) + "\n", encoding="utf-8")
    ENV_PATH.chmod(0o600)
    print("updated:")
    for key in sorted(UPDATES):
        print(f"{key}={UPDATES[key]}")


if __name__ == "__main__":
    main()
