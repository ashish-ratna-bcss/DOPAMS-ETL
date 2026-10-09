"""Audit cctns-v2.public.file_media_bookkeeping + V1 media path mounts."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "etl3"))

from db.connections import get_v1_source_connection, get_v2_source_connection

TABLE = "public.file_media_bookkeeping"


def main():
    print("MOUNT_CHECKS")
    for p in (
        "/home/tganb/dopams/media_cctnsv1",
        "/home/eagle/media_cctnsv1",
        "/mnt/shared-etl-files",
        "/mnt/shared-etl-files/crimes",
        "/mnt/shared-etl-files/chargesheets",
    ):
        print(p, "isdir=", os.path.isdir(p), "islink=", os.path.islink(p))
        if os.path.isdir(p):
            try:
                print("  entries=", len(os.listdir(p)[:5]), "sample=", os.listdir(p)[:5])
            except Exception as e:
                print("  list_err", e)

    v1 = get_v1_source_connection()
    cur = v1.cursor()
    cur.execute(
        """
        SELECT COUNT(*) FILTER (WHERE local_path LIKE '/home/tganb/%'),
               COUNT(*) FILTER (WHERE local_path LIKE '/home/eagle/%'),
               COUNT(*) FILTER (WHERE local_path IS NOT NULL)
        FROM cctns.cctns_media_files
        """
    )
    print("V1_PATH_PREFIXES", cur.fetchone())
    # remap check: replace prefix and see if file exists on eagle or shared
    cur.execute(
        """
        SELECT local_path, file_size_bytes FROM cctns.cctns_media_files
        WHERE status='DOWNLOADED' AND local_path IS NOT NULL
        LIMIT 20
        """
    )
    found_eagle = found_shared = missing = 0
    for path, sz in cur.fetchall():
        alt = path.replace("/home/tganb/dopams/media_cctnsv1", "/home/eagle/media_cctnsv1")
        # shared mount unlikely to mirror FIR structure of V1
        if os.path.isfile(path):
            found_eagle += 1  # original
        elif os.path.isfile(alt):
            found_eagle += 1
        else:
            missing += 1
    print("V1_REMAP_SAMPLE20 original_or_eagle_found=", found_eagle, "missing=", missing)
    v1.close()

    v2 = get_v2_source_connection()
    cur = v2.cursor()
    cur.execute(
        f"""
        SELECT source_type, source_field,
               COUNT(*) AS total,
               COUNT(*) FILTER (WHERE is_downloaded IS TRUE) AS downloaded,
               COUNT(*) FILTER (WHERE COALESCE(is_empty,false) IS TRUE) AS empty_flag,
               COUNT(*) FILTER (WHERE (is_downloaded IS NOT TRUE) AND COALESCE(is_empty,false) IS NOT TRUE) AS pendingish,
               COUNT(*) FILTER (WHERE download_error IS NOT NULL AND btrim(download_error) <> '') AS with_error,
               COUNT(*) FILTER (WHERE download_error ILIKE '%%400%%') AS http400
        FROM {TABLE}
        GROUP BY 1,2
        ORDER BY 1,2
        """
    )
    rows = cur.fetchall()
    print("V2_BY_SOURCE_FIELD")
    summary = []
    for r in rows:
        item = {
            "source_type": r[0], "source_field": r[1], "total": r[2],
            "downloaded": r[3], "empty": r[4], "pendingish": r[5],
            "with_error": r[6], "http400": r[7],
        }
        summary.append(item)
        print(item)
    cur.execute(
        f"""
        SELECT source_type, source_field, left(COALESCE(download_error,''),120), COUNT(*)
        FROM {TABLE}
        WHERE download_error IS NOT NULL AND btrim(download_error) <> ''
        GROUP BY 1,2,3
        ORDER BY 4 DESC
        LIMIT 40
        """
    )
    print("V2_ERROR_BUCKETS")
    for r in cur.fetchall():
        print(r)
    # disk check for downloaded sample
    cur.execute(
        f"""
        SELECT file_id, source_type, source_field, file_path
        FROM {TABLE}
        WHERE is_downloaded IS TRUE
        LIMIT 30
        """
    )
    present = missing_disk = 0
    for file_id, st, sf, fpath in cur.fetchall():
        candidates = []
        if fpath:
            candidates.append(fpath)
        # common layout under FILES_MEDIA_BASE_PATH
        base = "/mnt/shared-etl-files"
        mapping = {
            ("crime", "FIR_COPY"): "crimes",
            ("chargesheets", "CHARGESHEET_COPY"): "chargesheets",
            ("chargesheet", "CHARGESHEET_COPY"): "chargesheets",
            ("interrogation", "INTERROGATION_REPORT"): "interrogations/interrogationreport",
            ("interrogation", "MEDIA"): "interrogations/media",
            ("person", "IDENTITY_DETAILS"): "person/identitydetails",
            ("person", "MEDIA"): "person/media",
            ("property", "MEDIA"): "property",
            ("case_property", "MEDIA"): "case_property",
            ("mo_seizures", "MEDIA"): "mo_seizures",
        }
        sub = mapping.get((st, sf))
        if sub and file_id:
            d = os.path.join(base, sub)
            if os.path.isdir(d):
                for name in os.listdir(d):
                    if name.startswith(str(file_id) + "."):
                        candidates.append(os.path.join(d, name))
                        break
        if any(os.path.isfile(c) for c in candidates):
            present += 1
        else:
            missing_disk += 1
    print("V2_DISK_SAMPLE30 present=", present, "missing=", missing_disk)
    out = Path("/home/eagle/dopams-cctns-ai/etl3/diagnostics/reports/v2_media_summary.json")
    out.write_text(json.dumps({"summary": summary}, indent=2), encoding="utf-8")
    print("WROTE", out)
    v2.close()


if __name__ == "__main__":
    main()
