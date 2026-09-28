# Deploying CCTNSV1_DAILY_ETL_RUN

```
./deploy/deploy.sh
```

Syncs the code to `dopams-new`, installs Airflow + dependencies into a venv there, initializes Airflow (self-contained under `airflow_home/`, pointed at `dags/`), and starts the scheduler + webserver in the background. Safe to re-run — it kills and restarts both processes each time.

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

## Changing the schedule or DAG logic

Edit `dags/simple_apis_etl.py` / `dags/accused_yearly_etl.py` / `dags/pipeline_run.py` locally, then re-run `./deploy/deploy.sh` — Airflow picks up DAG file changes automatically (scans `dags/` every ~5 min by default), but re-deploying also refreshes `requirements.txt` and restarts both processes cleanly.
