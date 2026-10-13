#!/usr/bin/env python3
"""Confirm cutover behavior with RO status + attempt deltas."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, "/home/eagle/dopams-cctns-ai")
for k, v in {
    "ETL3_AI_ENABLED": "1",
    "ETL3_AI_MODE": "backfill",
    "ETL3_AI_LIMIT": "0",
    "ETL3_AI_BATCH_SIZE": "5",
    "ETL3_AI_MAX_RETRIES": "2",
    "ETL3_AI_REQUEST_DELAY_SEC": "3",
    "ETL3_AI_HEALTH_COOLDOWN_SEC": "60",
    "OLLAMA_THINK": "0",
    "LLM_TIMEOUT": "180",
    "OLLAMA_BASE_URL": "http://10.12.1.124:11434",
    "OLLAMA_MODEL": "qwen3:8b",
}.items():
    os.environ.setdefault(k, v)

from etl3.db import connections
from etl3.run_ai_backfill import print_status

LOG = Path("/home/eagle/dopams-cctns-ai/logs/ai_backfill_20261010T084414Z.log")


def attempt_breakdown(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT status, COALESCE(validation_status,''), COUNT(*)
            FROM ai_extraction_attempts
            GROUP BY 1,2 ORDER BY 1,2
            """
        )
        return [{"status": s, "validation_status": v, "count": n} for s, v, n in cur.fetchall()]


def extra(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM drug_extractions WHERE provenance='etl3_ai'")
        drugs = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM kb.drug_categories")
        kb = cur.fetchone()[0]
        cur.execute(
            """
            SELECT COUNT(*) FROM accused_enrichment
            WHERE field_sources::text LIKE '%%LLM_FALLBACK%%'
            """
        )
        accused_llm = cur.fetchone()[0]
        # Recent accused digests vs drug digests: accused hash kind is in payload via input
        # We approximate by counting attempts that are not success/empty/invalid for drugs
        # Use source_module + presence in accused_enrichment updates if available.
        cur.execute(
            """
            SELECT COUNT(*) FROM ai_extraction_attempts
            WHERE status IN ('success','empty')
            """
        )
        settledish = cur.fetchone()[0]
    return {
        "ai_drug_rows": drugs,
        "kb_drug_categories": kb,
        "accused_llm_rows": accused_llm,
        "success_or_empty_attempts": settledish,
    }


def main():
    # Wait until log shows either drug progress or accused>=15
    deadline = time.time() + 180
    while time.time() < deadline:
        text = LOG.read_text(errors="replace") if LOG.exists() else ""
        accused_n = 0
        drug_n = 0
        for line in text.splitlines():
            if "[ai-per-fir] accused processed=" in line:
                try:
                    accused_n = int(line.split("processed=")[1].split()[0])
                except Exception:
                    pass
            if "[ai-per-fir] drug processed=" in line:
                try:
                    drug_n = int(line.split("processed=")[1].split()[0])
                except Exception:
                    pass
        print(f"log_counters drug_processed={drug_n} accused_processed={accused_n}", flush=True)
        if accused_n >= 15 or drug_n >= 5:
            break
        time.sleep(20)

    conn = connections.get_unified_connection(readonly=True)
    try:
        status = print_status(conn)
        out = {
            "status": status,
            "attempts": attempt_breakdown(conn),
            "extra": extra(conn),
            "log_ai_lines": [
                ln for ln in LOG.read_text(errors="replace").splitlines()
                if "ai-per-fir" in ln or "[ollama]" in ln or "schedule=" in ln
            ],
        }
    finally:
        conn.close()

    Path("/tmp/ai_backfill_cutover_confirm.json").write_text(
        json.dumps(out, indent=2, default=str), encoding="utf-8"
    )
    print("WROTE /tmp/ai_backfill_cutover_confirm.json", flush=True)
    # Hard asserts for cutover success criteria
    assert status.get("accused_settled", 0) > 15, status
    assert status.get("host") == "http://10.12.1.124:11434"
    assert status.get("model") == "qwen3:8b"
    assert out["extra"]["kb_drug_categories"] == 379
    print("CONFIRM_OK", flush=True)


if __name__ == "__main__":
    main()
