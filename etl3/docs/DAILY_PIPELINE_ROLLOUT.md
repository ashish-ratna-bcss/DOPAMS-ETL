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
python etl3/run_daily_pipeline.py --check          # source readiness only
python etl3/run_daily_pipeline.py --gate-check     # schedule-activation readiness only (read-only)
python etl3/run_daily_pipeline.py --etl3-only
python etl3/run_daily_pipeline.py --with-sources   # V1 → V2 → gate → ETL-3
```

## Proposed schedule (not activated)

Reuse V1/V2 slot times 05:30 / 11:30 / 17:30 / 23:30 IST (00:00/06:00/12:00/18:00 UTC). After both sources succeed for a slot, run `run_daily_pipeline.py --etl3-only` (or the existing `run_daily_when_sources_finished.py` gate + phase5 + enrichment).

Unit files for this cadence exist at `etl3/deploy/etl3-daily-pipeline.{service,timer}` -- **not installed, not enabled**. `WorkingDirectory`/`ExecStart` paths in the service file assume this checkout's documented location and a `venv` virtualenv at its root; verify both against the actual host before ever running `systemctl enable`.

Do **not** start a second Airflow/PM2 stack while `/home/eagle/dopams-cctns` still owns production schedules.

## Activation gate — required before enabling the timer

`python etl3/run_daily_pipeline.py --gate-check` aggregates the automated half of this checklist (read-only; starts/stops nothing) and exits non-zero while any blocker is open:

- [ ] **AI backfill complete** — `--gate-check`'s `ai_backfill.complete` is `true` (zero pending drug *and* accused items, the same completion criterion `run_ai_backfill.py --status` reports). As of the last check this gate ran, it was **not** complete (large pending backlog on both). Do not enable the timer while this is false.
- [ ] **No conflicting process** — `--gate-check`'s `conflicting_process.clear` is `true` (no other live connection sharing the `etl3_unified` application_name).
- [ ] **Correct code deployed** — the commit actually checked out at `/home/eagle/dopams-cctns-ai` contains the name-normalization fix and this gate; verify on the host itself (`git log -1`), not inferred from this repository.
- [ ] **Tests pass** — `etl3/tests/test_enrichment.py`, `test_multi_accused.py`, `test_daily_pipeline_gate.py` all green on the deployed commit (the known, pre-existing, unrelated `ai retry` failure in `test_enrichment.py` is not a blocker).
- [ ] **Migration 019 decision made** — applied deliberately, or deliberately deferred; not left ambiguous.
- [ ] **Monitoring/recovery ready** — someone is watching `enrichment_run_log.status` and the systemd unit's own exit status after the first few scheduled runs.

Only once every box is checked does a human run `systemctl enable --now etl3-daily-pipeline.timer` on the deployment host. This document and the gate check do not do that for you.

## Rollback

1. Stop any new checkout scheduler/job (none enabled by default).
2. Leave `/home/eagle/dopams-cctns` Airflow as the sole production schedule.
3. ETL-3 target `dopams_cctns_v2` can remain; baseline `dopams_cctns` is untouched.
4. Restore from `/home/eagle/backups/dopams_cctns_v2/*.dump` only if required.
