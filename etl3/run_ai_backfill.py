"""Production AI historical backfill for ETL-3 (drugs + accused).

Resumes from ai_extraction_attempts / input hashes. Does not reset the
target, does not re-run merge, and does not write to V1/V2.

Usage (repo root):
    python etl3/run_ai_backfill.py
    python etl3/run_ai_backfill.py --drugs-only
    python etl3/run_ai_backfill.py --accused-only
    python etl3/run_ai_backfill.py --status
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from etl3.config import settings as cfg
from etl3.db import connections
from etl3.enrichment import kb as drug_kb
from etl3.enrichment.ai import OllamaAccusedClient, OllamaDrugClient, ai_settings
from etl3.enrichment.project import _hash
from etl3.enrichment.runner import (
    _ai_already_settled,
    _brief_facts,
    _accused_enrichment,
    run_ai_extraction,
)

_STOP = False


def _handle_signal(signum, _frame):
    global _STOP
    print(f"[ai-backfill] received signal {signum}; will stop after current batch", flush=True)
    _STOP = True


def _pairs(conn, sql):
    with conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()


def _eligible_drug_pending(conn) -> tuple[int, int, int]:
    """Return (eligible, settled, pending) for drug AI."""
    known = {row[0] for row in _pairs(conn, "SELECT crime_id FROM crimes_unified")}
    facts = _brief_facts(conn)
    eligible = settled = pending = 0
    for crime_id, (_src, text) in facts.items():
        if crime_id not in known:
            continue
        eligible += 1
        digest = _hash({"brief_facts": text})
        if _ai_already_settled(conn, crime_id, digest):
            settled += 1
        else:
            pending += 1
    return eligible, settled, pending


def print_status(conn) -> dict:
    ai = ai_settings()
    eligible, settled, pending = _eligible_drug_pending(conn)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT status, COALESCE(validation_status, ''), COUNT(*)
            FROM ai_extraction_attempts
            GROUP BY 1, 2
            ORDER BY 1, 2
            """
        )
        groups = cur.fetchall()
        cur.execute("SELECT COUNT(*) FROM drug_extractions WHERE provenance = 'etl3_ai'")
        ai_drugs = cur.fetchone()[0]
    report = {
        "mode": ai["mode"],
        "host": ai["host"],
        "model": ai["model"],
        "batch_size": ai["batch_size"],
        "limit": ai["limit"],
        "drug_eligible": eligible,
        "drug_settled": settled,
        "drug_pending": pending,
        "ai_drug_rows": ai_drugs,
        "attempt_groups": [
            {"status": s, "validation_status": v, "count": n} for s, v, n in groups
        ],
    }
    print(json.dumps(report, indent=2, default=str), flush=True)
    return report


def _ensure_backfill_mode():
    """Force production backfill semantics for this process."""
    import os

    os.environ["ETL3_AI_MODE"] = "backfill"
    os.environ["ETL3_AI_ENABLED"] = "1"
    # Clear any leftover diagnostic cap for this process.
    os.environ["ETL3_AI_LIMIT"] = "0"


def run_backfill(drugs: bool = True, accused: bool = True) -> dict:
    global _STOP
    _ensure_backfill_mode()
    ai = ai_settings()
    if not ai["enabled"]:
        raise SystemExit("ETL3_AI_ENABLED must be on for backfill")
    if not ai["host"] or not ai["model"]:
        raise SystemExit("OLLAMA_BASE_URL and OLLAMA_MODEL are required for backfill")
    if ai["mode"] != "backfill":
        raise SystemExit(f"Expected mode=backfill, got {ai['mode']!r}")
    if cfg.EXPECTED_UNIFIED_DBNAME != "dopams_cctns_v2":
        raise SystemExit(f"Refusing target {cfg.EXPECTED_UNIFIED_DBNAME!r}")
    print(
        f"[ai-backfill] host={ai['host']} model={ai['model']} "
        f"batch_size={ai['batch_size']} limit={ai['limit']}",
        flush=True,
    )

    conn = connections.get_unified_connection()
    run_id = str(uuid.uuid4())
    summary = {"run_id": run_id, "drugs": None, "accused": None, "stopped": False}
    try:
        print_status(conn)
        drug_kb_obj = drug_kb.load_drug_kb(conn)
        if drugs and not _STOP:
            print(f"\n===== drug AI backfill run_id={run_id} =====\n", flush=True)
            client = OllamaDrugClient(ai["host"], ai["model"], ai["timeout"])
            # Process until pending=0 or stop/limit (limit is 0 in backfill).
            # One invocation walks the full eligible set; commits every batch.
            stats = run_ai_extraction(
                conn, run_id, drug_kb_obj.items,
                client=client, settings=ai, drug_kb_obj=drug_kb_obj,
            )
            conn.commit()
            summary["drugs"] = stats
            print("[ai-backfill] drugs:", json.dumps(stats), flush=True)
        if accused and not _STOP:
            print(f"\n===== accused AI backfill run_id={run_id} =====\n", flush=True)
            accused_client = OllamaAccusedClient(ai["host"], ai["model"], ai["timeout"])
            stats = _accused_enrichment(
                conn, run_id, accused_client=accused_client, settings=ai,
            )
            conn.commit()
            summary["accused"] = stats.get("ai") if isinstance(stats, dict) else stats
            print("[ai-backfill] accused:", json.dumps(summary["accused"]), flush=True)
        summary["final_status"] = print_status(conn)
        summary["stopped"] = _STOP
        pending = (summary.get("final_status") or {}).get("drug_pending")
        if pending == 0 and not _STOP:
            print("[ai-backfill] drug backlog exhausted", flush=True)
        elif pending:
            print(f"[ai-backfill] drug pending remaining={pending}", flush=True)
        return summary
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="ETL-3 production AI backfill")
    parser.add_argument("--status", action="store_true", help="Report backlog only")
    parser.add_argument("--drugs-only", action="store_true")
    parser.add_argument("--accused-only", action="store_true")
    args = parser.parse_args()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    if args.status:
        conn = connections.get_unified_connection(readonly=True)
        try:
            print_status(conn)
        finally:
            conn.close()
        return

    drugs = not args.accused_only
    accused = not args.drugs_only
    if args.drugs_only and args.accused_only:
        drugs = accused = True
    run_backfill(drugs=drugs, accused=accused)


if __name__ == "__main__":
    main()
