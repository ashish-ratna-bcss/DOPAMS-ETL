# ETL-3 daily pipeline rollout (cctns-ai)

## Status

**Schedule: NOT ENABLED.** Manual/controlled entrypoints only.

## Checkouts

| Path | Role |
|------|------|
| `/home/eagle/dopams-cctns-ai` | This branch (`cctns-ai`) — ETL-3 + wrappers |
| `/home/eagle/dopams-cctns` | Old pipeline — leave running; do not modify |

## Target databases

| Component | Database | Access |
|-----------|----------|--------|
| V1 ETL | `cctns_v1` | write (V1 only) |
| V2 ETL | `cctns-v2` | write (V2 only) |
| ETL-3 | `dopams_cctns_v2` | write |
| ETL-3 sources | `cctns_v1`, `cctns-v2` | read-only session |
| KB | `dopams_cctns_v2.kb` | retain; do not truncate |

## Entrypoints

```bash
# AI historical backfill (resume-safe)
python etl3/run_ai_backfill.py --status
python etl3/run_ai_backfill.py

# V1 / V2 (explicit; refuse without flags)
python etl3/run_v1_etl.py --check
python etl3/run_v1_etl.py --run-cycle
python etl3/run_v2_etl.py --check
python etl3/run_v2_etl.py --run

# Daily orchestration (manual)
python etl3/run_daily_pipeline.py --check
python etl3/run_daily_pipeline.py --etl3-only
python etl3/run_daily_pipeline.py --with-sources   # V1 → V2 → gate → ETL-3
```

## Proposed schedule (not activated)

Reuse V1/V2 slot times 05:30 / 11:30 / 17:30 / 23:30 IST. After both sources succeed for a slot, run `run_daily_pipeline.py --etl3-only` (or the existing `run_daily_when_sources_finished.py` gate + phase5 + enrichment).

Do **not** start a second Airflow/PM2 stack while `/home/eagle/dopams-cctns` still owns production schedules.

## Rollback

1. Stop any new checkout scheduler/job (none enabled by default).
2. Leave `/home/eagle/dopams-cctns` Airflow as the sole production schedule.
3. ETL-3 target `dopams_cctns_v2` can remain; baseline `dopams_cctns` is untouched.
4. Restore from `/home/eagle/backups/dopams_cctns_v2/*.dump` only if required.
