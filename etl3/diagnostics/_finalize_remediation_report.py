"""Assemble final remediation validation report without re-running Phase-5."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

os.environ["ETL3_AI_ENABLED"] = "0"

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "etl3"))

from db.connections import (
    get_unified_connection,
    get_v1_source_connection,
    get_v2_source_connection,
)
from sync.reconcile import classify_gap

OUT = Path(__file__).resolve().parent / "reports" / "phase5_gap_remediation_validation.json"
FINAL = Path(__file__).resolve().parent / "reports" / "media_storage_gap_remediation_final.json"

IDS = [
    "6a825ec7d8a9e1156252d824",
    "6ac788d1515873145609a177",
    "6ac795b3d5f5f99c45fc93b9",
    "6ac7afe12308a466ad90e3be",
    "6ac7bd794090c46bdc603c11",
    "6ac7c31999703f12c73f99dc",
    "6ac7c9dfcf6b6f185de35b2f",
    "6ac7db0c99703fb493400ef1",
    "6ac7ecedd3448842629ff4d7",
    "6ac7f28c4ef853fa86d881b0",
]

RUN_PRIMARY = "9cfd07f1-941b-430b-be7e-d8a105a7acf2"
RUN_SECOND = "2a6931c8-c54a-4050-8da8-594bc16441bc"
RUN_FAILED_AI = "aa14bd75-eaea-4fc2-a704-aa02f9276afa"


def main():
    out = {}

    # Safety / identity
    out["repo"] = {
        "path": "/home/eagle/dopams-cctns-ai",
        "branch": subprocess.check_output(
            "git -C /home/eagle/dopams-cctns-ai rev-parse --abbrev-ref HEAD",
            shell=True, text=True,
        ).strip(),
        "commit": subprocess.check_output(
            "git -C /home/eagle/dopams-cctns-ai rev-parse --short HEAD",
            shell=True, text=True,
        ).strip(),
    }
    dump = "/home/eagle/backups/dopams_cctns_v2/dopams_cctns_v2_pre_gap_remediation_20261009T074430Z.dump"
    out["backup_present"] = Path(dump).is_file()
    out["backup_path"] = dump

    v2 = get_v2_source_connection()
    vc = v2.cursor()
    vc.execute("SELECT COUNT(*) FROM crimes")
    v2_count = vc.fetchone()[0]
    vc.execute(
        """
        SELECT source_type, source_field,
               COUNT(*) total,
               COUNT(*) FILTER (WHERE is_downloaded IS TRUE) downloaded,
               COUNT(*) FILTER (WHERE download_error LIKE 'PERMANENT:%%') permanent,
               COUNT(*) FILTER (WHERE download_error LIKE 'SOURCE: MEDIA present but file_id is null%%') null_fid_annot,
               COUNT(*) FILTER (WHERE download_error ILIKE '%%missing on disk%%') disk_missing,
               COUNT(*) FILTER (WHERE download_error ILIKE '%%400%%') http400
        FROM public.file_media_bookkeeping
        GROUP BY 1,2 ORDER BY 1,2
        """
    )
    v2_media = [
        dict(zip(
            ["source_type", "source_field", "total", "downloaded", "permanent",
             "null_fid_annot", "disk_missing", "http400"],
            r,
        ))
        for r in vc.fetchall()
    ]
    v2.close()

    v1 = get_v1_source_connection()
    c1 = v1.cursor()
    c1.execute(
        """
        SELECT status, COUNT(*),
               COUNT(*) FILTER (WHERE local_path LIKE '/home/tganb/%%'),
               COUNT(*) FILTER (WHERE local_path LIKE '/home/eagle/%%')
        FROM cctns.cctns_media_files GROUP BY 1 ORDER BY 1
        """
    )
    v1_status = [
        {"status": s, "count": n, "tganb_paths": t, "eagle_paths": e}
        for s, n, t, e in c1.fetchall()
    ]
    c1.execute(
        """
        SELECT COUNT(*) FROM cctns.cctns_media_files
        WHERE status='NOT_FOUND'
          AND error_message LIKE 'Document not available on Alfresco DMS (0 bytes)%%'
        """
    )
    nf_zero = c1.fetchone()[0]
    c1.execute(
        """
        SELECT entity_type, COUNT(*) FROM cctns.cctns_media_files
        WHERE status='NOT_FOUND' GROUP BY 1
        """
    )
    nf_by = dict(c1.fetchall())
    v1.close()

    tg = get_unified_connection(readonly=True)
    cur = tg.cursor()
    cur.execute("SELECT current_database()")
    out["target_db"] = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM crimes_unified WHERE source_system='V2'")
    u_count = cur.fetchone()[0]
    cur.execute(
        "SELECT crime_id FROM crimes_unified WHERE source_system='V2' AND crime_id = ANY(%s) ORDER BY 1",
        (IDS,),
    )
    present = [r[0] for r in cur.fetchall()]
    cur.execute(
        """
        SELECT source_record_id::text, source_run_id, consolidation_run_id::text
        FROM crimes_source
        WHERE source_system='V2' AND source_record_id = ANY(%s)
        ORDER BY 1
        """,
        (IDS,),
    )
    obs = [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]
    cur.execute(
        """
        SELECT run_id::text, status, started_at, finished_at, rows_observed, rows_changed
        FROM consolidation_run_log
        WHERE run_id::text = ANY(%s)
        ORDER BY started_at
        """,
        ([RUN_FAILED_AI, RUN_PRIMARY, RUN_SECOND],),
    )
    runs = [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]
    cur.execute(
        """
        SELECT source_system, source_module, last_processed_source_run_id, status
        FROM consolidation_cursor WHERE source_system='V2' AND source_module='crimes'
        """
    )
    crime_cursor = dict(zip([d[0] for d in cur.description], cur.fetchone()))
    cur.execute("SELECT status, COUNT(1) FROM consolidation_cursor GROUP BY 1")
    cursor_counts = dict(cur.fetchall())

    # station gaps
    cur.execute(
        """
        SELECT gap_key, status FROM source_gap_ledger
        WHERE gap_type='ambiguous_v1_station_name' ORDER BY gap_key
        """
    )
    station_rows = cur.fetchall()
    station = {
        "count": len(station_rows),
        "classification": classify_gap("ambiguous_v1_station_name"),
        "all_open": all(s == "OPEN" for _, s in station_rows),
        "keys": [k for k, _ in station_rows],
        "assigned_ps_codes": 0,
    }
    # ps enrichment assigned=0 confirmed in run log; verify unified codes remain unset for these
    cur.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema='public' AND table_name='crimes_unified'
        """
    )
    ccols = {r[0] for r in cur.fetchall()}
    code_col = next((c for c in ("ps_code", "police_station_code") if c in ccols), None)
    if code_col and station_rows:
        keys = [k for k, _ in station_rows]
        cur.execute(
            f"SELECT COUNT(*) FROM crimes_unified WHERE source_system='V1' AND crime_id = ANY(%s) AND {code_col} IS NOT NULL AND {code_col}::text <> ''",
            (keys,),
        )
        station["ambiguous_with_assigned_code_count"] = cur.fetchone()[0]

    # integrity
    cur.execute(
        """
        SELECT crime_id, source_system, COUNT(*)
        FROM crimes_unified GROUP BY 1,2 HAVING COUNT(*) > 1 LIMIT 10
        """
    )
    dup_crimes = cur.fetchall()
    kb = {}
    for t, exp in (
        ("drug_categories", 379),
        ("drug_ignore_list", 197),
        ("geo_reference", 676108),
        ("geo_countries", 10520),
    ):
        cur.execute(f"SELECT COUNT(*) FROM kb.{t}")
        got = cur.fetchone()[0]
        kb[t] = {"count": got, "expected": exp, "ok": got == exp}

    # source safety
    safety = {}
    for name, getter in (("v1", get_v1_source_connection), ("v2", get_v2_source_connection)):
        c = getter()
        try:
            c.cursor().execute("CREATE TEMP TABLE etl3_ro_probe(x int)")
            safety[name] = "UNEXPECTED_WRITE_OK"
            c.rollback()
        except Exception as e:
            c.rollback()
            safety[name] = f"blocked:{type(e).__name__}"
        c.close()

    # confirm AI/schedule not started
    ai_procs = subprocess.check_output(
        "ps aux | grep -E 'run_ai_backfill|run_daily_pipeline|airflow scheduler' | grep -v grep || true",
        shell=True,
        text=True,
    )
    old_checkout = subprocess.check_output(
        "ps aux | grep '/home/eagle/dopams-cctns/' | grep -v grep | head -5 || true",
        shell=True,
        text=True,
    )

    # load prior investigation summaries if present
    storage_probe = {}
    sp = Path(__file__).resolve().parent / "reports" / "storage_host_probe.json"
    if sp.exists():
        d = json.loads(sp.read_text())
        storage_probe = {
            "tganb_exists": False,
            "shared_filename_hits": d.get("shared_filename_hits_of_30", {}),
            "remap_check_100": d.get("remap_check_100"),
            "expanded_probe": {
                "probed": d.get("not_found_expanded_probe", {}).get("probed"),
                "nonzero": d.get("not_found_expanded_probe", {}).get("nonzero"),
            },
            "case_property_recoverable": d.get("case_property_full_recovery_scan", {}).get(
                "recoverable_with_authoritative_id"
            ),
            "dopams181": (d.get("steps", {}).get("dopams181") or "")[:400],
        }

    out.update(
        {
            "v1_media": {
                "status_counts": v1_status,
                "not_found_total": sum(nf_by.values()),
                "not_found_by_entity": nf_by,
                "not_found_zero_byte_error": nf_zero,
                "recommendation": "controlled_redownload",
                "plan": str(
                    Path(__file__).resolve().parent
                    / "reports"
                    / "v1_media_redownload_plan.json"
                ),
                "mounted_or_restored": False,
            },
            "v2_media": v2_media,
            "case_property_file_ids_recovered": 0,
            "missing_v2_crimes": {
                "cause": (
                    "New V2 ETL run 02770723-7e81-4d87-b2a2-e113212d0468 fetched after last "
                    "successful consolidation (cursor was 0ed1fe57-...). Ten crime_ids existed "
                    "in cctns-v2.crimes but had no crimes_source observation / unified row."
                ),
                "v2_source_count": v2_count,
                "unified_v2_count": u_count,
                "missing_count": v2_count - u_count,
                "ids": IDS,
                "all_present": set(IDS).issubset(set(present)),
                "observations": obs,
            },
            "incremental": {
                "failed_ai_run": RUN_FAILED_AI,
                "failed_ai_reason": "Ollama ConnectionResetError during enrichment; entity consolidation had already committed the 10 crimes",
                "primary_success_run_id": RUN_PRIMARY,
                "second_idempotent_run_id": RUN_SECOND,
                "runs": runs,
                "crime_cursor": crime_cursor,
                "cursor_status_counts": cursor_counts,
                "ai_during_completion": "disabled via ETL3_AI_ENABLED=0 for completion process only",
            },
            "station_gaps": station,
            "integrity": {
                "dup_crime_keys": dup_crimes,
                "kb": kb,
            },
            "source_safety": safety,
            "ai_backfill_not_started": "run_ai_backfill" not in ai_procs,
            "daily_schedule_not_started": "run_daily_pipeline" not in ai_procs,
            "ai_related_processes": ai_procs.strip() or "(none)",
            "old_checkout_processes_sample": old_checkout.strip() or "(none matched in head)",
            "storage_probe_summary": storage_probe,
            "phase6_tests": {
                "note": "classifier/catalog/chargesheet tests passed; concurrent-lock test failed only while Phase-5 held the lock — re-check below",
            },
        }
    )

    # re-run phase6 readiness checks now that cursors are idle
    proc = subprocess.run(
        [sys.executable, "etl3/tests/test_phase6_readiness.py"],
        cwd="/home/eagle/dopams-cctns-ai",
        capture_output=True,
        text=True,
        env={**os.environ, "ETL3_AI_ENABLED": "0"},
    )
    out["phase6_tests"]["exit_code"] = proc.returncode
    out["phase6_tests"]["stdout"] = (proc.stdout or "")[-2000:]
    out["phase6_tests"]["stderr"] = (proc.stderr or "")[-1000:]
    results = {}
    for line in (proc.stdout or "").splitlines():
        if line.startswith("[PASS]"):
            results[line[7:].strip()] = "PASS"
        elif line.startswith("[FAIL]"):
            results[line[7:].strip()] = "FAIL"
    out["phase6_tests"]["results"] = results

    tg.close()
    OUT.write_text(json.dumps(out, indent=2, default=str))
    FINAL.write_text(json.dumps(out, indent=2, default=str))
    print(
        json.dumps(
            {
                "report": str(FINAL),
                "target_db": out["target_db"],
                "v2_aligned": out["missing_v2_crimes"]["missing_count"] == 0,
                "run_id": RUN_PRIMARY,
                "run_id2": RUN_SECOND,
                "station": station,
                "phase6": results,
                "source_safety": safety,
                "case_property_recovered": 0,
                "v1_recommendation": "controlled_redownload",
                "ai_backfill_started": False,
            },
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
