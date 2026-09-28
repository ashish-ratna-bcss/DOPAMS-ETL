#!/usr/bin/env bash
# Source .env, configure Airflow metadata on Postgres + LocalExecutor, (re)start PM2.
set -euo pipefail

ETL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ETL_DIR}/.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing ${ENV_FILE} — copy from cctnsv1/.env and set PG_* / API URLs." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

: "${PG_USER:?PG_USER required in .env}"
: "${PG_PASSWORD:?PG_PASSWORD required in .env}"
PG_HOST="${PG_HOST:-localhost}"
PG_PORT="${PG_PORT:-5432}"
AIRFLOW_METADATA_DATABASE="${AIRFLOW_METADATA_DATABASE:-cctns_v1_airflow}"

ENC_PASS="$("${ETL_DIR}/venv/bin/python3" -c "import urllib.parse, os; print(urllib.parse.quote_plus(os.environ['PG_PASSWORD']))")"
export AIRFLOW__DATABASE__SQL_ALCHEMY_CONN="postgresql+psycopg2://${PG_USER}:${ENC_PASS}@${PG_HOST}:${PG_PORT}/${AIRFLOW_METADATA_DATABASE}"
export AIRFLOW_HOME="${ETL_DIR}/airflow_home"
export AIRFLOW__CORE__DAGS_FOLDER="${ETL_DIR}/dags"
export AIRFLOW__CORE__LOAD_EXAMPLES=False
export AIRFLOW__CORE__EXECUTOR=LocalExecutor

if ! command -v pm2 >/dev/null 2>&1; then
  echo "pm2 not found in PATH" >&2
  exit 1
fi

cd "$ETL_DIR"
pm2 startOrReload "${ETL_DIR}/deploy/ecosystem.config.cjs" --update-env
pm2 save
echo "PM2 reloaded (LocalExecutor + Postgres metadata: ${AIRFLOW_METADATA_DATABASE} on ${PG_HOST})"
