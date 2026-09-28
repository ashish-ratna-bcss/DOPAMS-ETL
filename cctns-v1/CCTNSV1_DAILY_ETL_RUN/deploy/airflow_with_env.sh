#!/usr/bin/env bash
# PM2 entrypoint: load .env + Airflow Postgres metadata URL, then exec airflow CLI.
set -euo pipefail

ETL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ETL_DIR}/.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing ${ENV_FILE}" >&2
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

export AIRFLOW_HOME="${ETL_DIR}/airflow_home"
export AIRFLOW__CORE__DAGS_FOLDER="${ETL_DIR}/dags"
export AIRFLOW__CORE__LOAD_EXAMPLES=False
export AIRFLOW__CORE__EXECUTOR=LocalExecutor
export AIRFLOW__CORE__PARALLELISM=4
export AIRFLOW__DATABASE__SQL_ALCHEMY_CONN="postgresql+psycopg2://${PG_USER}:${ENC_PASS}@${PG_HOST}:${PG_PORT}/${AIRFLOW_METADATA_DATABASE}"

exec "${ETL_DIR}/venv/bin/airflow" "$@"
