"""Run Phase-5 incremental once, then validate missing crimes + integrity gates.

Does not start AI backfill or daily scheduling.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "etl3"))

from db.connections import (
    get_unified_connection,
    get_v1_source_connection,
    get_v2_source_connection,
)
from run_phase5_incremental import run_incremental
from sync.reconcile import classify_gap

OUT = Path(__file__).resolve().parent / "reports" / "phase5_gap_remediation_validation.json"
OUT.parent.mkdir(parents=True, exist_ok=True)

MISSING_BEFORE = [
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


def counts(tg):
    cur = tg.cursor()
    cur.execute("SELECT COUNT(*) FROM crimes_unified WHERE source_system='V2'")
    u = cur.fetchone()[0]
    cur.execute(
        "SELECT crime_id FROM crimes_unified WHERE source_system='V2' AND crime_id = ANY(%s) ORDER BY 1",
        (MISSING_BEFORE,),
    )
    present = [r[0] for r in cur.fetchall()]
    cur.execute(
        """
        SELECT source_record_id, COUNT(*)
        FROM crimes_source
        WHERE source_system='V2' AND source_record_id = ANY(%s)
        GROUP BY 1 ORDER BY 1
        """,
        (MISSING_BEFORE,),
    )
    obs = dict(cur.fetchall())
    return u, present, obs


def main():
    out = {"steps": []}

    # Pre
    v2 = get_v2_source_connection()
    vc = v2.cursor()
    vc.execute("SELECT COUNT(*) FROM crimes")
    v2_count = vc.fetchone()[0]
    vc.execute("SELECT crime_id::text FROM crimes")
    v2_ids = {r[0] for r in vc.fetchall()}
    v2.close()

    tg = get_unified_connection()
    u_before, present_before, obs_before = counts(tg)
    cur = tg.cursor()
    cur.execute("SELECT crime_id FROM crimes_unified WHERE source_system='V2'")
    u_ids = {r[0] for r in cur.fetchall()}
    missing_before = sorted(v2_ids - u_ids)
    out["pre"] = {
        "v2_source_count": v2_count,
        "unified_v2_count": u_before,
        "missing_count": len(missing_before),
        "missing_ids": missing_before,
        "target_present": present_before,
        "obs": obs_before,
    }
    out["steps"].append("pre_ok")

    # Snapshot unrelated counts for regression
    snapshots = {}
    for table in (
        "crimes_unified",
        "accused_unified",
        "persons_unified",
        "arrests_unified",
        "chargesheets_unified",
        "kb.drug_categories",
        "kb.drug_ignore_list",
        "kb.geo_reference",
        "kb.geo_countries",
    ):
        try:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            snapshots[table] = cur.fetchone()[0]
        except Exception as e:
            tg.rollback()
            snapshots[table] = f"err:{e}"
    out["pre_snapshots"] = snapshots

    # Run incremental
    run_id = run_incremental(tg)
    out["incremental_run_id"] = run_id
    out["steps"].append("incremental_done")

    # Post
    u_after, present_after, obs_after = counts(tg)
    cur = tg.cursor()
    cur.execute("SELECT crime_id FROM crimes_unified WHERE source_system='V2'")
    u_ids_after = {r[0] for r in cur.fetchall()}
    missing_after = sorted(v2_ids - u_ids_after)
    out["post"] = {
        "unified_v2_count": u_after,
        "missing_count": len(missing_after),
        "missing_ids": missing_after,
        "target_present": present_after,
        "obs": obs_after,
        "all_ten_present": set(MISSING_BEFORE).issubset(set(present_after)),
    }

    post_snapshots = {}
    for table, before in snapshots.items():
        if isinstance(before, str):
            post_snapshots[table] = before
            continue
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        after = cur.fetchone()[0]
        post_snapshots[table] = {"before": before, "after": after, "delta": after - before}
    out["post_snapshots"] = post_snapshots

    # Second incremental should be idempotent for these keys
    run_id2 = run_incremental(tg)
    out["second_incremental_run_id"] = run_id2
    u_after2, present_after2, obs_after2 = counts(tg)
    out["second_post"] = {
        "unified_v2_count": u_after2,
        "target_present": present_after2,
        "obs": obs_after2,
        "unified_unchanged": u_after2 == u_after,
        "obs_counts_unchanged": obs_after2 == obs_after,
    }

    # Station gaps (open unresolved relationships)
    station = {"classification": classify_gap("ambiguous_v1_station_name"), "rows": []}
    cur.execute(
        """
        SELECT gap_key, gap_type, status
        FROM source_gap_ledger
        WHERE gap_type='ambiguous_v1_station_name'
        ORDER BY gap_key
        """
    )
    rows = [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]
    station["table"] = "source_gap_ledger"
    station["count"] = len(rows)
    station["rows"] = rows
    station["all_open"] = all((r.get("status") or "").upper() == "OPEN" for r in rows) if rows else True
    if rows:
        keys = [r["gap_key"] for r in rows]
        # inspect station fields without inventing codes
        cur.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema='public' AND table_name='crimes_unified'
            """
        )
        ccols = {r[0] for r in cur.fetchall()}
        ps_cols = [c for c in (
            "police_station_code", "ps_code", "station_code",
            "police_station_name", "ps_name", "station_name"
        ) if c in ccols]
        if ps_cols:
            cur.execute(
                f"SELECT crime_id, {', '.join(ps_cols)} FROM crimes_unified "
                f"WHERE source_system='V1' AND crime_id = ANY(%s)",
                (keys,),
            )
            station["crime_station_values"] = [
                dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()
            ]
            # fabricated code check: codes should remain null/empty when ambiguous
            code_col = next((c for c in ("police_station_code", "ps_code", "station_code") if c in ps_cols), None)
            if code_col:
                assigned = [
                    r for r in station["crime_station_values"]
                    if r.get(code_col) not in (None, "", "NULL")
                ]
                station["ambiguous_with_assigned_code_count"] = len(assigned)
    out["station_gaps"] = station

    # Integrity checks
    integrity = {}
    # duplicate PKs on crimes_unified
    cur.execute(
        """
        SELECT crime_id, source_system, COUNT(*)
        FROM crimes_unified
        GROUP BY 1,2 HAVING COUNT(*) > 1
        LIMIT 20
        """
    )
    integrity["dup_crime_keys"] = cur.fetchall()
    cur.execute(
        """
        SELECT table_name FROM information_schema.tables
        WHERE table_schema='public' AND table_name LIKE '%%change_log%%'
        """
    )
    cl_tables = [r[0] for r in cur.fetchall()]
    integrity["change_log_tables"] = cl_tables
    for clt in cl_tables[:3]:
        cur.execute(
            f"""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema='public' AND table_name=%s
            """,
            (clt,),
        )
        clcols = [r[0] for r in cur.fetchall()]
        # try common unique tuple
        if {"entity_type", "entity_id", "changed_at", "field_name"}.issubset(set(clcols)):
            cur.execute(
                f"""
                SELECT entity_type, entity_id, changed_at, field_name, COUNT(*)
                FROM {clt}
                GROUP BY 1,2,3,4 HAVING COUNT(*) > 1
                LIMIT 10
                """
            )
            integrity[f"dup_{clt}"] = cur.fetchall()
    # FK violations sample via information_schema is heavy; check orphan accused->crime if exists
    try:
        cur.execute(
            """
            SELECT COUNT(*) FROM accused_unified a
            LEFT JOIN crimes_unified c
              ON c.crime_id = a.crime_id AND c.source_system = a.source_system
            WHERE a.crime_id IS NOT NULL AND c.crime_id IS NULL
            """
        )
        integrity["orphan_accused_crime"] = cur.fetchone()[0]
    except Exception as e:
        tg.rollback()
        integrity["orphan_accused_crime"] = f"skip:{e}"

    out["integrity"] = integrity

    # Source safety
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
    out["source_safety"] = safety

    # V2 media permanent counts (unchanged expectation)
    v2 = get_v2_source_connection()
    vc = v2.cursor()
    vc.execute(
        """
        SELECT source_type, source_field,
               COUNT(*) FILTER (WHERE download_error LIKE 'PERMANENT:%%') permanent,
               COUNT(*) FILTER (WHERE download_error LIKE 'SOURCE: MEDIA present but file_id is null%%') null_fid,
               COUNT(*) FILTER (WHERE download_error ILIKE '%%missing on disk%%') disk_missing,
               COUNT(*) FILTER (WHERE is_downloaded IS TRUE) downloaded
        FROM public.file_media_bookkeeping
        GROUP BY 1,2 ORDER BY 1,2
        """
    )
    out["v2_media_after"] = [
        {
            "source_type": a,
            "source_field": b,
            "permanent": p,
            "null_file_id_annot": n,
            "disk_missing": d,
            "downloaded": dl,
        }
        for a, b, p, n, d, dl in vc.fetchall()
    ]
    v2.close()

    # run log for this run
    cur.execute(
        "SELECT * FROM consolidation_run_log WHERE run_id = ANY(%s)",
        ([run_id, run_id2],),
    )
    out["run_log_rows"] = [
        dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()
    ]

    tg.close()
    OUT.write_text(json.dumps(out, indent=2, default=str))
    print(
        json.dumps(
            {
                "report": str(OUT),
                "run_id": run_id,
                "run_id2": run_id2,
                "pre_missing": out["pre"]["missing_count"],
                "post_missing": out["post"]["missing_count"],
                "all_ten_present": out["post"]["all_ten_present"],
                "unified_delta": u_after - u_before,
                "second_idempotent": out["second_post"]["unified_unchanged"],
                "station_class": station.get("classification"),
                "station_count": station.get("count"),
                "source_safety": safety,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
