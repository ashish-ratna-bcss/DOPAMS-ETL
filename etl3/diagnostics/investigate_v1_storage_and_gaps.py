"""Investigate V1 media storage location + full 514 NOT_FOUND + missing V2 crimes.

Read-mostly against sources/target. Does not start AI backfill.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "etl3"))

from config import settings
from db.connections import (
    get_unified_connection,
    get_v1_source_connection,
    get_v2_source_connection,
)
from dotenv import dotenv_values
from sync.reconcile import classify_gap

REPORT = Path(__file__).resolve().parent / "reports"
REPORT.mkdir(parents=True, exist_ok=True)


def sh(cmd: str) -> str:
    try:
        return subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.STDOUT)[:4000]
    except subprocess.CalledProcessError as e:
        return (e.output or str(e))[:4000]


def probe_alfresco(url: str, path: str, name: str) -> dict:
    full = f"{url}?{urlencode({'path': path.strip().lstrip('/'), 'name': name.strip()})}"
    try:
        with urllib.request.urlopen(full, timeout=45) as resp:
            data = resp.read()
            return {"http": getattr(resp, "status", 200), "bytes": len(data)}
    except urllib.error.HTTPError as e:
        body = e.read() or b""
        return {"http": e.code, "bytes": len(body)}
    except Exception as e:
        return {"error": str(e)}


def main():
    assert settings.EXPECTED_UNIFIED_DBNAME == "dopams_cctns_v2"
    out = {
        "host": socket.gethostname(),
        "target": settings.EXPECTED_UNIFIED_DBNAME,
        "storage": {},
        "v1_not_found": {},
        "missing_v2_crimes": {},
        "station_gaps": {},
        "v2_media": {},
    }

    # --- mounts / paths ---
    out["storage"]["df"] = sh("df -h /home/eagle/media_cctnsv1 /mnt/shared-etl-files / 2>&1")
    out["storage"]["mounts"] = sh("mount | grep -E 'nfs|cifs|shared|tganb|media' || true")
    out["storage"]["find_media_dirs"] = sh(
        "find /home /mnt /data /opt /var -maxdepth 4 -type d -name 'media_cctnsv1' 2>/dev/null | head -20"
    )
    out["storage"]["tganb_exists"] = os.path.exists("/home/tganb")
    out["storage"]["tganb_media_exists"] = os.path.isdir("/home/tganb/dopams/media_cctnsv1")
    out["storage"]["eagle_media_exists"] = os.path.isdir("/home/eagle/media_cctnsv1")
    out["storage"]["eagle_media_count"] = (
        len(list(Path("/home/eagle/media_cctnsv1").rglob("*")))
        if os.path.isdir("/home/eagle/media_cctnsv1") else 0
    )
    # known hosts from env/docs
    out["storage"]["getent_tganb"] = sh("getent passwd tganb || true")
    out["storage"]["ssh_config_hosts"] = sh(
        "grep -E 'Host |HostName |tganb|dopams' ~/.ssh/config /etc/ssh/ssh_config 2>/dev/null | head -40 || true"
    )

    v1_env = dotenv_values(ROOT / "cctns-v1" / "CCTNSV1_DAILY_ETL_RUN" / ".env")
    out["storage"]["MEDIA_BASE_DIR"] = v1_env.get("MEDIA_BASE_DIR")
    out["storage"]["ALFRESCO"] = v1_env.get("ALFRESCO_DOWNLOAD_API_URL")

    v1 = get_v1_source_connection()
    cur = v1.cursor()
    # write probe
    try:
        cur.execute("CREATE TEMP TABLE etl3_ro_probe(x int)")
        out["v1_write"] = "UNEXPECTED_OK"
        v1.rollback()
    except Exception as e:
        v1.rollback()
        out["v1_write"] = f"blocked:{type(e).__name__}"

    cur.execute(
        """
        SELECT status, COUNT(*),
               COUNT(*) FILTER (WHERE local_path LIKE '/home/tganb/%'),
               COUNT(*) FILTER (WHERE local_path LIKE '/home/eagle/%')
        FROM cctns.cctns_media_files GROUP BY 1 ORDER BY 1
        """
    )
    out["v1_status"] = [
        {"status": s, "count": n, "tganb_paths": t, "eagle_paths": e}
        for s, n, t, e in cur.fetchall()
    ]

    # ALL 514 NOT_FOUND
    cur.execute(
        """
        SELECT media_id, entity_type, fir_reg_num, attach_path, dms_file_name,
               status, error_message, file_size_bytes, local_path
        FROM cctns.cctns_media_files
        WHERE status = 'NOT_FOUND'
        ORDER BY media_id
        """
    )
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    not_found = [dict(zip(cols, r)) for r in rows]
    out["v1_not_found"]["count"] = len(not_found)
    out["v1_not_found"]["by_entity"] = {}
    for r in not_found:
        out["v1_not_found"]["by_entity"][r["entity_type"]] = (
            out["v1_not_found"]["by_entity"].get(r["entity_type"], 0) + 1
        )
    # error buckets
    buckets = {}
    for r in not_found:
        key = (r.get("error_message") or "")[:100]
        buckets[key] = buckets.get(key, 0) + 1
    out["v1_not_found"]["error_buckets"] = buckets

    # Probe ALL 514 is expensive; probe stratified: first 20 FIR + first 20 COURT + last 10
    alfresco = (v1_env.get("ALFRESCO_DOWNLOAD_API_URL") or "").strip()
    fir = [r for r in not_found if r["entity_type"] == "FIR"]
    court = [r for r in not_found if r["entity_type"] == "COURT"]
    sample = fir[:25] + court[:25] + fir[-5:] + court[-5:]
    # dedupe by media_id
    seen = set()
    sample_unique = []
    for r in sample:
        if r["media_id"] not in seen:
            seen.add(r["media_id"])
            sample_unique.append(r)
    probes = []
    nonzero = 0
    for r in sample_unique:
        res = probe_alfresco(alfresco, r["attach_path"], r["dms_file_name"])
        res["media_id"] = r["media_id"]
        res["entity_type"] = r["entity_type"]
        probes.append(res)
        if res.get("bytes", 0) > 0:
            nonzero += 1
    out["v1_not_found"]["probed"] = len(probes)
    out["v1_not_found"]["probed_nonzero"] = nonzero
    out["v1_not_found"]["probe_results"] = probes

    # Check if any NOT_FOUND path exists under eagle or tganb
    present_elsewhere = 0
    for r in not_found[:100]:
        # reconstruct expected destination under configured MEDIA_BASE_DIR
        base = v1_env.get("MEDIA_BASE_DIR") or "/home/eagle/media_cctnsv1"
        attach = (r["attach_path"] or "").strip().lstrip("/\\")
        name = (r["dms_file_name"] or "").strip().lstrip("/\\")
        candidates = [
            os.path.join(base, attach, name),
            os.path.join("/home/tganb/dopams/media_cctnsv1", attach, name),
        ]
        if any(os.path.isfile(p) and os.path.getsize(p) > 0 for p in candidates):
            present_elsewhere += 1
    out["v1_not_found"]["present_on_local_candidates_of_first_100"] = present_elsewhere

    # Sample DOWNLOADED path existence on this host
    cur.execute(
        """
        SELECT local_path, file_size_bytes FROM cctns.cctns_media_files
        WHERE status='DOWNLOADED' AND local_path IS NOT NULL
        ORDER BY media_id LIMIT 50
        """
    )
    dl_ok = dl_missing = 0
    for path, sz in cur.fetchall():
        if path and os.path.isfile(path) and os.path.getsize(path) > 0:
            dl_ok += 1
        else:
            # try remap to MEDIA_BASE_DIR
            alt = None
            if path and "/media_cctnsv1/" in path:
                suffix = path.split("/media_cctnsv1/", 1)[1]
                alt = os.path.join(v1_env.get("MEDIA_BASE_DIR") or "/home/eagle/media_cctnsv1", suffix)
            if alt and os.path.isfile(alt) and os.path.getsize(alt) > 0:
                dl_ok += 1
            else:
                dl_missing += 1
    out["storage"]["downloaded_sample50"] = {"found": dl_ok, "missing": dl_missing}
    v1.close()

    # --- V2 media summary ---
    v2 = get_v2_source_connection()
    cur = v2.cursor()
    try:
        cur.execute("CREATE TEMP TABLE etl3_ro_probe(x int)")
        out["v2_write"] = "UNEXPECTED_OK"
        v2.rollback()
    except Exception as e:
        v2.rollback()
        out["v2_write"] = f"blocked:{type(e).__name__}"

    cur.execute(
        """
        SELECT source_type, source_field,
               COUNT(*) total,
               COUNT(*) FILTER (WHERE is_downloaded IS TRUE) downloaded,
               COUNT(*) FILTER (WHERE COALESCE(is_empty,false)) empty_flag,
               COUNT(*) FILTER (WHERE download_error ILIKE '%%400%%') http400,
               COUNT(*) FILTER (WHERE download_error LIKE 'SOURCE:%%') source_annot,
               COUNT(*) FILTER (WHERE download_error LIKE 'PERMANENT:%%') permanent,
               COUNT(*) FILTER (WHERE file_id IS NULL AND is_downloaded IS NOT TRUE
                                    AND COALESCE(is_empty,false) IS NOT TRUE) null_file_id_pending
        FROM public.file_media_bookkeeping
        GROUP BY 1,2 ORDER BY 1,2
        """
    )
    out["v2_media"]["by_type"] = [
        {
            "source_type": a, "source_field": b, "total": t, "downloaded": d,
            "empty": e, "http400": h, "source_annot": s, "permanent": p, "null_file_id_pending": n,
        }
        for a, b, t, d, e, h, s, p, n in cur.fetchall()
    ]

    # case_property: can source API expose file ids? inspect property media columns sample
    cur.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema='public' AND table_name IN ('properties','fsl_case_property','case_property')
        ORDER BY table_name, ordinal_position
        """
    )
    out["v2_media"]["property_columns"] = [r[0] for r in cur.fetchall()]

    # Missing V2 crimes
    cur.execute("SELECT COUNT(*) FROM public.crimes")
    v2_count = cur.fetchone()[0]
    cur.execute("SELECT crime_id::text FROM public.crimes")
    v2_ids = {r[0] for r in cur.fetchall()}
    v2.close()

    tg = get_unified_connection(readonly=True)
    cur = tg.cursor()
    cur.execute("SELECT COUNT(*) FROM crimes_unified WHERE source_system='V2'")
    u_count = cur.fetchone()[0]
    cur.execute("SELECT crime_id FROM crimes_unified WHERE source_system='V2'")
    u_ids = {r[0] for r in cur.fetchall()}
    missing = sorted(v2_ids - u_ids)
    extra = sorted(u_ids - v2_ids)
    out["missing_v2_crimes"] = {
        "v2_source_count": v2_count,
        "unified_v2_count": u_count,
        "missing_count": len(missing),
        "missing_ids": missing,
        "extra_in_unified_not_in_source": extra[:20],
        "extra_count": len(extra),
    }
    # observations for missing
    if missing:
        cur.execute(
            """
            SELECT source_record_id, COUNT(*)
            FROM crimes_source
            WHERE source_system='V2' AND source_record_id = ANY(%s)
            GROUP BY 1 ORDER BY 1
            """,
            (missing,),
        )
        out["missing_v2_crimes"]["in_crimes_source"] = dict(cur.fetchall())
        cur.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema='public' AND table_name='consolidation_cursor'
            ORDER BY ordinal_position
            """
        )
        cc_cols = [r[0] for r in cur.fetchall()]
        out["missing_v2_crimes"]["cursor_columns"] = cc_cols
        cur.execute("SELECT * FROM consolidation_cursor WHERE source_system='V2'")
        out["missing_v2_crimes"]["cursors"] = [
            dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()
        ]

    # station gaps
    cur.execute(
        """
        SELECT gap_key, status FROM source_gap_ledger
        WHERE gap_type='ambiguous_v1_station_name' ORDER BY gap_key
        """
    )
    station = cur.fetchall()
    out["station_gaps"] = {
        "count": len(station),
        "classification": classify_gap("ambiguous_v1_station_name"),
        "all_open": all(s == "OPEN" for _, s in station),
        "keys": [k for k, _ in station],
    }

    # KB counts
    for t, nexp in (
        ("drug_categories", 379),
        ("drug_ignore_list", 197),
        ("geo_reference", 676108),
        ("geo_countries", 10520),
    ):
        cur.execute(f"SELECT COUNT(*) FROM kb.{t}")
        got = cur.fetchone()[0]
        out.setdefault("kb", {})[t] = {"count": got, "expected": nexp, "ok": got == nexp}
    tg.close()

    path = REPORT / "storage_and_gaps_investigation.json"
    path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(json.dumps({
        "report": str(path),
        "tganb_media": out["storage"]["tganb_media_exists"],
        "eagle_media_count": out["storage"]["eagle_media_count"],
        "downloaded_sample50": out["storage"]["downloaded_sample50"],
        "not_found_count": out["v1_not_found"]["count"],
        "not_found_by_entity": out["v1_not_found"]["by_entity"],
        "probed": out["v1_not_found"]["probed"],
        "probed_nonzero": out["v1_not_found"]["probed_nonzero"],
        "missing_v2": out["missing_v2_crimes"]["missing_count"],
        "missing_ids": out["missing_v2_crimes"]["missing_ids"],
        "station_count": out["station_gaps"]["count"],
        "station_class": out["station_gaps"]["classification"],
        "v1_write": out["v1_write"],
        "v2_write": out["v2_write"],
    }, indent=2, default=str))


if __name__ == "__main__":
    main()
