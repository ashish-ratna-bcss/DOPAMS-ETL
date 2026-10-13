#!/usr/bin/env python3
"""READ-ONLY full status snapshot for ETL-3 audit. No writes."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/home/eagle/dopams-cctns-ai")
os.environ.setdefault("ETL3_AI_ENABLED", "1")
os.environ.setdefault("ETL3_AI_MODE", "backfill")
os.environ.setdefault("OLLAMA_BASE_URL", "http://10.12.1.124:11434")
os.environ.setdefault("OLLAMA_MODEL", "qwen3:8b")

from etl3.db import connections
from etl3.run_ai_backfill import (
    _eligible_accused_pending,
    _eligible_drug_pending,
    print_status,
)


def q(cur, sql, params=None):
    cur.execute(sql, params or ())
    return cur.fetchall()


def count(cur, sql, params=None):
    cur.execute(sql, params or ())
    row = cur.fetchone()
    return row[0] if row else None


def table_exists(cur, name, schema="public"):
    cur.execute(
        """
        SELECT 1 FROM information_schema.tables
        WHERE table_schema=%s AND table_name=%s
        """,
        (schema, name),
    )
    return cur.fetchone() is not None


def main():
    out = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "dbname": None,
        "tables": {},
        "enrichment": {},
        "ai": {},
        "runs": {},
        "kb": {},
        "gaps": {},
        "notes": [],
    }
    conn = connections.get_unified_connection(readonly=True)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database(), current_user")
            out["dbname"], out["db_user"] = cur.fetchone()

            # Principal consolidated / observation tables
            candidates = [
                "crimes_source",
                "persons_source",
                "accused_source",
                "arrests_source",
                "chargesheets_source",
                "properties_source",
                "hierarchy_source",
                "fsl_source",
                "disposal_source",
                "media_source",
                "crimes_unified",
                "persons_unified",
                "accused_unified",
                "arrests_unified",
                "chargesheets_unified",
                "properties_unified",
                "hierarchy_unified",
                "disposal_unified",
                "media_unified",
                "crime_enrichment",
                "person_enrichment",
                "arrest_enrichment",
                "accused_enrichment",
                "chargesheet_enrichment",
                "hierarchy_enrichment",
                "property_enrichment",
                "fsl_enrichment",
                "disposal_enrichment",
                "drug_extractions",
                "ai_extraction_attempts",
                "enrichment_run_log",
                "change_log",
                "etl_run_log",
                "merge_run_log",
            ]
            for t in candidates:
                if table_exists(cur, t):
                    out["tables"][t] = count(cur, f"SELECT COUNT(*) FROM {t}")
                else:
                    out["tables"][t] = None
                    out["notes"].append(f"missing_table:{t}")

            # Provenance breakdowns
            if table_exists(cur, "crimes_unified"):
                out["gaps"]["crimes_by_source"] = [
                    {"source_system": s, "n": n}
                    for s, n in q(
                        cur,
                        "SELECT source_system, COUNT(*) FROM crimes_unified GROUP BY 1 ORDER BY 1",
                    )
                ]
            if table_exists(cur, "accused_unified"):
                out["gaps"]["accused_by_source"] = [
                    {"source_system": s, "n": n}
                    for s, n in q(
                        cur,
                        "SELECT source_system, COUNT(*) FROM accused_unified GROUP BY 1 ORDER BY 1",
                    )
                ]
            if table_exists(cur, "drug_extractions"):
                out["enrichment"]["drugs_by_provenance"] = [
                    {"provenance": p, "n": n}
                    for p, n in q(
                        cur,
                        "SELECT provenance, COUNT(*) FROM drug_extractions GROUP BY 1 ORDER BY 1",
                    )
                ]

            # Unresolved links (nullable FKs)
            if table_exists(cur, "accused_unified"):
                out["gaps"]["accused_missing_person_id"] = count(
                    cur,
                    "SELECT COUNT(*) FROM accused_unified WHERE person_id IS NULL",
                )
            if table_exists(cur, "arrests_unified"):
                out["gaps"]["arrests_missing_accused_id"] = count(
                    cur,
                    "SELECT COUNT(*) FROM arrests_unified WHERE accused_id IS NULL",
                )

            # Run logs
            for log_table, order_col in (
                ("enrichment_run_log", "started_at"),
                ("etl_run_log", "started_at"),
                ("merge_run_log", "started_at"),
            ):
                if not table_exists(cur, log_table):
                    continue
                # Discover columns
                cur.execute(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name=%s ORDER BY ordinal_position
                    """,
                    (log_table,),
                )
                cols = [r[0] for r in cur.fetchall()]
                out["runs"][f"{log_table}_columns"] = cols
                # Prefer finished/started if present
                ts = None
                for c in ("finished_at", "ended_at", "started_at", "created_at"):
                    if c in cols:
                        ts = c
                        break
                status_col = "status" if "status" in cols else None
                sel = ", ".join(
                    c
                    for c in ("run_id", status_col, ts, "stats")
                    if c and c in cols
                )
                if not sel:
                    continue
                order = ts or cols[0]
                try:
                    rows = q(
                        cur,
                        f"SELECT {sel} FROM {log_table} ORDER BY {order} DESC NULLS LAST LIMIT 8",
                    )
                    out["runs"][log_table] = [
                        dict(zip([c.strip() for c in sel.split(",")], r))
                        for r in rows
                    ]
                except Exception as exc:
                    out["runs"][log_table] = {"error": str(exc)}
                    conn.rollback()

            # AI attempts
            if table_exists(cur, "ai_extraction_attempts"):
                out["ai"]["attempt_groups"] = [
                    {"status": s, "validation_status": v, "n": n}
                    for s, v, n in q(
                        cur,
                        """
                        SELECT status, COALESCE(validation_status,''), COUNT(*)
                        FROM ai_extraction_attempts
                        GROUP BY 1,2 ORDER BY 1,2
                        """,
                    )
                ]
                cur.execute(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name='ai_extraction_attempts'
                    """
                )
                out["ai"]["attempt_columns"] = [r[0] for r in cur.fetchall()]

            # KB
            if table_exists(cur, "drug_categories", schema="kb"):
                out["kb"]["drug_categories"] = count(
                    cur, "SELECT COUNT(*) FROM kb.drug_categories"
                )
            if table_exists(cur, "drug_ignore_list", schema="kb"):
                out["kb"]["drug_ignore_list"] = count(
                    cur, "SELECT COUNT(*) FROM kb.drug_ignore_list"
                )

            # Media availability sample if columns exist
            if table_exists(cur, "media_unified"):
                cur.execute(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name='media_unified'
                    """
                )
                mcols = {r[0] for r in cur.fetchall()}
                out["tables"]["media_unified_columns_sample"] = sorted(mcols)[:40]
                if "availability" in mcols:
                    out["gaps"]["media_by_availability"] = [
                        {"availability": a, "n": n}
                        for a, n in q(
                            cur,
                            "SELECT availability, COUNT(*) FROM media_unified GROUP BY 1 ORDER BY 2 DESC",
                        )
                    ]

        # Eligible/settled/pending (can be slow)
        print("computing AI eligibility...", flush=True)
        drug = _eligible_drug_pending(conn)
        accused = _eligible_accused_pending(conn)
        out["ai"]["drug_eligible_settled_pending"] = {
            "eligible": drug[0],
            "settled": drug[1],
            "pending": drug[2],
        }
        out["ai"]["accused_eligible_settled_pending"] = {
            "eligible": accused[0],
            "settled": accused[1],
            "pending": accused[2],
        }
        # Full status print also for parity
        out["ai"]["status_report"] = print_status(conn)

    finally:
        conn.close()

    path = Path("/tmp/etl3_full_audit_snapshot.json")
    path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(json.dumps(out, indent=2, default=str), flush=True)
    print(f"WROTE {path}", flush=True)


if __name__ == "__main__":
    main()
