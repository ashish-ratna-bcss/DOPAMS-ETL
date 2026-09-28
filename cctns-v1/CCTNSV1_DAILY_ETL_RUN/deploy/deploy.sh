#!/usr/bin/env bash
# ==============================================================================
# Deploys CCTNSV1_DAILY_ETL_RUN to dopams-new and sets up Airflow to run the
# two DAGs (cctnsv1_simple_apis_etl, cctnsv1_accused_yearly_etl) nightly.
#
# Usage:
#   ./deploy/deploy.sh
#
# What it does, in order:
#   1. rsync this whole folder to the server (excluding venv/, airflow_home/,
#      *.log, __pycache__, .git)
#   2. create/refresh a Python venv on the server, install requirements.txt
#      (includes apache-airflow)
#   3. initialize Airflow's metadata DB (self-contained under
#      REMOTE_DIR/airflow_home, not the user's default ~/airflow), pointed
#      at REMOTE_DIR/dags, with example DAGs disabled
#   4. (re)start the Airflow scheduler + webserver as background processes
#
# Reboot survival: step 4 starts Airflow with nohup, which does NOT survive
# a server reboot (no sudo/systemd access from this script -- see
# deploy/README.md). Run this script again after a reboot, or install the
# provided systemd units yourself (deploy/airflow-scheduler.service,
# deploy/airflow-webserver.service) for that to be automatic.
# ==============================================================================
set -euo pipefail

REMOTE_HOST="dopams-new"
REMOTE_DIR="/home/tganb/cctnsv1_daily_etl"
REMOTE_AIRFLOW_HOME="${REMOTE_DIR}/airflow_home"
WEBSERVER_PORT="8793"
LOCAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> [1/4] Syncing code to ${REMOTE_HOST}:${REMOTE_DIR}"
ssh "$REMOTE_HOST" "mkdir -p $REMOTE_DIR"
rsync -az --delete \
  --exclude 'venv/' \
  --exclude 'airflow_home/' \
  --exclude '*.log' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude '.git/' \
  --exclude 'deploy/' \
  "$LOCAL_DIR/" "${REMOTE_HOST}:${REMOTE_DIR}/"

echo "==> [2/4] Setting up Python venv + dependencies (incl. Airflow) on ${REMOTE_HOST}"
ssh "$REMOTE_HOST" "
  cd '$REMOTE_DIR' &&
  python3 -m venv venv &&
  ./venv/bin/pip install --quiet --upgrade pip &&
  ./venv/bin/pip install --quiet -r requirements.txt
"

echo "==> [3/4] Initializing Airflow (self-contained under ${REMOTE_AIRFLOW_HOME})"
ssh "$REMOTE_HOST" "
  cd '$REMOTE_DIR' &&
  export AIRFLOW_HOME='$REMOTE_AIRFLOW_HOME' &&
  export AIRFLOW__CORE__DAGS_FOLDER='${REMOTE_DIR}/dags' &&
  export AIRFLOW__CORE__LOAD_EXAMPLES=False &&
  ./venv/bin/airflow db migrate &&
  (./venv/bin/airflow users list 2>/dev/null | grep -q admin || \
   ./venv/bin/airflow users create --username admin --password admin \
     --firstname CCTNS --lastname Admin --role Admin --email admin@example.com)
"

echo "==> [4/4] (Re)starting Airflow scheduler + webserver on ${REMOTE_HOST}"
ssh "$REMOTE_HOST" "
  cd '$REMOTE_DIR' &&
  export AIRFLOW_HOME='$REMOTE_AIRFLOW_HOME' &&
  export AIRFLOW__CORE__DAGS_FOLDER='${REMOTE_DIR}/dags' &&
  export AIRFLOW__CORE__LOAD_EXAMPLES=False &&
  pkill -f 'airflow scheduler' 2>/dev/null || true &&
  pkill -f 'airflow webserver' 2>/dev/null || true &&
  sleep 2 &&
  nohup ./venv/bin/airflow scheduler > '${REMOTE_DIR}/airflow_scheduler.log' 2>&1 &
  disown &&
  nohup ./venv/bin/airflow webserver --port ${WEBSERVER_PORT} > '${REMOTE_DIR}/airflow_webserver.log' 2>&1 &
  disown &&
  sleep 3 &&
  pgrep -f 'airflow scheduler' > /dev/null && echo 'scheduler: running' || echo 'scheduler: NOT running -- check airflow_scheduler.log' &&
  pgrep -f 'airflow webserver' > /dev/null && echo 'webserver: running' || echo 'webserver: still starting or check airflow_webserver.log'
"

echo "==> Deploy complete."
echo "    Airflow UI:   http://<dopams-new-ip>:${WEBSERVER_PORT}  (login: admin / admin -- change this)"
echo "    DAGs:         cctnsv1_simple_apis_etl (00:30 daily), cctnsv1_accused_yearly_etl (00:30 daily)"
echo "    Scheduler log: ${REMOTE_HOST}:${REMOTE_DIR}/airflow_scheduler.log"
echo "    Webserver log: ${REMOTE_HOST}:${REMOTE_DIR}/airflow_webserver.log"
echo "    NOTE: started via nohup, will NOT survive a server reboot -- see deploy/README.md"
