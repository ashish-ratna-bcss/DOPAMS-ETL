"""Preflight for historical AI backfill. Exit non-zero on failure.

Checks: Ollama reachability + model, target DB identity, source RO,
resumable backfill mode, backlog status. Does not start the backfill.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.config import settings as cfg
from etl3.db import connections
from etl3.enrichment.ai import ai_settings
from etl3.run_ai_backfill import _ensure_backfill_mode, print_status


def _fail(msg: str) -> None:
    print(f"PREFLIGHT_FAIL: {msg}", flush=True)
    sys.exit(1)


def check_ollama(host: str, model: str) -> dict:
    tags_url = host.rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(tags_url, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        _fail(f"Ollama unreachable at {host}: {exc}")
    names = []
    for m in payload.get("models") or []:
        name = m.get("name") or m.get("model")
        if name:
            names.append(name)
    # Accept exact or tag-less match (qwen3:8b vs qwen3:8b)
    ok = model in names or any(n.split(":")[0] == model.split(":")[0] and n == model for n in names)
    if model not in names:
        # still allow if listed with same full name
        _fail(f"model {model!r} not in Ollama tags; available={names[:20]}")
    # lightweight generate ping
    gen_url = host.rstrip("/") + "/api/generate"
    body = json.dumps({"model": model, "prompt": "ping", "stream": False, "options": {"num_predict": 1}}).encode()
    req = urllib.request.Request(gen_url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            gen = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        _fail(f"Ollama generate ping failed: {exc}")
    return {"tags_ok": True, "model": model, "models_sample": names[:10], "generate_ok": "response" in gen or "done" in gen}


def check_db() -> dict:
    if cfg.EXPECTED_UNIFIED_DBNAME != "dopams_cctns_v2":
        _fail(f"EXPECTED_UNIFIED_DBNAME={cfg.EXPECTED_UNIFIED_DBNAME!r}")
    tg = connections.get_unified_connection()
    cur = tg.cursor()
    cur.execute("SELECT current_database(), current_user")
    dbname, user = cur.fetchone()
    if dbname != "dopams_cctns_v2":
        tg.close()
        _fail(f"connected to {dbname!r}, expected dopams_cctns_v2")
    out = {"database": dbname, "user": user}
    for label, factory, probe in (
        ("V1", connections.get_v1_source_connection, "SELECT current_database()"),
        ("V2", connections.get_v2_source_connection, "SELECT current_database()"),
    ):
        c = factory()
        ccur = c.cursor()
        ccur.execute(probe)
        out[f"{label}_db"] = ccur.fetchone()[0]
        try:
            if label == "V1":
                ccur.execute("UPDATE cctns.cctns_media_files SET status=status WHERE false")
            else:
                ccur.execute(
                    "UPDATE file_media_bookkeeping SET is_downloaded=is_downloaded WHERE false"
                )
            c.commit()
            out[f"{label}_ro"] = "UNEXPECTED_WRITE"
            _fail(f"{label} source is writable")
        except Exception as exc:
            c.rollback()
            out[f"{label}_ro"] = f"rejected:{type(exc).__name__}"
        c.close()
    # AI audit tables present
    for table in ("ai_extraction_attempts", "drug_extractions", "enrichment_run_log"):
        cur.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public' AND table_name=%s",
            (table,),
        )
        if cur.fetchone()[0] == 0:
            tg.close()
            _fail(f"missing table {table}")
    cur.execute("SELECT COUNT(*) FROM ai_extraction_attempts")
    out["ai_attempts"] = cur.fetchone()[0]
    cur.execute("SELECT COUNT(DISTINCT input_hash) FROM ai_extraction_attempts WHERE input_hash IS NOT NULL")
    out["distinct_input_hashes"] = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM drug_extractions")
    out["drug_extractions"] = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM crimes_unified")
    out["crimes_unified"] = cur.fetchone()[0]
    # KB intact
    cur.execute("SELECT COUNT(*) FROM kb.drug_categories")
    out["kb_drug_categories"] = cur.fetchone()[0]
    tg.close()
    return out


def check_mode() -> dict:
    _ensure_backfill_mode()
    ai = ai_settings()
    if not ai["enabled"]:
        _fail("AI not enabled after _ensure_backfill_mode")
    if ai["mode"] != "backfill":
        _fail(f"mode={ai['mode']!r} expected backfill")
    if ai["limit"] not in (0, None):
        _fail(f"backfill should ignore limit; got limit={ai['limit']!r}")
    if ai["host"] != os.environ.get("OLLAMA_BASE_URL", ai["host"]):
        pass
    expected_host = "http://10.12.1.124:11434"
    expected_model = "qwen3:8b"
    if ai["host"].rstrip("/") != expected_host:
        _fail(f"host={ai['host']!r} expected {expected_host}")
    if ai["model"] != expected_model:
        _fail(f"model={ai['model']!r} expected {expected_model}")
    return ai


def main() -> None:
    report = {"ok": False}
    report["mode"] = check_mode()
    report["ollama"] = check_ollama(report["mode"]["host"], report["mode"]["model"])
    report["db"] = check_db()
    conn = connections.get_unified_connection(readonly=True)
    try:
        report["status"] = print_status(conn)
    finally:
        conn.close()
    report["ok"] = True
    print(json.dumps(report, indent=2, default=str))
    print("PREFLIGHT_OK", flush=True)


if __name__ == "__main__":
    main()
