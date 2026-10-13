#!/usr/bin/env python3
"""READ-ONLY layer verification for V1/V2 vs enrichment vs AI. No writes."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/home/eagle/dopams-cctns-ai")
os.environ.setdefault("ETL3_AI_ENABLED", "1")
os.environ.setdefault("ETL3_AI_MODE", "backfill")
os.environ.setdefault("OLLAMA_BASE_URL", "http://10.12.1.124:11434")
os.environ.setdefault("OLLAMA_MODEL", "qwen3:8b")

from etl3.db import connections
from etl3.run_ai_backfill import _eligible_accused_pending, _eligible_drug_pending


def main():
    out = {"captured_at_utc": datetime.now(timezone.utc).isoformat()}
    conn = connections.get_unified_connection(readonly=True)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database()")
            out["database"] = cur.fetchone()[0]

            def count(sql, params=None):
                cur.execute(sql, params or ())
                return cur.fetchone()[0]

            def rows(sql, params=None):
                cur.execute(sql, params or ())
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, r)) for r in cur.fetchall()]

            # Source observation layers
            out["source"] = {
                "crimes_source": count("SELECT COUNT(*) FROM crimes_source"),
                "crimes_source_by_ss": rows(
                    "SELECT source_system, COUNT(*) n FROM crimes_source GROUP BY 1 ORDER BY 1"
                ),
                "accused_source": count("SELECT COUNT(*) FROM accused_source"),
                "accused_source_by_ss": rows(
                    "SELECT source_system, COUNT(*) n FROM accused_source GROUP BY 1 ORDER BY 1"
                ),
                "persons_source": count("SELECT COUNT(*) FROM persons_source"),
                "persons_source_by_ss": rows(
                    "SELECT source_system, COUNT(*) n FROM persons_source GROUP BY 1 ORDER BY 1"
                ),
                "arrests_source": count("SELECT COUNT(*) FROM arrests_source"),
                "arrests_source_by_ss": rows(
                    "SELECT source_system, COUNT(*) n FROM arrests_source GROUP BY 1 ORDER BY 1"
                ),
            }

            # Unified merged layer
            out["unified"] = {
                "crimes_unified": count("SELECT COUNT(*) FROM crimes_unified"),
                "crimes_by_ss": rows(
                    "SELECT source_system, COUNT(*) n FROM crimes_unified GROUP BY 1 ORDER BY 1"
                ),
                "persons_unified": count("SELECT COUNT(*) FROM persons_unified"),
                "persons_by_ss": rows(
                    "SELECT source_system, COUNT(*) n FROM persons_unified GROUP BY 1 ORDER BY 1"
                ),
                "accused_unified": count("SELECT COUNT(*) FROM accused_unified"),
                "accused_by_ss": rows(
                    "SELECT source_system, COUNT(*) n FROM accused_unified GROUP BY 1 ORDER BY 1"
                ),
                "arrests_unified": count("SELECT COUNT(*) FROM arrests_unified"),
                "arrests_by_ss": rows(
                    "SELECT source_system, COUNT(*) n FROM arrests_unified GROUP BY 1 ORDER BY 1"
                ),
                "media_unified": count("SELECT COUNT(*) FROM media_unified"),
                "media_by_ss": rows(
                    "SELECT source_system, COUNT(*) n FROM media_unified GROUP BY 1 ORDER BY 1"
                ),
            }

            # Deterministic enrichment (no LLM required)
            out["deterministic"] = {
                "crime_enrichment": count("SELECT COUNT(*) FROM crime_enrichment"),
                "person_enrichment": count("SELECT COUNT(*) FROM person_enrichment"),
                "accused_enrichment": count("SELECT COUNT(*) FROM accused_enrichment"),
                "arrest_enrichment": count("SELECT COUNT(*) FROM arrest_enrichment"),
                "drug_v1_dossier": count(
                    "SELECT COUNT(*) FROM drug_extractions WHERE provenance='v1_dossier'"
                ),
                "drug_v2_property": count(
                    "SELECT COUNT(*) FROM drug_extractions WHERE provenance='v2_property'"
                ),
                "drug_etl3_ai": count(
                    "SELECT COUNT(*) FROM drug_extractions WHERE provenance='etl3_ai'"
                ),
            }

            # Sample: unified row exists without needing AI
            out["independence_checks"] = {
                "crimes_unified_without_ai_attempt": count(
                    """
                    SELECT COUNT(*) FROM crimes_unified c
                    WHERE NOT EXISTS (
                      SELECT 1 FROM ai_extraction_attempts a WHERE a.crime_id = c.crime_id
                    )
                    """
                ),
                "accused_unified_total": count("SELECT COUNT(*) FROM accused_unified"),
                "drug_non_ai_rows": count(
                    "SELECT COUNT(*) FROM drug_extractions WHERE provenance <> 'etl3_ai'"
                ),
            }

            # AI attempts
            out["ai_attempts"] = rows(
                """
                SELECT status, COALESCE(validation_status,'') AS validation_status, COUNT(*) AS n
                FROM ai_extraction_attempts
                GROUP BY 1,2 ORDER BY 1,2
                """
            )
            out["ai_attempts_total"] = count("SELECT COUNT(*) FROM ai_extraction_attempts")

            # Accused enrichment LLM vs non-LLM
            out["accused_llm_rows"] = count(
                """
                SELECT COUNT(*) FROM accused_enrichment
                WHERE field_sources::text LIKE '%%LLM_FALLBACK%%'
                """
            )

            # be_read views
            out["be_read_views"] = [
                r["viewname"]
                for r in rows(
                    """
                    SELECT viewname FROM pg_catalog.pg_views
                    WHERE schemaname='be_read' ORDER BY 1
                    """
                )
            ]
            # Does be_read.accused expose enrichment/AI columns?
            if out["be_read_views"]:
                cur.execute(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema='be_read' AND table_name='accused'
                    ORDER BY ordinal_position
                    """
                )
                out["be_read_accused_columns"] = [r[0] for r in cur.fetchall()]
                cur.execute(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema='be_read' AND table_name='crime'
                    ORDER BY ordinal_position
                    """
                )
                out["be_read_crime_columns"] = [r[0] for r in cur.fetchall()]
                out["be_read_counts"] = {
                    "crime": count("SELECT COUNT(*) FROM be_read.crime"),
                    "accused": count("SELECT COUNT(*) FROM be_read.accused"),
                    "person": count("SELECT COUNT(*) FROM be_read.person"),
                    "media": count("SELECT COUNT(*) FROM be_read.media")
                    if "media" in out["be_read_views"]
                    else None,
                }

            # Check if firs_mv / accuseds_mv exist on this DB (legacy app MVs)
            cur.execute(
                """
                SELECT schemaname, matviewname FROM pg_matviews
                WHERE matviewname IN ('firs_mv','accuseds_mv')
                ORDER BY 1,2
                """
            )
            out["materialized_views_here"] = [
                {"schema": s, "name": n} for s, n in cur.fetchall()
            ]
            cur.execute(
                """
                SELECT table_schema, table_name FROM information_schema.tables
                WHERE table_name IN ('firs_mv','accuseds_mv','firs','accuseds')
                ORDER BY 1,2
                """
            )
            out["legacy_named_tables_here"] = [
                {"schema": s, "name": n} for s, n in cur.fetchall()
            ]

            # Gaps
            out["gaps"] = {
                "accused_missing_person_id": count(
                    "SELECT COUNT(*) FROM accused_unified WHERE person_id IS NULL"
                ),
                "arrests_missing_accused_id": count(
                    "SELECT COUNT(*) FROM arrests_unified WHERE accused_id IS NULL"
                ),
            }

            cur.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema='public' AND table_name='crimes_source'
                ORDER BY ordinal_position
                """
            )
            cols = [r[0] for r in cur.fetchall()]
            out["crimes_source_columns"] = cols
            order = "captured_at" if "captured_at" in cols else (
                "source_run_id" if "source_run_id" in cols else cols[-1]
            )
            payload_col = "payload" if "payload" in cols else None
            if payload_col:
                cur.execute(
                    f"""
                    SELECT source_system, left({payload_col}::text, 200)
                    FROM crimes_source
                    WHERE {payload_col} IS NOT NULL
                    ORDER BY {order} DESC NULLS LAST
                    LIMIT 2
                    """
                )
                out["crimes_source_payload_samples"] = [
                    {"source_system": s, "payload_prefix": p} for s, p in cur.fetchall()
                ]

        drug = _eligible_drug_pending(conn)
        accused = _eligible_accused_pending(conn)
        out["ai_backlog"] = {
            "drug": {"eligible": drug[0], "settled": drug[1], "pending": drug[2]},
            "accused": {
                "eligible": accused[0],
                "settled": accused[1],
                "pending": accused[2],
            },
        }
    finally:
        conn.close()

    path = "/tmp/verify_layers_ro.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, default=str)
    print(json.dumps(out, indent=2, default=str))
    print("WROTE", path)


if __name__ == "__main__":
    main()
