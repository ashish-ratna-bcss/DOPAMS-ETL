"""Read-only audit of V1/V2 media bookkeeping + ETL-3 relational gaps.

Writes only a JSON report under etl3/diagnostics/reports/.
Does not download media, does not mutate sources or dopams_cctns_v2.
Does not start AI backfill.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "etl3"))

from config import settings
from db.connections import (
    get_unified_connection,
    get_v1_source_connection,
    get_v2_source_connection,
)
from sync.reconcile import classify_gap

REPORT_DIR = Path(__file__).resolve().parent / "reports"


def _q(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchall()
    return cols, rows


def audit_v1_media(conn) -> dict:
    out = {"database": None, "totals_by_status": {}, "by_entity_status": [], "zero_byte": {}, "error_samples": []}
    cols, rows = _q(conn, "SELECT current_database()")
    out["database"] = rows[0][0]
    _, rows = _q(
        conn,
        """
        SELECT status, COUNT(*),
               COUNT(*) FILTER (WHERE COALESCE(file_size_bytes,0)=0),
               COUNT(*) FILTER (WHERE local_path IS NOT NULL)
        FROM cctns.cctns_media_files
        GROUP BY status ORDER BY status
        """,
    )
    for status, n, zero, with_path in rows:
        out["totals_by_status"][status] = {
            "count": n, "zero_or_null_size": zero, "with_local_path": with_path,
        }
    _, rows = _q(
        conn,
        """
        SELECT entity_type, status, COUNT(*),
               COUNT(*) FILTER (WHERE COALESCE(file_size_bytes,0)=0 AND status IN ('DOWNLOADED','CACHED','SUCCESS'))
        FROM cctns.cctns_media_files
        GROUP BY 1,2 ORDER BY 1,2
        """,
    )
    out["by_entity_status"] = [
        {"entity_type": e, "status": s, "count": n, "zero_size_successish": z}
        for e, s, n, z in rows
    ]
    _, rows = _q(conn, "SELECT COUNT(*) FROM cctns.cctns_media_files")
    out["total_tracked"] = rows[0][0]
    # Zero-byte among "successful" statuses
    _, rows = _q(
        conn,
        """
        SELECT entity_type, COUNT(*)
        FROM cctns.cctns_media_files
        WHERE COALESCE(file_size_bytes,0)=0
          AND status IN ('DOWNLOADED','CACHED','EMPTY','ZERO_BYTE','SUCCESS')
        GROUP BY 1 ORDER BY 1
        """,
    )
    out["zero_byte_success_statuses"] = {e: n for e, n in rows}
    # Explicit empty / not found / failed
    _, rows = _q(
        conn,
        """
        SELECT status, left(COALESCE(error_message,''), 80), COUNT(*)
        FROM cctns.cctns_media_files
        WHERE status NOT IN ('DOWNLOADED','CACHED','PENDING')
        GROUP BY 1,2 ORDER BY 3 DESC
        LIMIT 40
        """,
    )
    out["error_samples"] = [
        {"status": s, "error_prefix": e, "count": n} for s, e, n in rows
    ]
    # Disk presence sample for DOWNLOADED with path
    missing_on_disk = 0
    present_on_disk = 0
    _, rows = _q(
        conn,
        """
        SELECT local_path, file_size_bytes
        FROM cctns.cctns_media_files
        WHERE status IN ('DOWNLOADED','CACHED') AND local_path IS NOT NULL
        LIMIT 5000
        """,
    )
    for path, size in rows:
        try:
            if path and os.path.isfile(path):
                present_on_disk += 1
                if os.path.getsize(path) == 0:
                    out.setdefault("disk_zero_byte", 0)
                    out["disk_zero_byte"] = out.get("disk_zero_byte", 0) + 1
            else:
                missing_on_disk += 1
        except OSError:
            missing_on_disk += 1
    out["disk_sample"] = {
        "sampled": present_on_disk + missing_on_disk,
        "present": present_on_disk,
        "missing_path_or_file": missing_on_disk,
    }
    return out


def audit_v2_media(conn) -> dict:
    out = {"database": None, "by_source_field": [], "error_buckets": [], "pending_case_property": 0}
    cols, rows = _q(conn, "SELECT current_database()")
    out["database"] = rows[0][0]
    # Discover files bookkeeping table (production name: file_media_bookkeeping)
    _, rows = _q(
        conn,
        """
        SELECT table_schema, table_name
        FROM information_schema.tables
        WHERE table_name IN ('file_media_bookkeeping','files','media_files','file_downloads')
        ORDER BY 1,2
        """,
    )
    out["candidate_tables"] = [{"schema": s, "table": t} for s, t in rows]
    table = None
    preferred = ("file_media_bookkeeping", "files")
    for want in preferred:
        for s, t in rows:
            if t == want:
                table = f"{s}.{t}"
                break
        if table:
            break
    if not table:
        out["error"] = "no files bookkeeping table found"
        return out
    out["files_table"] = table
    _, rows = _q(
        conn,
        f"""
        SELECT source_type, source_field,
               COUNT(*) AS total,
               COUNT(*) FILTER (WHERE is_downloaded IS TRUE) AS downloaded,
               COUNT(*) FILTER (WHERE COALESCE(is_empty,false) IS TRUE) AS empty_flag,
               COUNT(*) FILTER (WHERE (is_downloaded IS NOT TRUE) AND COALESCE(is_empty,false) IS NOT TRUE) AS pendingish,
               COUNT(*) FILTER (WHERE download_error IS NOT NULL AND btrim(download_error) <> '') AS with_error
        FROM {table}
        GROUP BY 1,2
        ORDER BY 1,2
        """,
    )
    out["by_source_field"] = [
        {
            "source_type": a, "source_field": b, "total": t, "downloaded": d,
            "empty_flag": e, "pendingish": p, "with_error": err,
        }
        for a, b, t, d, e, p, err in rows
    ]
    _, rows = _q(
        conn,
        f"""
        SELECT source_type, source_field,
               left(COALESCE(download_error,''), 100) AS err,
               COUNT(*)
        FROM {table}
        WHERE download_error IS NOT NULL AND btrim(download_error) <> ''
        GROUP BY 1,2,3
        ORDER BY 4 DESC
        LIMIT 50
        """,
    )
    out["error_buckets"] = [
        {"source_type": a, "source_field": b, "error_prefix": e, "count": n}
        for a, b, e, n in rows
    ]
    _, rows = _q(
        conn,
        f"""
        SELECT COUNT(*) FROM {table}
        WHERE source_type IN ('case_property','fsl_case_property','property')
          AND (is_downloaded IS NOT TRUE)
          AND COALESCE(is_empty,false) IS NOT TRUE
        """,
    )
    out["pending_case_propertyish"] = rows[0][0]
    # HTTP 400 specifically
    _, rows = _q(
        conn,
        f"""
        SELECT source_type, source_field, COUNT(*)
        FROM {table}
        WHERE download_error ILIKE '%400%'
        GROUP BY 1,2 ORDER BY 3 DESC
        """,
    )
    out["http_400"] = [
        {"source_type": a, "source_field": b, "count": n} for a, b, n in rows
    ]
    return out


def audit_etl3_gaps(conn) -> dict:
    out = {"database": None, "unified_counts": {}, "gap_ledger": [], "station_27": []}
    _, rows = _q(conn, "SELECT current_database()")
    out["database"] = rows[0][0]
    for table in (
        "crimes_unified", "accused_unified", "persons_unified", "arrests_unified",
        "chargesheets_unified", "source_gap_ledger", "identity_links", "change_log",
    ):
        try:
            _, rows = _q(conn, f"SELECT COUNT(*) FROM {table}")
            out["unified_counts"][table] = rows[0][0]
        except Exception as e:
            conn.rollback()
            out["unified_counts"][table] = f"ERR {e}"
    _, rows = _q(
        conn,
        """
        SELECT gap_type, status, COUNT(*)
        FROM source_gap_ledger
        GROUP BY 1,2 ORDER BY 1,2
        """,
    )
    for gap_type, status, n in rows:
        out["gap_ledger"].append({
            "gap_type": gap_type,
            "status": status,
            "count": n,
            "classification": classify_gap(gap_type),
        })
    try:
        _, rows = _q(
            conn,
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema='public' AND table_name='source_gap_ledger'
            ORDER BY ordinal_position
            """,
        )
        out["gap_ledger_columns"] = [r[0] for r in rows]
    except Exception as e:
        conn.rollback()
        out["gap_ledger_columns_err"] = str(e)
    return out


