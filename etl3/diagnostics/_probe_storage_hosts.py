"""Locate V1 media on shared NFS / peer hosts; deepen 514 verification; inspect missing crimes."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "etl3"))

from dotenv import dotenv_values
from db.connections import (
    get_unified_connection,
    get_v1_source_connection,
    get_v2_source_connection,
)

OUT = Path(__file__).resolve().parent / "reports" / "storage_host_probe.json"
OUT.parent.mkdir(parents=True, exist_ok=True)


def sh(cmd: str, timeout: int = 90) -> str:
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return ((r.stdout or "") + (r.stderr or ""))[:8000]
    except Exception as e:
        return f"ERR:{e}"


def main():
    v1_env = dotenv_values(ROOT / "etl3" / "config" / "source_envs" / "v1.env")
    out: dict = {"steps": {}}

    out["steps"]["shared_ls"] = sh("ls -la /mnt/shared-etl-files")
    out["steps"]["find_named"] = sh(
        "find /mnt/shared-etl-files -maxdepth 4 "
        r"\( -iname '*media*cctns*' -o -iname '*cctnsv1*' -o -iname '*tganb*' -o -iname '*alfresco*' \) "
        "2>/dev/null | head -80"
    )
    out["steps"]["find_home_media"] = sh(
        "find /home /mnt /data /opt -maxdepth 5 -type d -name 'media_cctnsv1' 2>/dev/null | head -20"
    )
    out["steps"]["dopams181"] = sh(
        "ssh -o ConnectTimeout=8 -o BatchMode=yes -o StrictHostKeyChecking=accept-new dopams181 "
        "'hostname; "
        "ls -ld /home/tganb/dopams/media_cctnsv1 /home/eagle/media_cctnsv1 2>&1; "
        "find /home /mnt /data -maxdepth 4 -type d -name media_cctnsv1 2>/dev/null | head; "
        "df -h 2>/dev/null | head -15'"
    )

    # --- V1 media bookkeeping ---
    v1 = get_v1_source_connection()
    cur = v1.cursor()
    cur.execute(
        """
        SELECT local_path, file_size_bytes, dms_file_name, attach_path, media_id
        FROM cctns.cctns_media_files
        WHERE status='DOWNLOADED'
        ORDER BY media_id
        LIMIT 10
        """
    )
    out["downloaded_path_samples"] = [
        {
            "local_path": r[0],
            "size": r[1],
            "dms_file_name": r[2],
            "attach_path": r[3],
            "media_id": r[4],
        }
        for r in cur.fetchall()
    ]
    cur.execute(
        """
        SELECT COUNT(*), COALESCE(SUM(file_size_bytes),0)
        FROM cctns.cctns_media_files WHERE status='DOWNLOADED'
        """
    )
    n, total = cur.fetchone()
    out["downloaded_bookkeeping"] = {"count": n, "sum_size_bytes": int(total or 0)}

    cur.execute(
        """
        SELECT dms_file_name FROM cctns.cctns_media_files
        WHERE status='DOWNLOADED' AND dms_file_name IS NOT NULL
        ORDER BY media_id LIMIT 30
        """
    )
    names = [r[0] for r in cur.fetchall()]
    hits = []
    for name in names:
        if not name or any(c in name for c in "/\\'.."):
            continue
        res = sh(
            f"find /mnt/shared-etl-files -maxdepth 6 -type f -name {json.dumps(name)} 2>/dev/null | head -3",
            timeout=120,
        ).strip()
        if res and not res.startswith("ERR:"):
            hits.append({"name": name, "paths": res.splitlines()[:3]})
    out["shared_filename_hits_of_30"] = {"hit_count": len(hits), "hits": hits[:10]}

    base = v1_env.get("MEDIA_BASE_DIR") or "/home/eagle/media_cctnsv1"
    cur.execute(
        """
        SELECT local_path, file_size_bytes FROM cctns.cctns_media_files
        WHERE status='DOWNLOADED' AND local_path LIKE '/home/tganb/%'
        ORDER BY media_id LIMIT 100
        """
    )
    remap = {"checked": 0, "exists_original": 0, "exists_remapped": 0}
    for path, _size in cur.fetchall():
        remap["checked"] += 1
        if path and os.path.isfile(path) and os.path.getsize(path) > 0:
            remap["exists_original"] += 1
        if path and "/media_cctnsv1/" in path:
            suffix = path.split("/media_cctnsv1/", 1)[1]
            alt = os.path.join(base, suffix)
            if os.path.isfile(alt) and os.path.getsize(alt) > 0:
                remap["exists_remapped"] += 1
    out["remap_check_100"] = remap

    alfresco = (
        v1_env.get("ALFRESCO_DOWNLOAD_API_URL") or "http://103.164.200.184/alfresco/download"
    ).rstrip("/")
    cur.execute(
        """
        SELECT media_id, entity_type, fir_reg_num, attach_path, dms_file_name,
               status, local_path, file_size_bytes, error_message, updated_at
        FROM cctns.cctns_media_files
        WHERE status='NOT_FOUND'
        ORDER BY entity_type, media_id
        """
    )
    cols = [d[0] for d in cur.description]
    not_found = [dict(zip(cols, r)) for r in cur.fetchall()]
    out["not_found_all"] = {
        "count": len(not_found),
        "by_entity": {},
        "error_messages": {},
        "records_summary": [
            {
                "media_id": r["media_id"],
                "entity_type": r["entity_type"],
                "fir_reg_num": r["fir_reg_num"],
                "attach_path": r["attach_path"],
                "dms_file_name": r["dms_file_name"],
                "error_message": r["error_message"],
            }
            for r in not_found
        ],
    }
    for r in not_found:
        et = r["entity_type"] or "?"
        out["not_found_all"]["by_entity"][et] = out["not_found_all"]["by_entity"].get(et, 0) + 1
        em = (r["error_message"] or "")[:120]
        out["not_found_all"]["error_messages"][em] = out["not_found_all"]["error_messages"].get(em, 0) + 1

    probe_ids = set()
    for i, r in enumerate(not_found):
        if i % 8 == 0:
            probe_ids.add(r["media_id"])
    by_ent: dict[str, list] = {}
    for r in not_found:
        by_ent.setdefault(r["entity_type"] or "?", []).append(r)
    for rows in by_ent.values():
        for r in rows[:10] + rows[-10:]:
            probe_ids.add(r["media_id"])

    probe_results = []
    nonzero = 0
    for r in not_found:
        if r["media_id"] not in probe_ids:
            continue
        path = (r["attach_path"] or "").strip().lstrip("/\\")
        name = (r["dms_file_name"] or "").strip()
        if not path or not name:
            probe_results.append({"media_id": r["media_id"], "error": "missing_path_or_name"})
            continue
        url = f"{alfresco}?{urlencode({'path': path, 'name': name})}"
        try:
            with urllib.request.urlopen(url, timeout=45) as resp:
                data = resp.read()
                nbytes = len(data)
                if nbytes > 0:
                    nonzero += 1
                probe_results.append(
                    {
                        "media_id": r["media_id"],
                        "entity_type": r["entity_type"],
                        "http": getattr(resp, "status", 200),
                        "bytes": nbytes,
                    }
                )
        except urllib.error.HTTPError as e:
            body = e.read() or b""
            probe_results.append(
                {
                    "media_id": r["media_id"],
                    "entity_type": r["entity_type"],
                    "http": e.code,
                    "bytes": len(body),
                }
            )
        except Exception as e:
            probe_results.append({"media_id": r["media_id"], "error": str(e)[:200]})
    out["not_found_expanded_probe"] = {
        "probed": len(probe_results),
        "nonzero": nonzero,
        "results": probe_results,
    }
    v1.close()

    # --- Missing V2 crimes + case_property media ---
    missing_ids = [
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
    v2 = get_v2_source_connection()
    vc = v2.cursor()
    vc.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema='public' AND table_name='crimes' ORDER BY ordinal_position
        """
    )
    crime_cols = [r[0] for r in vc.fetchall()]
    want = [
        c
        for c in [
            "crime_id",
            "fir_number",
            "fir_no",
            "police_station",
            "ps_code",
            "date_created",
            "date_modified",
            "etl_run_id",
            "fetched_at",
            "status",
        ]
        if c in crime_cols
    ]
    crime_details = []
    if want:
        sel = ", ".join(want)
        for mid in missing_ids:
            vc.execute(f"SELECT {sel} FROM crimes WHERE crime_id::text=%s", (mid,))
            row = vc.fetchone()
            crime_details.append(
                {"id": mid, "found": row is not None, "row": dict(zip(want, row)) if row else None}
            )
    out["missing_crime_source_details"] = crime_details

    vc.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema='public' AND table_name='case_property'
        """
    )
    cp_cols = [r[0] for r in vc.fetchall()]
    out["case_property_cols"] = cp_cols
    if "media" in cp_cols:
        vc.execute(
            """
            SELECT case_property_id, media
            FROM case_property
            WHERE media IS NOT NULL AND media::text NOT IN ('','null','[]','{}')
            LIMIT 20
            """
        )
        out["case_property_media_samples"] = [
            {"case_property_id": cid, "media_preview": str(media)[:500]}
            for cid, media in vc.fetchall()
        ]
        vc.execute("SELECT COUNT(*) FROM case_property WHERE media IS NOT NULL")
        out["case_property_media_nonnull"] = vc.fetchone()[0]

    vc.execute(
        """
        SELECT id, parent_id, file_id, download_error, is_downloaded, is_empty
        FROM public.file_media_bookkeeping
        WHERE source_type='case_property' AND source_field='MEDIA'
          AND file_id IS NULL
          AND download_error LIKE 'SOURCE: MEDIA present but file_id is null%%'
        """
    )
    all_bk = [dict(zip([d[0] for d in vc.description], r)) for r in vc.fetchall()]
    out["null_file_id_bookkeeping_count"] = len(all_bk)
    out["null_file_id_bookkeeping_sample"] = all_bk[:20]

    recoverable_total = 0
    unrecoverable = 0
    media_shapes: dict[str, int] = {}
    recovered_samples = []
    if "media" in cp_cols:
        for br in all_bk:
            pid = br["parent_id"]
            vc.execute("SELECT media FROM case_property WHERE case_property_id::text=%s", (str(pid),))
            row = vc.fetchone()
            if not row or row[0] is None:
                unrecoverable += 1
                media_shapes["missing_in_source"] = media_shapes.get("missing_in_source", 0) + 1
                continue
            media = row[0]
            shape = type(media).__name__
            if isinstance(media, dict):
                shape = "dict:" + ",".join(sorted(str(k) for k in list(media.keys())[:12]))
            elif isinstance(media, list):
                shape = f"list_len_{len(media)}"
                if media and isinstance(media[0], dict):
                    shape += ":" + ",".join(sorted(str(k) for k in list(media[0].keys())[:12]))
            media_shapes[shape] = media_shapes.get(shape, 0) + 1
            fid = None
            if isinstance(media, dict):
                for k in ("file_id", "fileId", "_id", "id", "media_id", "documentId"):
                    if media.get(k):
                        fid = media.get(k)
                        break
            elif isinstance(media, list) and media:
                first = media[0]
                if isinstance(first, dict):
                    for k in ("file_id", "fileId", "_id", "id"):
                        if first.get(k):
                            fid = first.get(k)
                            break
                elif isinstance(first, str) and len(first.strip()) >= 20:
                    fid = first.strip()
            if fid:
                recoverable_total += 1
                if len(recovered_samples) < 10:
                    recovered_samples.append(
                        {"parent_id": pid, "file_id": str(fid)[:80], "media_type": type(media).__name__}
                    )
            else:
                unrecoverable += 1
    out["case_property_full_recovery_scan"] = {
        "null_file_id_count": len(all_bk),
        "recoverable_with_authoritative_id": recoverable_total,
        "unrecoverable": unrecoverable,
        "media_shapes_top": dict(sorted(media_shapes.items(), key=lambda x: -x[1])[:20]),
        "samples": recovered_samples,
    }

    vc.execute(
        """
        SELECT source_type, source_field, COUNT(*)
        FROM public.file_media_bookkeeping
        WHERE download_error LIKE 'PERMANENT:%%'
        GROUP BY 1,2 ORDER BY 1,2
        """
    )
    out["v2_permanent_by_type"] = [
        {"source_type": a, "source_field": b, "count": c} for a, b, c in vc.fetchall()
    ]
    vc.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema='public' AND table_name='file_media_bookkeeping'
        ORDER BY ordinal_position
        """
    )
    fmb_cols = [r[0] for r in vc.fetchall()]
    out["file_media_bookkeeping_cols"] = fmb_cols
    path_col = next(
        (c for c in ("local_path", "file_path", "stored_path", "disk_path", "path") if c in fmb_cols),
        None,
    )
    sel_cols = ["id", "file_id", "download_error"] + ([path_col] if path_col else [])
    vc.execute(
        f"""
        SELECT {', '.join(sel_cols)}
        FROM public.file_media_bookkeeping
        WHERE source_type='person' AND source_field='IDENTITY_DETAILS'
          AND (download_error ILIKE '%%missing%%' OR download_error ILIKE '%%not found on disk%%'
               OR download_error ILIKE '%%disk%%' OR download_error ILIKE '%%404%%'
               OR download_error LIKE 'PERMANENT:%%')
        LIMIT 20
        """
    )
    out["identity_disk_errors_sample"] = [
        dict(zip([d[0] for d in vc.description], r)) for r in vc.fetchall()
    ]
    v2.close()

    tg = get_unified_connection(readonly=True)
    cur = tg.cursor()
    cur.execute(
        """
        SELECT source_record_id, COUNT(*)
        FROM crimes_source
        WHERE source_system='V2' AND source_record_id = ANY(%s)
        GROUP BY 1 ORDER BY 1
        """,
        (missing_ids,),
    )
    out["missing_in_crimes_source"] = dict(cur.fetchall())
    cur.execute(
        """
        SELECT crime_id FROM crimes_unified
        WHERE source_system='V2' AND crime_id = ANY(%s)
        """,
        (missing_ids,),
    )
    out["missing_already_in_unified"] = [r[0] for r in cur.fetchall()]
    # recent run log
    cur.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema='public' AND table_name='consolidation_run_log'
        ORDER BY ordinal_position
        """
    )
    rl_cols = [r[0] for r in cur.fetchall()]
    out["run_log_columns"] = rl_cols
    if rl_cols:
        cur.execute(
            "SELECT * FROM consolidation_run_log ORDER BY 1 DESC LIMIT 5"
        )
        out["recent_runs"] = [
            dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()
        ]
    tg.close()

    OUT.write_text(json.dumps(out, indent=2, default=str))
    print(
        json.dumps(
            {
                "report": str(OUT),
                "shared_hits": out["shared_filename_hits_of_30"]["hit_count"],
                "remap": out["remap_check_100"],
                "not_found": out["not_found_all"]["count"],
                "expanded_probe_nonzero": out["not_found_expanded_probe"]["nonzero"],
                "expanded_probed": out["not_found_expanded_probe"]["probed"],
                "dopams181_snippet": out["steps"]["dopams181"][:400],
                "case_property_recoverable": out["case_property_full_recovery_scan"][
                    "recoverable_with_authoritative_id"
                ],
                "missing_crimes_in_source": sum(1 for x in crime_details if x["found"]),
                "missing_in_obs": out["missing_in_crimes_source"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
