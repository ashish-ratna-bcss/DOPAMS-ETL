"""Local KB schema, lookup semantics, Ollama config, and runtime independence.

Run: python etl3/tests/test_kb_local.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.config import settings
from etl3.db import connections
from etl3.enrichment.ai import AIExtractionError, ai_settings, parse_drug_response
from etl3.enrichment.address import load_geo_kb
from etl3.enrichment.kb import (
    DrugKB,
    SIMILARITY_THRESHOLD,
    compare_static_mappings,
    load_drug_kb,
    resolve_primary_name,
)
from etl3.enrichment.rules import classify_domicile


def check(label, fn):
    try:
        fn()
        print(f"[PASS] {label}")
    except Exception as exc:
        print(f"[FAIL] {label}: {type(exc).__name__}: {exc}")
        raise


def test_write_target_is_v2():
    assert settings.EXPECTED_UNIFIED_DBNAME == "dopams_cctns_v2"
    conn = connections.get_unified_connection(readonly=True)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database()")
            assert cur.fetchone()[0] == "dopams_cctns_v2"
    finally:
        conn.close()


def test_kb_schema_isolation():
    conn = connections.get_unified_connection(readonly=True)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_schema, table_name
                FROM information_schema.tables
                WHERE table_name IN (
                    'drug_categories','drug_ignore_list','geo_reference','geo_countries'
                )
                ORDER BY 1,2
                """
            )
            rows = cur.fetchall()
        assert rows, "KB tables missing"
        assert all(schema == "kb" for schema, _ in rows), rows
        assert {t for _, t in rows} == {
            "drug_categories", "drug_ignore_list", "geo_reference", "geo_countries",
        }
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1 FROM pg_views
                WHERE schemaname='public'
                  AND viewname IN (
                    'drug_categories','drug_ignore_list','geo_reference','geo_countries'
                  )
                """
            )
            assert cur.fetchone() is None
    finally:
        conn.close()


def test_row_counts_match_dev2():
    src = connections.get_v2_source_connection()
    # reopen as read-only to dev-2 using same host
    from dotenv import dotenv_values
    import psycopg2
    e = dotenv_values(settings.V2_SOURCE_ENV_PATH)
    dev2 = psycopg2.connect(
        host=e["POSTGRES_HOST"], port=e["POSTGRES_PORT"], dbname="dev-2",
        user=e["POSTGRES_USER"], password=e["POSTGRES_PASSWORD"],
        options="-c default_transaction_read_only=on",
    )
    dev2.set_session(readonly=True, autocommit=True)
    dst = connections.get_unified_connection(readonly=True)
    try:
        for table in (
            "drug_categories", "drug_ignore_list", "geo_reference", "geo_countries",
        ):
            with dev2.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) FROM public.{table}")
                a = cur.fetchone()[0]
            with dst.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) FROM kb.{table}")
                b = cur.fetchone()[0]
            assert a == b, f"{table}: dev-2={a} kb={b}"
    finally:
        src.close()
        dev2.close()
        dst.close()


def test_pg_trgm_and_lookups():
    conn = connections.get_unified_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT extname FROM pg_extension WHERE extname='pg_trgm'")
            assert cur.fetchone(), "pg_trgm missing"
            cur.execute(
                """
                SELECT indexname FROM pg_indexes
                WHERE schemaname='kb' AND tablename='drug_categories'
                  AND indexdef ILIKE '%gin_trgm_ops%'
                """
            )
            assert cur.fetchone(), "missing trigram index on kb.drug_categories.raw_name"

        kb = load_drug_kb(conn, include_static_extras=False)
        # exact
        name, tier = resolve_primary_name("ganja", kb.items, drug_kb=kb)
        assert name == "Ganja" and tier == "exact"
        # substring
        name, tier = resolve_primary_name("floating dry ganja packet", kb.items, drug_kb=kb)
        assert name == "Ganja" and "substring" in tier
        # verified trigram (misspelling that does not hit exact/substring first)
        name, tier = resolve_primary_name("heroien", kb.items, drug_kb=kb)
        assert name == "Heroin" and tier == "pgtrgm"
        # ganza is present as KB key "dry ganza" so substring wins before trgm
        name, tier = resolve_primary_name("ganza", kb.items, drug_kb=kb)
        assert name == "Ganja" and tier in ("substring_raw_in_kb", "pgtrgm")
        # below threshold / nonsense
        name, tier = resolve_primary_name("zzzznotadrugqqq", kb.items, drug_kb=kb)
        assert name is None and tier == "none"
        assert SIMILARITY_THRESHOLD == 0.35
        # NULL/empty
        assert resolve_primary_name(None, kb.items, drug_kb=kb) == (None, "none")
        assert resolve_primary_name("", kb.items, drug_kb=kb) == (None, "none")

        # unverified rows must not participate in similarity
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO kb.drug_categories (raw_name, standard_name, category_group, is_verified)
                VALUES ('zzunverifiedfuzzyxx', 'ShouldNotWin', 'Other', false)
                ON CONFLICT (raw_name) DO UPDATE
                SET is_verified = false, standard_name = 'ShouldNotWin'
                """
            )
        conn.commit()
        hit = kb.fuzzy_match("zzunverifiedfuzzyxx")
        assert hit is None
        with conn.cursor() as cur:
            cur.execute("DELETE FROM kb.drug_categories WHERE raw_name='zzunverifiedfuzzyxx'")
        conn.commit()

        # ignore list exact on primary
        assert kb.is_ignored("unknown") is True
        assert kb.is_ignored("Ganja") is False
        assert kb.is_ignored(None) is False
    finally:
        conn.close()


