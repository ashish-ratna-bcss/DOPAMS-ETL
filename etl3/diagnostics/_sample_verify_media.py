"""Sample-verify V1 NOT_FOUND and V2 HTTP 400 still fail at source (read-only API)."""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "etl3"))

from db.connections import get_v1_source_connection, get_v2_source_connection
from dotenv import dotenv_values


def main():
    v1_env = dotenv_values(ROOT / "cctns-v1" / "CCTNSV1_DAILY_ETL_RUN" / ".env")
    alfresco = (v1_env.get("ALFRESCO_DOWNLOAD_API_URL") or "").strip()
    v2_env = dotenv_values(ROOT / "cctns-v2" / ".env")
    files_url = (v2_env.get("FILES_BASE_URL") or "").rstrip("/")

    out = {"alfresco": alfresco, "files_url": files_url, "v1_samples": [], "v2_samples": []}

    v1 = get_v1_source_connection()
    cur = v1.cursor()
    cur.execute(
        """
        SELECT media_id, entity_type, attach_path, dms_file_name, error_message
        FROM cctns.cctns_media_files
        WHERE status='NOT_FOUND'
        ORDER BY media_id
        LIMIT 5
        """
    )
    for media_id, entity, path, name, err in cur.fetchall():
        item = {"media_id": media_id, "entity": entity, "prior_error": err}
        if alfresco:
            url = f"{alfresco}?{urlencode({'path': path.strip().lstrip('/'), 'name': name.strip()})}"
            try:
                with urllib.request.urlopen(url, timeout=30) as resp:
                    raw = resp.read()
                    item.update({"http": getattr(resp, "status", 200), "bytes": len(raw)})
            except urllib.error.HTTPError as e:
                item.update({"http": e.code, "bytes": len(e.read() or b"")})
            except Exception as e:
                item["probe_error"] = str(e)
        out["v1_samples"].append(item)
    v1.close()

    v2 = get_v2_source_connection()
    cur = v2.cursor()
    cur.execute(
        """
        SELECT id, source_type, source_field, file_id, download_error
        FROM public.file_media_bookkeeping
        WHERE download_error ILIKE '%400%'
        ORDER BY id
        LIMIT 5
        """
    )
    for rid, st, sf, file_id, err in cur.fetchall():
        item = {"id": rid, "source_type": st, "source_field": sf, "file_id": file_id, "prior_error": err}
        if files_url and file_id:
            url = f"{files_url}/{file_id}"
            try:
                req = urllib.request.Request(url)
                with urllib.request.urlopen(req, timeout=30) as resp:
                    body = resp.read(200).decode("utf-8", errors="replace")
                    item.update({"http": getattr(resp, "status", 200), "body": body})
            except urllib.error.HTTPError as e:
                item.update({
                    "http": e.code,
                    "body": (e.read(200) or b"").decode("utf-8", errors="replace"),
                })
            except Exception as e:
                item["probe_error"] = str(e)
        out["v2_samples"].append(item)

    # case_property pending sample (no download — metadata only)
    cur.execute(
        """
        SELECT COUNT(*), COUNT(file_id)
        FROM public.file_media_bookkeeping
        WHERE source_type='case_property' AND source_field='MEDIA'
          AND is_downloaded IS NOT TRUE AND COALESCE(is_empty,false) IS NOT TRUE
        """
    )
    out["case_property_pending"] = {"rows": cur.fetchone()[0]}
    cur.execute(
        """
        SELECT id, file_id, download_attempts, download_error
        FROM public.file_media_bookkeeping
        WHERE source_type='case_property' AND source_field='MEDIA'
          AND is_downloaded IS NOT TRUE AND COALESCE(is_empty,false) IS NOT TRUE
        ORDER BY id LIMIT 5
        """
    )
    out["case_property_samples"] = [
        {"id": a, "file_id": b, "attempts": c, "error": d} for a, b, c, d in cur.fetchall()
    ]
    v2.close()
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
