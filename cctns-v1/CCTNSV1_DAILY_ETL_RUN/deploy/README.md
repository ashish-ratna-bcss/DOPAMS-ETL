# Deploying CCTNSV1_DAILY_ETL_RUN

**ETL design:** [`../pipeline.md`](../pipeline.md) · **DAG tasks & graphs:** [`../dags/README.md`](../dags/README.md)  
This file is setup/ops only.

## One-time setup, before the first deploy

1. Copy `.env.example` → `.env` and fill in API URLs + Postgres credentials. Keep schema names explicit:
   ```env
   PG_ETL_SCHEMA=cctns
   PG_AIRFLOW_SCHEMA=airflow
   ```
   (Same values are the code defaults if omitted; set them in `.env` so PM2/Airflow and pgAdmin stay aligned.)
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
- **Auto-create:** PM2 `airflow_with_env.sh` creates the DB if missing, runs `airflow db migrate`, and creates/resets the Airflow Admin user from **`AIRFLOW_ADMIN_PASSWORD`** in `.env` (required, min 12 chars, not `admin`). DAG task `bootstrap_database` runs ETL DDL + migrate too.
- **UI bind:** `AIRFLOW_WEBSERVER_HOST` / `AIRFLOW_WEBSERVER_PORT` (**9001 only**, HTTP). Prefer a private IP for `AIRFLOW_WEBSERVER_HOST`.
- **Postgres TLS:** set `PG_SSLMODE=prefer` (default) or `require` once the server has SSL.
- **Failure alerts:** every failed Airflow task appends JSON to `logs/etl_failures.log`. Optionally set `CCTNS_ALERT_WEBHOOK_URL` in `.env` for Slack/Teams/webhook POST.

**After every code pull:**
```bash
cd ~/dopams/DOPAMS-ETL/cctns-v1/CCTNSV1_DAILY_ETL_RUN
./deploy/reload_pm2.sh
```

Optional manual migrate only: `./deploy/setup_airflow_metadata_db.sh`

`config/settings.py` loads `.env` from the project root so Airflow tasks always see `PG_*` and API URLs.

Airflow UI: `http://<dopams-new-ip>:9001` — username/password from `.env` (`AIRFLOW_ADMIN_*`). Never use the old default `admin`/`admin`.

**UI shows "Ooops!" when triggering a DAG:** With metadata in schema `airflow`, the Postgres URL must set `search_path` (handled in `deploy/airflow_with_env.sh`). After `git pull`, run `./deploy/reload_pm2.sh`. If trigger still fails, check webserver logs (`pm2 logs cctnsv1-airflow-webserver --lines 50`) for `log_template` / `TypeError`, and verify `SELECT COUNT(*) FROM airflow.log_template;` is greater than 0.

## Deploy / reload (port **9001** only)

On dopams-new after `git pull`:

```bash
cd ~/dopams/DOPAMS-ETL/cctns-v1/CCTNSV1_DAILY_ETL_RUN
./deploy/deploy.sh          # alias for reload_pm2.sh; refuses any port except 9001
# or: ./deploy/reload_pm2.sh
```

Do **not** run a second Airflow on `:8793`. If something is still listening there, stop it:

```bash
pkill -f 'airflow webserver --port 8793' || true
```

## Reboot survival

Prefer **PM2** (`pm2 save` + `pm2 startup`). Optional systemd units under `deploy/` also call `airflow_with_env.sh` and listen on **9001** only — install only if you are not using PM2 (do not run both).

## Checking it's actually running

```bash
ssh dopams-new "ss -ltnp | grep 9001; pgrep -fa 'airflow webserver'"
```

Airflow UI: `http://192.168.103.106:9001` — credentials from `.env` (`AIRFLOW_ADMIN_*`). Port must be **9001**.
## Checking whether a run actually worked

1. **Airflow task logs**: DAG → task → Logs — shows `fetched=`, `inserted=`, `updated=`, `unchanged=`, and any date windows that failed to fetch (relevant to `accused` only).
2. **`cctns_v1_etl_run_log`** in Postgres (once `db/sql/001_schema_fix.sql` is applied) — the durable, queryable history of every run.
3. **`cctns_v1_audit_log`** in Postgres (same migration) — every field that actually changed on an update, old value → new value → when.

## Changing the schedule or DAG logic

Edit `dags/daily_sync_fir_court_accused_details.py` / `dags/daily_sync_accused_dossier.py` / `dags/pipeline_run.py` locally, then re-run `./deploy/deploy.sh` — Airflow picks up DAG file changes automatically (scans `dags/` every ~5 min by default), but re-deploying also refreshes `requirements.txt` and restarts both processes cleanly.
