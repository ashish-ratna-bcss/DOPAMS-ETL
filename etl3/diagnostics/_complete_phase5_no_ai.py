"""Complete Phase-5 after AI enrichment failure — AI explicitly disabled for this process.

Crimes already landed in unified during the failed run's pre-enrichment commits.
This run finishes enrichment (deterministic only), advances cursors, reconciles,
and validates. Does not start AI backfill or daily scheduling.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Disable AI before any etl3 settings/ai reads for this process only.
os.environ["ETL3_AI_ENABLED"] = "0"

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


def snap(cur):
    out = {}
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
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        out[table] = cur.fetchone()[0]
    return out


def crime_state(cur):
    cur.execute("SELECT COUNT(*) FROM crimes_unified WHERE source_system='V2'")
    u = cur.fetchone()[0]
    cur.execute(
        "SELECT crime_id FROM crimes_unified WHERE source_system='V2' AND crime_id = ANY(%s) ORDER BY 1",
        (IDS,),
    )
    present = [r[0] for r in cur.fetchall()]
    cur.execute(
        """
        SELECT source_record_id, COUNT(*)
        FROM crimes_source
        WHERE source_system='V2' AND source_record_id = ANY(%s)
        GROUP BY 1 ORDER BY 1
        """,
        (IDS,),
    )
    obs = dict(cur.fetchall())
    return u, present, obs


def main():
    out = {
        "ai_forced_off": os.environ.get("ETL3_AI_ENABLED"),
        "prior_failed_run": "aa14bd75-eaea-4fc2-a704-aa02f9276afa",
        "note": "Prior run consolidated the 10 crimes then failed in AI enrichment; this run finishes without AI.",
    }

    v2 = get_v2_source_connection()
    vc = v2.cursor()
    vc.execute("SELECT COUNT(*) FROM crimes")
    v2_count = vc.fetchone()[0]
    v2.close()
    out["v2_source_count"] = v2_count

    tg = get_unified_connection()
    cur = tg.cursor()
    u0, present0, obs0 = crime_state(cur)
    out["pre"] = {
        "unified_v2": u0,
        "present": present0,
        "obs": obs0,
        "snapshots": snap(cur),
    }

    run_id = run_incremental(tg)
    out["incremental_run_id"] = run_id

    u1, present1, obs1 = crime_state(cur)
    snaps1 = snap(cur)
    out["post"] = {
        "unified_v2": u1,
        "present": present1,
        "obs": obs1,
        "all_ten_present": set(IDS).issubset(set(present1)),
        "missing_vs_source": v2_count - u1,
        "snapshots": snaps1,
        "snapshot_deltas": {
            k: snaps1[k] - out["pre"]["snapshots"][k] for k in snaps1
        },
    }

    # Cursor for V2 crimes should advance past prior watermark
    cur.execute(
        """
        SELECT source_system, source_module, last_processed_source_run_id, status
        FROM consolidation_cursor WHERE source_system='V2' AND source_module='crimes'
        """
    )
    out["crime_cursor_after"] = dict(zip([d[0] for d in cur.description], cur.fetchone()))
    cur.execute("SELECT status, COUNT(1) FROM consolidation_cursor GROUP BY 1")
    out["cursor_status_counts"] = dict(cur.fetchall())

    # Second incremental — idempotent check
    run_id2 = run_incremental(tg)
    out["second_incremental_run_id"] = run_id2
    u2, present2, obs2 = crime_state(cur)
    out["second_post"] = {
        "unified_v2": u2,
        "present": present2,
        "obs": obs2,
        "unified_unchanged": u2 == u1,
        "obs_unchanged": obs2 == obs1,
    }

    # Station gaps
    station = {"classification": classify_gap("ambiguous_v1_station_name")}
    cur.execute(
        """
        SELECT gap_key, gap_type, status
        FROM source_gap_ledger
        WHERE gap_type='ambiguous_v1_station_name'
        ORDER BY gap_key
        """
    )
    rows = [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]
    station["count"] = len(rows)
    station["all_open"] = all((r.get("status") or "").upper() == "OPEN" for r in rows)
    station["keys"] = [r["gap_key"] for r in rows]
    cur.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema='public' AND table_name='crimes_unified'
        """
    )
    ccols = {r[0] for r in cur.fetchall()}
    code_col = next((c for c in ("ps_code", "police_station_code", "station_code") if c in ccols), None)
    name_col = next((c for c in ("station_name", "police_station_name", "ps_name") if c in ccols), None)
    if code_col and rows:
        sel = ", ".join(["crime_id", code_col] + ([name_col] if name_col else []))
        cur.execute(
            f"SELECT {sel} FROM crimes_unified WHERE source_system='V1' AND crime_id = ANY(%s)",
            (station["keys"],),
        )
        vals = [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]
        station["crime_station_values_sample"] = vals[:10]
        station["ambiguous_with_assigned_code_count"] = sum(
            1 for r in vals if r.get(code_col) not in (None, "")
        )
    out["station_gaps"] = station

    # Integrity
    integrity = {}
    cur.execute(
        """
        SELECT crime_id, source_system, COUNT(*)
        FROM crimes_unified GROUP BY 1,2 HAVING COUNT(*) > 1 LIMIT 20
        """
    )
    integrity["dup_crime_keys"] = cur.fetchall()
    cur.execute(
        """
        SELECT table_name FROM information_schema.tables
        WHERE table_schema='public' AND table_name LIKE '%%change_log%%'
        """
    )
    for clt in [r[0] for r in cur.fetchall()][:5]:
        cur.execute(
            f"""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema='public' AND table_name=%s
            """,
            (clt,),
        )
        clcols = {r[0] for r in cur.fetchall()}
        if {"entity_id", "field_name", "changed_at"}.issubset(clcols):
            cur.execute(
                f"""
                SELECT entity_id, field_name, changed_at, COUNT(*)
                FROM {clt}
                GROUP BY 1,2,3 HAVING COUNT(*) > 1
                LIMIT 5
                """
            )
            integrity[f"dup_{clt}"] = cur.fetchall()
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

    # V2 media after (unchanged expected)
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

    cur.execute(
        "SELECT run_id, status, rows_observed, rows_changed, error_message IS NOT NULL AS has_err "
        "FROM consolidation_run_log WHERE run_id = ANY(%s)",
        ([run_id, run_id2, "aa14bd75-eaea-4fc2-a704-aa02f9276afa"],),
    )
    out["run_log"] = [
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
                "all_ten_present": out["post"]["all_ten_present"],
                "missing_vs_source": out["post"]["missing_vs_source"],
                "cursor": out["crime_cursor_after"],
                "second_idempotent": out["second_post"]["unified_unchanged"],
                "station_count": station["count"],
                "station_class": station["classification"],
                "kb_deltas": {
                    k: out["post"]["snapshot_deltas"][k]
                    for k in out["post"]["snapshot_deltas"]
                    if k.startswith("kb.")
                },
                "source_safety": safety,
            },
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