def test_geography_and_domicile():
    conn = connections.get_unified_connection(readonly=True)
    try:
        geo = load_geo_kb(conn, schema="kb")
        assert geo.loaded
        assert classify_domicile("Telangana", "India", None, None) == "native state"
        assert classify_domicile("Karnataka", "India", None, None) == "inter state"
        assert classify_domicile(None, "United States", None, None) == "international"
        assert classify_domicile(None, None, None, None) is None
        assert classify_domicile("Telangana", None, None, None) is None
    finally:
        conn.close()


def test_static_mapping_precedence_documented():
    conn = connections.get_unified_connection(readonly=True)
    try:
        report = compare_static_mappings(conn)
        assert "kb.drug_categories wins" in report["precedence"]
        print(
            f"       static conflicts={len(report['conflicts'])} "
            f"extras={len(report['static_only_extras'])}"
        )
    finally:
        conn.close()


def test_runtime_has_no_dev2_dependency():
    """Enrichment runtime modules must not connect to or query dev-2."""
    root = Path(__file__).resolve().parents[1]  # etl3/
    runtime_paths = [
        root / "enrichment",
        root / "run_enrichment.py",
        root / "db" / "connections.py",
        root / "config" / "settings.py",
    ]
    bad = []
    for base in runtime_paths:
        paths = [base] if base.is_file() else list(base.rglob("*.py"))
        for path in paths:
            text = path.read_text(encoding="utf-8", errors="ignore")
            for i, line in enumerate(text.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                    continue
                if 'dbname="dev-2"' in line or "dbname='dev-2'" in line:
                    bad.append(f"{path}:{i}:{stripped}")
                if "DATABASE_URL" in line and "os.environ" in line:
                    bad.append(f"{path}:{i}:{stripped}")
            if "public.drug_categories" in text:
                bad.append(f"{path}: queries public.drug_categories")
            if "FROM drug_categories" in text and "kb.drug_categories" not in text:
                bad.append(f"{path}: unqualified drug_categories query")
    assert not bad, "runtime still references forbidden sources:\n" + "\n".join(bad[:20])


def test_ollama_settings_and_endpoint():
    """AI settings follow the environment; no hardcoded host/model in code."""
    configured_base = "http://example-test-host:11434"
    configured_model = "test-model-from-env"
    with mock.patch.dict(os.environ, {
        "ETL3_AI_ENABLED": "0",
        "OLLAMA_BASE_URL": configured_base,
        "OLLAMA_MODEL": configured_model,
        "OLLAMA_HOST": "",
        "LLM_MODEL_EXTRACTION": "",
    }, clear=False):
        cfg = ai_settings()
        assert cfg["host"] == configured_base
        assert cfg["model"] == configured_model
        assert cfg["enabled"] is False
    # Live tags probe uses whatever OLLAMA_BASE_URL is currently configured
    # in the process environment / .env (soft — labs may be offline).
    from etl3.config.settings import ollama_base_url, ollama_model
    live_base = ollama_base_url()
    live_model = ollama_model()
    if live_base and live_model:
        try:
            with urllib.request.urlopen(f"{live_base}/api/tags", timeout=15) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            names = {m["name"] for m in payload.get("models", [])}
            if live_model not in names:
                print(f"[WARN] configured model {live_model!r} not in tags at {live_base}: {sorted(names)[:12]}")
        except Exception as exc:
            print(f"[WARN] Ollama tags unreachable at {live_base}: {exc}")


def test_ai_validation_rejects_malformed():
    try:
        parse_drug_response("not-json")
        raise AssertionError("expected invalid")
    except AIExtractionError as exc:
        assert exc.status == "invalid"
    empty = parse_drug_response('{"drugs":[]}')
    assert empty["drugs"] == []
    # null / missing raw_drug_name is skipped (generic / unnamed), not a schema failure
    skipped = parse_drug_response(
        '{"drugs":[{"raw_drug_name":null,"raw_quantity":1,"raw_unit":"g","drug_form":"solid"}]}'
    )
    assert skipped["drugs"] == []
    skipped2 = parse_drug_response(
        '{"drugs":[{"raw_quantity":1,"raw_unit":"g","drug_form":"solid"}]}'
    )
    assert skipped2["drugs"] == []


def main():
    check("write target dopams_cctns_v2", test_write_target_is_v2)
    check("kb schema isolation", test_kb_schema_isolation)
    check("row counts match dev-2", test_row_counts_match_dev2)
    check("pg_trgm + drug lookups", test_pg_trgm_and_lookups)
    check("geography + domicile rules", test_geography_and_domicile)
    check("static mapping precedence", test_static_mapping_precedence_documented)
    check("no runtime dev-2 dependency", test_runtime_has_no_dev2_dependency)
    check("ollama settings + endpoint", test_ollama_settings_and_endpoint)
    check("ai validation failure handling", test_ai_validation_rejects_malformed)
    print("ALL KB LOCAL CHECKS PASSED")


if __name__ == "__main__":
    main()
