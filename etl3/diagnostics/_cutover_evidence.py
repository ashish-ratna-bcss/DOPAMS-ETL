#!/usr/bin/env python3
"""Read-only evidence that per-FIR interleaving is happening."""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, "/home/eagle/dopams-cctns-ai")
os.environ.setdefault("ETL3_AI_ENABLED", "1")
os.environ.setdefault("ETL3_AI_MODE", "backfill")
os.environ.setdefault("OLLAMA_BASE_URL", "http://10.12.1.124:11434")
os.environ.setdefault("OLLAMA_MODEL", "qwen3:8b")

from etl3.db import connections

LOG = Path(sys.argv[1] if len(sys.argv) > 1 else "logs/ai_backfill_latest.log")
RUN_ID = sys.argv[2] if len(sys.argv) > 2 else "7050521b-1fe9-41c6-ad3d-61bf17074de4"


def snap():
    conn = connections.get_unified_connection(readonly=True)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT status, COUNT(*) FROM ai_extraction_attempts
                GROUP BY 1 ORDER BY 1
                """
            )
            attempts = dict(cur.fetchall())
            cur.execute(
                "SELECT COUNT(*) FROM drug_extractions WHERE provenance='etl3_ai'"
            )
            drugs = cur.fetchone()[0]
            cur.execute(
                """
                SELECT COUNT(*) FROM accused_enrichment
                WHERE field_sources::text LIKE '%%LLM_FALLBACK%%'
                """
            )
            accused_llm = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM kb.drug_categories")
            kb = cur.fetchone()[0]
            cur.execute(
                """
                SELECT COUNT(*) FROM ai_extraction_attempts
                WHERE enrichment_run_id::text = %s OR true
                """,
                (RUN_ID,),
            )
            # Prefer attempts recorded after process start via computed_at if available
            cur.execute(
                """
                SELECT COUNT(*) FROM ai_extraction_attempts a
                WHERE a.created_at >= now() - interval '30 minutes'
                """
            )
            try:
                recent = cur.fetchone()[0]
            except Exception:
                conn.rollback()
                cur.execute(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name='ai_extraction_attempts'
                    """
                )
                cols = [r[0] for r in cur.fetchall()]
                recent = {"columns": cols}
        return {
            "attempts": attempts,
            "ai_drug_rows": drugs,
            "accused_llm_rows": accused_llm,
            "kb_drug_categories": kb,
            "recent_meta": recent,
        }
    finally:
        conn.close()


def main():
    deadline = time.time() + 600
    last_text = ""
    evidence = {
        "drug_progress": False,
        "accused_progress": False,
        "per_fir_banner": False,
        "think_off": False,
    }
    while time.time() < deadline:
        text = LOG.read_text(errors="replace") if LOG.exists() else ""
        if "ai-per-fir" in text:
            evidence["per_fir_banner"] = True
        if "think=False" in text or "payload_think_key=False" in text:
            evidence["think_off"] = True
        if re.search(r"\[ai-per-fir\] drug processed=", text):
            evidence["drug_progress"] = True
        if re.search(r"\[ai-per-fir\] accused processed=", text):
            evidence["accused_progress"] = True
        if evidence["drug_progress"] and evidence["accused_progress"]:
            break
        if text != last_text:
            # print newest meaningful lines
            for line in text.splitlines()[-8:]:
                if "ai-per-fir" in line or "ollama" in line or "error" in line.lower():
                    print(line, flush=True)
            last_text = text
        time.sleep(15)

    db = snap()
    out = {"evidence": evidence, "db": db, "log_tail": LOG.read_text(errors="replace").splitlines()[-50:]}
    Path("/tmp/ai_backfill_cutover_evidence.json").write_text(
        json.dumps(out, indent=2, default=str), encoding="utf-8"
    )
    print(json.dumps({"evidence": evidence, "db": db}, indent=2, default=str), flush=True)
    if not (evidence["per_fir_banner"] and evidence["think_off"]):
        raise SystemExit(2)
    if not (evidence["drug_progress"] and evidence["accused_progress"]):
        print("PARTIAL: waiting for both drug and accused progress lines", flush=True)
        raise SystemExit(3)
    print("INTERLEAVE_OK", flush=True)


if __name__ == "__main__":
    main()
