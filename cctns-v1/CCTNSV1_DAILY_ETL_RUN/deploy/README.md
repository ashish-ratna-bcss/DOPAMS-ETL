# Deploying CCTNSV1_DAILY_ETL_RUN

See `pipeline.md` for how the pipeline itself works. This file is setup/ops only.

## One-time setup, before the first deploy

1. Fill in `.env` — the 4 API URLs (see `cctnsv1/.env`) and Postgres credentials for `dopams-new`.
   On first pipeline run (or manually: `python -m db.init_schema`), the ETL creates the
   `cctns_v1` database if your role has `CREATEDB`, then applies `db/sql/init_schema.sql`
   and `db/sql/init_etl_support.sql`. Greenfield only — an existing dopams-new DB is
   left unchanged except for missing `updated_at` / audit / run-log objects.
2. Run the safe dedupe on the live DB (removes only confirmed byte-identical duplicate rows):
   ```bash
   psql -h <dopams-new host> -U dopams_bcss -d cctns_v1 -f db/sql/000_dedupe_exact_only.sql
   ```
3. Review the ambiguous duplicate groups (query at the top of `db/sql/001_schema_fix.sql`) and decide the real business key for `cctns_court` / `cctns_accused_details` / `cctns_accused`.
4. Uncomment + adjust the `natural_key` blocks in `db/sql/001_schema_fix.sql` to match that decision, then run it:
   ```bash
   psql -h <dopams-new host> -U dopams_bcss -d cctns_v1 -f db/sql/001_schema_fix.sql
   ```
5. In `dags/pipeline_run.py`, flip `"upsert_ready": False` → `True` for each entity once its constraint is live.

## Airflow on dopams-new (PM2 — recommended)

Production-style settings (Postgres + LocalExecutor, no separate Airflow DB):

- **One database** `PG_DATABASE` (default `cctns_v1`), **two schemas:**
  - `cctns` (or `PG_ETL_SCHEMA`) — `cctns_*`, audit/run log
  - `airflow` (or `PG_AIRFLOW_SCHEMA`) — `dag`, `dag_run`, `ab_*`, …
- **Auto-create:** PM2 `airflow_with_env.sh` creates the DB if missing, runs `airflow db migrate`, and creates `admin` on scheduler start. DAG task `bootstrap_database` runs ETL DDL + migrate too.

**After every code pull:**
```bash
cd ~/dopams/DOPAMS-ETL/cctns-v1/CCTNSV1_DAILY_ETL_RUN
./deploy/reload_pm2.sh
```

Optional manual migrate only: `./deploy/setup_airflow_metadata_db.sh`

`config/settings.py` loads `.env` from the project root so Airflow tasks always see `PG_*` and API URLs.

Airflow UI: `http://<dopams-new-ip>:9001` (login `admin` / `admin` — change password).

## Deploy (rsync + venv, optional)

```
./deploy/deploy.sh
```

Syncs the code to `dopams-new`, installs Airflow + dependencies into a venv there, initializes Airflow (self-contained under `airflow_home/`, pointed at `dags/`), and starts the scheduler + webserver in the background. Safe to re-run — it kills and restarts both processes each time. On dopams-new prefer **`./deploy/reload_pm2.sh`** after `git pull` instead.

## Reboot survival (one-time, needs your `sudo`)

`deploy.sh` starts Airflow with `nohup`, not a system service, because installing a `systemd` unit needs `sudo`, which this deploy script deliberately doesn't attempt on its own. If `dopams-new` reboots, Airflow won't come back up on its own unless you do this once:

```bash
scp deploy/airflow-scheduler.service deploy/airflow-webserver.service dopams-new:/tmp/
ssh dopams-new
sudo mv /tmp/airflow-scheduler.service /tmp/airflow-webserver.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now airflow-scheduler airflow-webserver
```

After that, both survive reboots automatically, and you can go back to plain `./deploy/deploy.sh` for code updates — just skip step 4's manual process restart by using `sudo systemctl restart airflow-scheduler airflow-webserver` instead if you prefer the systemd units to be authoritative.

## Checking it's actually running

```bash
ssh dopams-new "pgrep -fa 'airflow scheduler'; pgrep -fa 'airflow webserver'"
```

Airflow UI: `http://<dopams-new-ip>:8793` (login `admin` / `admin` — **change this password**, it's a placeholder).

## Checking whether a run actually worked

1. **Airflow task logs**: DAG → task → Logs — shows `fetched=`, `inserted=`, `updated=`, `unchanged=`, and any date windows that failed to fetch (relevant to `accused` only).
2. **`cctns_v1_etl_run_log`** in Postgres (once `db/sql/001_schema_fix.sql` is applied) — the durable, queryable history of every run.
3. **`cctns_v1_audit_log`** in Postgres (same migration) — every field that actually changed on an update, old value → new value → when.

## Changing the schedule or DAG logic

Edit `dags/simple_apis_etl.py` / `dags/accused_yearly_etl.py` / `dags/pipeline_run.py` locally, then re-run `./deploy/deploy.sh` — Airflow picks up DAG file changes automatically (scans `dags/` every ~5 min by default), but re-deploying also refreshes `requirements.txt` and restarts both processes cleanly.
