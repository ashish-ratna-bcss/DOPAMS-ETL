#!/usr/bin/env bash
# ==============================================================================
# CCTNS V1 deploy helper — PM2 / port 9001 ONLY.
#
# Usage (on dopams-new, after git pull of DOPAMS-ETL):
#   cd ~/dopams/DOPAMS-ETL/cctns-v1/CCTNSV1_DAILY_ETL_RUN
#   ./deploy/deploy.sh
#
# This script no longer rsyncs or starts a second Airflow on :8793.
# The only supported UI is AIRFLOW_WEBSERVER_PORT=9001 via reload_pm2.sh.
# ==============================================================================
set -euo pipefail

ETL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ETL_DIR"

PORT="${AIRFLOW_WEBSERVER_PORT:-}"
if [[ -z "$PORT" && -f .env ]]; then
  # shellcheck disable=SC1091
  PORT="$(set -a; source .env; set +a; echo "${AIRFLOW_WEBSERVER_PORT:-9001}")"
fi
PORT="${PORT:-9001}"

if [[ "$PORT" != "9001" ]]; then
  echo "ERROR: Airflow UI must run on port 9001 only (got AIRFLOW_WEBSERVER_PORT=$PORT)." >&2
  echo "Set AIRFLOW_WEBSERVER_PORT=9001 in .env and re-run." >&2
  exit 1
fi

# Stop any legacy webserver that used to listen on 8793 / other ports.
pkill -f "airflow webserver --port 8793" 2>/dev/null || true
pkill -f "/home/tganb/cctnsv1_daily_etl/.*airflow webserver" 2>/dev/null || true

echo "==> Reloading Airflow via PM2 (UI :9001 only)"
exec ./deploy/reload_pm2.sh
