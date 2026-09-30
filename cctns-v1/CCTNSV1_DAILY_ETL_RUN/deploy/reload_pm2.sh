#!/usr/bin/env bash
set -euo pipefail

ETL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ ! -x "${ETL_DIR}/deploy/airflow_with_env.sh" ]]; then
  chmod +x "${ETL_DIR}/deploy/airflow_with_env.sh"
fi

if ! command -v pm2 >/dev/null 2>&1; then
  echo "pm2 not found in PATH" >&2
  exit 1
fi

cd "$ETL_DIR"
pm2 delete cctnsv1-airflow-scheduler cctnsv1-airflow-webserver 2>/dev/null || true
pm2 start "${ETL_DIR}/deploy/ecosystem.config.cjs"
pm2 save
echo "PM2 started (Airflow + ETL metadata in PG_DATABASE, LocalExecutor, UI :9001)"
