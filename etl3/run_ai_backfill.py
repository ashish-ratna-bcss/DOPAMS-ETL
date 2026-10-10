"""Production AI historical backfill for ETL-3 (drugs + accused).

Default: per-FIR scheduling — for each eligible crime, run drug enrichment
then accused enrichment sequentially (single-flight Ollama), with independent
settlement checks and commits per enrichment type.

Resumes from ai_extraction_attempts / input hashes. Does not reset the
target, does not re-run merge, and does not write to V1/V2.

Usage (repo root):
    python etl3/run_ai_backfill.py
    python etl3/run_ai_backfill.py --drugs-only
    python etl3/run_ai_backfill.py --accused-only
    python etl3/run_ai_backfill.py --status
    python etl3/run_ai_backfill.py --legacy-phases   # old drugs-then-accused
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
from etl3.enrichment.accused_facts import (
    apply_v1_code_type_and_ccl_numbering,
    build_roster_for_prompt,
)
from etl3.enrichment.runner import (
    _accused_enrichment,
    _accused_input_digest,
    _ai_already_settled,
    _brief_facts,
    _drug_input_digest,
    _load_accused_by_crime,
    run_ai_extraction,
    run_per_fir_ai_backfill,
)

_STOP = False


def _handle_signal(signum, _frame):
    global _STOP
    print(f"[ai-backfill] received signal {signum}; will stop after current enrichment", flush=True)
    _STOP = True


def _stop_check() -> bool:
    return _STOP


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
        digest = _drug_input_digest(text)
        if _ai_already_settled(conn, crime_id, digest):
            settled += 1
        else:
            pending += 1
    return eligible, settled, pending


def _eligible_accused_pending(conn) -> tuple[int, int, int]:
    """Return (eligible, settled, pending) for accused AI (one call per FIR roster)."""
    facts = _brief_facts(conn)
    by_crime = _load_accused_by_crime(conn)
    eligible = settled = pending = 0
    for crime_id, records in by_crime.items():
        fact_entry = facts.get(crime_id)
        if not fact_entry:
            continue
        eligible += 1
        _source_system, text = fact_entry
        work = list(records)
        if work and work[0].get("source_system") == "V1":
            work = apply_v1_code_type_and_ccl_numbering(work, text)
        roster = build_roster_for_prompt(work)
        digest = _accused_input_digest(text, roster)
        if _ai_already_settled(conn, crime_id, digest):
            settled += 1
        else:
            pending += 1
    return eligible, settled, pending


def print_status(conn) -> dict:
    ai = ai_settings()
    drug_eligible, drug_settled, drug_pending = _eligible_drug_pending(conn)
    accused_eligible, accused_settled, accused_pending = _eligible_accused_pending(conn)
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
        try:
            cur.execute(
                """
                SELECT COUNT(*) FROM accused_enrichment
                WHERE field_sources::text LIKE '%%LLM_FALLBACK%%'
                """
            )
            ai_accused_rows = cur.fetchone()[0]
        except Exception:
            ai_accused_rows = None
    report = {
        "mode": ai["mode"],
        "host": ai["host"],
        "model": ai["model"],
        "batch_size": ai["batch_size"],
        "limit": ai["limit"],
        "drug_eligible": drug_eligible,
        "drug_settled": drug_settled,
        "drug_pending": drug_pending,
        "accused_eligible": accused_eligible,
        "accused_settled": accused_settled,
        "accused_pending": accused_pending,
        "ai_drug_rows": ai_drugs,
        "ai_accused_llm_rows": ai_accused_rows,
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


def run_backfill(
    drugs: bool = True,
    accused: bool = True,
    *,
    legacy_phases: bool = False,
) -> dict:
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
    schedule = "legacy-phases" if legacy_phases else "per-fir"
    print(
        f"[ai-backfill] schedule={schedule} host={ai['host']} model={ai['model']} "
        f"batch_size={ai['batch_size']} limit={ai['limit']}",
        flush=True,
    )

    conn = connections.get_unified_connection()
    run_id = str(uuid.uuid4())
    summary = {
        "run_id": run_id,
        "schedule": schedule,
        "drugs": None,
        "accused": None,
        "stopped": False,
    }
    try:
        print_status(conn)
        drug_kb_obj = drug_kb.load_drug_kb(conn)
        if legacy_phases:
            if drugs and not _STOP:
                print(f"\n===== drug AI backfill run_id={run_id} =====\n", flush=True)
                client = OllamaDrugClient(ai["host"], ai["model"], ai["timeout"])
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
        else:
            print(f"\n===== per-FIR AI backfill run_id={run_id} =====\n", flush=True)
            drug_client = (
                OllamaDrugClient(ai["host"], ai["model"], ai["timeout"])
                if drugs else None
            )
            accused_client = (
                OllamaAccusedClient(ai["host"], ai["model"], ai["timeout"])
                if accused else None
            )
            stats = run_per_fir_ai_backfill(
                conn,
                run_id,
                drug_kb_obj.items,
                drug_client=drug_client,
                accused_client=accused_client,
                settings=ai,
                drug_kb_obj=drug_kb_obj,
                do_drugs=drugs,
                do_accused=accused,
                stop_check=_stop_check,
            )
            conn.commit()
            summary["drugs"] = stats.get("drugs")
            summary["accused"] = stats.get("accused")
            summary["per_fir_status"] = stats.get("status")
            print("[ai-backfill] per-fir:", json.dumps(stats, default=str), flush=True)

        summary["final_status"] = print_status(conn)
        summary["stopped"] = _STOP
        final = summary.get("final_status") or {}
        drug_pending = final.get("drug_pending")
        accused_pending = final.get("accused_pending")
        if drug_pending == 0 and accused_pending == 0 and not _STOP:
            print("[ai-backfill] drug + accused backlog exhausted", flush=True)
        else:
            print(
                f"[ai-backfill] remaining drug_pending={drug_pending} "
                f"accused_pending={accused_pending}",
                flush=True,
            )
        return summary
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="ETL-3 production AI backfill")
    parser.add_argument("--status", action="store_true", help="Report backlog only")
    parser.add_argument("--drugs-only", action="store_true")
    parser.add_argument("--accused-only", action="store_true")
    parser.add_argument(
        "--legacy-phases",
        action="store_true",
        help="Use old all-drugs-then-all-accused schedule (not recommended)",
    )
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
    run_backfill(drugs=drugs, accused=accused, legacy_phases=args.legacy_phases)


if __name__ == "__main__":
    main()
