"""Ollama configuration must come only from the environment — no hardcoded fallbacks."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.config import settings as cfg
from etl3.enrichment.ai import OllamaAccusedClient, OllamaDrugClient, ai_settings


def _clear_ollama_env():
    return mock.patch.dict(
        os.environ,
        {
            "ETL3_AI_ENABLED": "0",
            "OLLAMA_BASE_URL": "",
            "OLLAMA_HOST": "",
            "OLLAMA_MODEL": "",
            "LLM_MODEL_EXTRACTION": "",
        },
        clear=False,
    )


def test_configured_endpoint_and_model():
    base = "http://10.12.1.124:11434"
    model = "qwen3:8b"
    with mock.patch.dict(os.environ, {
        "ETL3_AI_ENABLED": "1",
        "OLLAMA_BASE_URL": base,
        "OLLAMA_MODEL": model,
        "OLLAMA_HOST": "",
        "LLM_MODEL_EXTRACTION": "",
    }, clear=False):
        resolved = ai_settings()
        assert resolved["enabled"] is True
        assert resolved["host"] == base
        assert resolved["model"] == model
        client = OllamaDrugClient(resolved["host"], resolved["model"], resolved["timeout"])
        assert client.host == base.rstrip("/")
        assert client.model == model


def test_alternate_endpoint_is_honored():
    base = "http://example-test-host:1234"
    model = "test-model"
    with mock.patch.dict(os.environ, {
        "ETL3_AI_ENABLED": "1",
        "OLLAMA_BASE_URL": base,
        "OLLAMA_MODEL": model,
        "OLLAMA_HOST": "",
        "LLM_MODEL_EXTRACTION": "",
    }, clear=False):
        resolved = ai_settings()
        assert resolved["host"] == base
        assert resolved["model"] == model
        drug = OllamaDrugClient(resolved["host"], resolved["model"], 30)
        accused = OllamaAccusedClient(resolved["host"], resolved["model"], 30)
        assert drug.host == base
        assert drug.model == model
        assert accused.host == base
        assert accused.model == model
        # No network call — construction only.


def test_missing_endpoint_fails_when_ai_enabled():
    with mock.patch.dict(os.environ, {
        "ETL3_AI_ENABLED": "1",
        "OLLAMA_BASE_URL": "",
        "OLLAMA_HOST": "",
        "OLLAMA_MODEL": "any-model",
        "LLM_MODEL_EXTRACTION": "",
    }, clear=False):
        try:
            ai_settings()
            raise AssertionError("expected RuntimeError for missing OLLAMA_BASE_URL")
        except RuntimeError as exc:
            assert "OLLAMA_BASE_URL" in str(exc)
            assert "fallback" in str(exc).lower()


def test_missing_model_fails_when_ai_enabled():
    with mock.patch.dict(os.environ, {
        "ETL3_AI_ENABLED": "1",
        "OLLAMA_BASE_URL": "http://example-test-host:1234",
        "OLLAMA_HOST": "",
        "OLLAMA_MODEL": "",
        "LLM_MODEL_EXTRACTION": "",
    }, clear=False):
        try:
            ai_settings()
            raise AssertionError("expected RuntimeError for missing OLLAMA_MODEL")
        except RuntimeError as exc:
            assert "OLLAMA_MODEL" in str(exc)
            assert "fallback" in str(exc).lower()


def test_ai_disabled_does_not_require_ollama():
    with mock.patch.dict(os.environ, {
        "ETL3_AI_ENABLED": "0",
        "OLLAMA_BASE_URL": "",
        "OLLAMA_HOST": "",
        "OLLAMA_MODEL": "",
        "LLM_MODEL_EXTRACTION": "",
    }, clear=False):
        resolved = ai_settings()
        assert resolved["enabled"] is False
        assert resolved["host"] == ""
        assert resolved["model"] == ""
        # Deterministic path only needs settings resolution — no Ollama client.


def test_endpoint_change_without_code_change():
    with mock.patch.dict(os.environ, {
        "ETL3_AI_ENABLED": "1",
        "OLLAMA_BASE_URL": "http://host-a:1111",
        "OLLAMA_MODEL": "model-a",
        "OLLAMA_HOST": "",
        "LLM_MODEL_EXTRACTION": "",
    }, clear=False):
        first = ai_settings()
    with mock.patch.dict(os.environ, {
        "ETL3_AI_ENABLED": "1",
        "OLLAMA_BASE_URL": "http://host-b:2222",
        "OLLAMA_MODEL": "model-b",
        "OLLAMA_HOST": "",
        "LLM_MODEL_EXTRACTION": "",
    }, clear=False):
        second = ai_settings()
    assert first["host"] != second["host"]
    assert first["model"] != second["model"]
    assert second["host"] == "http://host-b:2222"
    assert second["model"] == "model-b"


def test_no_hardcoded_runtime_fallback_in_settings_source():
    text = Path(cfg.__file__).read_text(encoding="utf-8")
    for needle in (
        "10.12.1.124",
        "192.168.102.21",
        "qwen3:8b",
        "qwen2.5:32b-instruct-q4_K_M",
        "localhost:11434",
        "127.0.0.1:11434",
    ):
        assert needle not in text, f"settings.py still contains {needle!r}"


def test_no_hardcoded_runtime_fallback_in_ai_source():
    ai_path = Path(__file__).resolve().parent.parent / "enrichment" / "ai.py"
    text = ai_path.read_text(encoding="utf-8")
    for needle in (
        "10.12.1.124",
        "192.168.102.21",
        "qwen3:8b",
        "qwen2.5:32b-instruct-q4_K_M",
        "localhost:11434",
        "127.0.0.1:11434",
    ):
        assert needle not in text, f"ai.py still contains {needle!r}"


def test_backfill_mode_ignores_ai_limit():
    with mock.patch.dict(os.environ, {
        "ETL3_AI_ENABLED": "1",
        "OLLAMA_BASE_URL": "http://example-test-host:1234",
        "OLLAMA_MODEL": "test-model",
        "OLLAMA_HOST": "",
        "LLM_MODEL_EXTRACTION": "",
        "ETL3_AI_MODE": "backfill",
        "ETL3_AI_LIMIT": "5",
        "ETL3_AI_BATCH_SIZE": "5",
        "ETL3_AI_REQUEST_DELAY_SEC": "3",
        "ETL3_AI_HEALTH_COOLDOWN_SEC": "60",
        "LLM_TIMEOUT": "180",
    }, clear=False):
        resolved = ai_settings()
        assert resolved["mode"] == "backfill"
        assert resolved["limit"] == 0
        assert resolved["batch_size"] == 5
        assert resolved["request_delay_sec"] == 3.0
        assert resolved["health_cooldown_sec"] == 60.0
        assert resolved["timeout"] == 180


def test_limited_mode_honours_ai_limit():
    with mock.patch.dict(os.environ, {
        "ETL3_AI_ENABLED": "1",
        "OLLAMA_BASE_URL": "http://example-test-host:1234",
        "OLLAMA_MODEL": "test-model",
        "OLLAMA_HOST": "",
        "LLM_MODEL_EXTRACTION": "",
        "ETL3_AI_MODE": "limited",
        "ETL3_AI_LIMIT": "5",
        "ETL3_AI_BATCH_SIZE": "10",
    }, clear=False):
        resolved = ai_settings()
        assert resolved["mode"] == "limited"
        assert resolved["limit"] == 5
        assert resolved["batch_size"] == 10


def main():
    tests = [
        test_configured_endpoint_and_model,
        test_alternate_endpoint_is_honored,
        test_missing_endpoint_fails_when_ai_enabled,
        test_missing_model_fails_when_ai_enabled,
        test_ai_disabled_does_not_require_ollama,
        test_endpoint_change_without_code_change,
        test_no_hardcoded_runtime_fallback_in_settings_source,
        test_no_hardcoded_runtime_fallback_in_ai_source,
        test_backfill_mode_ignores_ai_limit,
        test_limited_mode_honours_ai_limit,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"OK  {fn.__name__}")
        except Exception as exc:
            failed += 1
            print(f"FAIL {fn.__name__}: {exc}")
    if failed:
        raise SystemExit(failed)
    print("ALL OLLAMA CONFIG TESTS PASSED")


if __name__ == "__main__":
    main()