def audit_etl3_station_gaps(conn) -> dict:
    _, colrows = _q(
        conn,
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema='public' AND table_name='source_gap_ledger'
        ORDER BY ordinal_position
        """,
    )
    colnames = [r[0] for r in colrows]
    select_cols = ", ".join(f'"{c}"' for c in colnames)
    _, rows = _q(
        conn,
        f"""
        SELECT {select_cols}
        FROM source_gap_ledger
        WHERE gap_type = 'ambiguous_v1_station_name'
        ORDER BY 1
        """,
    )
    items = [dict(zip(colnames, row)) for row in rows]
    return {
        "count": len(items),
        "classification_now": classify_gap("ambiguous_v1_station_name"),
        "items": items[:30],
        "expected_class": "UNRESOLVED_RELATIONSHIP",
    }


def audit_source_vs_target(v1, v2, tg) -> dict:
    """High-level coverage using source counts vs unified provenance."""
    out = {}
    # V1 FIR count
    try:
        _, rows = _q(v1, "SELECT COUNT(*) FROM cctns.cctns_fir")
        out["v1_fir"] = rows[0][0]
    except Exception as e:
        v1.rollback()
        out["v1_fir"] = f"ERR {e}"
    try:
        _, rows = _q(v2, "SELECT COUNT(*) FROM public.crimes")
        out["v2_crimes"] = rows[0][0]
    except Exception as e:
        v2.rollback()
        out["v2_crimes"] = f"ERR {e}"
    _, rows = _q(
        tg,
        """
        SELECT source_system, COUNT(*) FROM crimes_unified GROUP BY 1 ORDER BY 1
        """,
    )
    out["crimes_unified_by_source"] = {s: n for s, n in rows}
    _, rows = _q(
        tg,
        """
        SELECT source_system, COUNT(*) FROM accused_unified GROUP BY 1 ORDER BY 1
        """,
    )
    out["accused_unified_by_source"] = {s: n for s, n in rows}
    return out


def main():
    assert settings.EXPECTED_UNIFIED_DBNAME == "dopams_cctns_v2"
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = {
        "generated_at": stamp,
        "checkout_note": "dopams-cctns-ai / cctns-ai",
        "target": settings.EXPECTED_UNIFIED_DBNAME,
        "ai_backfill": "not_started",
    }
    v1 = get_v1_source_connection()
    v2 = get_v2_source_connection()
    tg = get_unified_connection(readonly=True)
    try:
        # write probes
        for label, conn, expect_fail in (("V1", v1, True), ("V2", v2, True)):
            try:
                with conn.cursor() as cur:
                    cur.execute("CREATE TEMP TABLE etl3_audit_probe(x int)")
                report[f"{label}_write"] = "UNEXPECTED_SUCCESS"
                conn.rollback()
            except Exception as e:
                conn.rollback()
                report[f"{label}_write"] = f"blocked:{type(e).__name__}"
        report["v1_media"] = audit_v1_media(v1)
        report["v2_media"] = audit_v2_media(v2)
        report["etl3_gaps"] = audit_etl3_gaps(tg)
        report["station_ambiguous"] = audit_etl3_station_gaps(tg)
        report["coverage"] = audit_source_vs_target(v1, v2, tg)
    finally:
        for c in (v1, v2, tg):
            c.close()
    path = REPORT_DIR / f"media_gap_audit_{stamp}.json"
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps({
        "report": str(path),
        "v1_media_total": report["v1_media"].get("total_tracked"),
        "v1_statuses": report["v1_media"].get("totals_by_status"),
        "v2_http_400": report["v2_media"].get("http_400"),
        "station_count": report["station_ambiguous"].get("count"),
        "station_class": report["station_ambiguous"].get("classification_now"),
        "gap_ledger": report["etl3_gaps"].get("gap_ledger"),
        "coverage": report["coverage"],
    }, indent=2, default=str))


if __name__ == "__main__":
    main()
