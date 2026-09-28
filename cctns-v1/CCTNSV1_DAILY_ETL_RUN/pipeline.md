# CCTNS V1 Daily ETL — how it works, how to run it, how to check it

## Folder layout

```
apis/      code that calls the 4 CCTNS V1 API endpoints
db/        Postgres connection, upsert logic, and SQL (schema fix + dedupe)
config/    loads .env, exposes settings to every other module
dags/      2 Airflow DAGs + the shared pipeline logic they both call
.env       real credentials (never commit this)
```

Nothing is written to local disk at all. Data flows straight from the API into Postgres in memory. Logging goes to stdout only: under Airflow that's captured automatically as the task's own log (visible in the Airflow UI), and the durable structured record of every run lives in `cctns_v1_etl_run_log` in Postgres itself — a local log file would just be a third copy of the same information.

### Two DAGs, not one

- **`dags/simple_apis_etl.py`** (`cctnsv1_simple_apis_etl`) — FIR, Court, Accused Details. All 3 support a single unfiltered GET that returns everything in a few seconds; no date chunking needed.
- **`dags/accused_yearly_etl.py`** (`cctnsv1_accused_yearly_etl`) — Accused date-range endpoint only. This is the one that needs month-by-month chunking + adaptive halving against the flaky Oracle backend (`ORA-06502` buffer errors). Kept separate because it's slower and more failure-prone than the other 3 — easier to monitor and retry on its own.

Both call into the shared logic in `dags/pipeline_run.py` (`run_simple_apis()` / `run_accused_yearly()`), so there's one place the actual extract/stage/load behavior lives.

## Current status — read this before running anything

| Entity | API pull | DB load |
|---|---|---|
| `fir` | ✅ ready | ✅ ready — real primary key, 0 duplicates, safe |
| `court` | ✅ ready | ⛔ **blocked** — natural_key not applied yet |
| `accused_details` | ✅ ready | ⛔ **blocked** — natural_key not applied yet |
| `accused` | ✅ ready | ⛔ **blocked** — natural_key not applied yet |

The 3 blocked tables are waiting on a decision, not a bug: real duplicate analysis on the live `dopams-new` database found that a naive composite key (e.g. `fir_reg_num + person_code + accused_name`) wrongly collapses groups of **genuinely different people** into one row — e.g. 18 distinct "unknown" accused sharing one FIR. See the full explanation at the top of `db/sql/001_schema_fix.sql`. Until that's reviewed and the key is finalized, the pipeline **still fetches** data for these 3 entities every run (so the extract/API side is fully exercised and logged), but deliberately does **not** write it to Postgres — this avoids either silently duplicating data or silently merging distinct people. Nothing is cached anywhere in between; an entity that isn't loaded this run is fetched fresh again next run.

## One-time setup, in order

1. **Fill in `.env`** — copy the 4 API URLs + `AUTH_TOKEN`/`API_KEY` from `cctnsv1/.env`. Postgres credentials are already filled in (from `dopams-new`).
2. **Install dependencies**: `pip install -r requirements.txt`
3. **Run the safe dedupe** (removes only confirmed byte-identical duplicate rows — see the file header for the exact counts this affects):
   ```
   psql -h <dopams-new host> -U dopams_bcss -d cctns_v1 -f db/sql/000_dedupe_exact_only.sql
   ```
4. **Review the ambiguous duplicate groups** (the ones NOT touched by step 3) using the query at the top of `db/sql/001_schema_fix.sql`, and decide the real key per table.
5. **Uncomment and adjust** the `natural_key` blocks in `db/sql/001_schema_fix.sql` to match that decision, then run it:
   ```
   psql -h <dopams-new host> -U dopams_bcss -d cctns_v1 -f db/sql/001_schema_fix.sql
   ```
6. **Update `dags/pipeline_run.py`**: flip `"upsert_ready": False` to `True` for each entity once its constraint is live.

## Running it

**Standalone (no Airflow needed, good for testing):**
```
python dags/pipeline_run.py simple     # FIR, Court, Accused Details
python dags/pipeline_run.py accused    # Accused date-range only
```
Each does exactly what its DAG does: fetch its entities, load whichever are marked `upsert_ready` straight into Postgres, logging to stdout as it goes.

**Under Airflow** (this is how it actually runs — see Deployment below):
1. `deploy/deploy.sh` installs Airflow on `dopams-new` and points `AIRFLOW__CORE__DAGS_FOLDER` at this `dags/` directory.
2. Both DAGs are scheduled for `30 0 * * *` (00:30 daily), `catchup=False`, 2 retries with a 5-minute backoff.
3. Airflow UI (`http://<dopams-new-ip>:8793`) → DAGs → `cctnsv1_simple_apis_etl` and `cctnsv1_accused_yearly_etl` to trigger manually or check run history — they run and can be monitored independently.

## Deployment (Airflow, running on dopams-new)

`deploy/deploy.sh`:
1. `rsync`s this whole folder to `dopams-new` (`/home/tganb/cctnsv1_daily_etl`), excluding `venv/`, `airflow_home/`, `*.log`, `__pycache__/`.
2. Creates/refreshes a Python venv there and installs `requirements.txt` (includes `apache-airflow`).
3. Initializes Airflow — self-contained under `airflow_home/` (not the user's default `~/airflow`), pointed at `dags/`, example DAGs disabled.
4. (Re)starts the Airflow scheduler + webserver as background processes.

Run it from your Mac:
```
./deploy/deploy.sh
```

**One caveat**: step 4 starts Airflow with `nohup`, not as a system service — installing a real `systemd` service needs `sudo`, which this script deliberately doesn't attempt on its own (sandbox restrictions on this side). That means Airflow won't automatically come back up if `dopams-new` reboots. `deploy/README.md` has the one-time manual `sudo` steps to install `deploy/airflow-scheduler.service` / `deploy/airflow-webserver.service` for full reboot survival — after that, redeploys are still just `./deploy/deploy.sh`.

## How to check whether a run actually worked

1. **Airflow task logs** (if run via Airflow): DAG → task → Logs, shows exactly what stdout showed for that run — `fetched=`, `inserted=`, `updated=`, `unchanged=`, and whether any date windows failed to fetch (relevant to `accused` only — see `apis/accused.py`).
2. **`cctns_v1_etl_run_log` in Postgres** (once `db/sql/001_schema_fix.sql` is applied) — the durable, queryable history of every run: rows fetched/inserted/updated/unchanged, per entity, per night. This is the source of truth, not a log file.
3. **Audit trail**: once triggers are live (same migration), any field that actually changes on an update gets a row in `cctns_v1_audit_log` — `table_name`, `record_key`, `field_name`, `old_value`, `new_value`, `changed_at`. Useful for answering "when did this FIR's status change, and from what?"

## Known open item

The business key for `cctns_court`, `cctns_accused_details`, and `cctns_accused` is the one thing standing between this pipeline and being fully live. It needs a human decision (not something to automate away), because the source data has real ambiguity between "duplicate record" and "different person who happens to share a generic name/code." See `db/sql/001_schema_fix.sql` for the specific example and the review query.
