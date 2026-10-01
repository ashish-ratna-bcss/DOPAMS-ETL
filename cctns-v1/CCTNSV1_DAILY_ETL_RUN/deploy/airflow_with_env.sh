#!/usr/bin/env bash
# PM2 entrypoint: .env → ETL DB for bootstrap + dedicated Airflow metadata DB → airflow CLI.
# Airflow tables do NOT live in PG_DATABASE (cctns_v1); they use PG_AIRFLOW_DATABASE.
set -euo pipefail

ETL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ETL_DIR}/.env"
AF="${ETL_DIR}/venv/bin/airflow"

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
PG_DATABASE="${PG_DATABASE:-cctns_v1}"
PG_ETL_SCHEMA="${PG_ETL_SCHEMA:-cctns}"
# Dedicated DB for Airflow metadata (keeps cctns_v1 business-only).
PG_AIRFLOW_DATABASE="${PG_AIRFLOW_DATABASE:-cctns_v1_airflow}"
export PG_ETL_SCHEMA PG_AIRFLOW_DATABASE

ENC_PASS="$("${ETL_DIR}/venv/bin/python3" -c "import urllib.parse, os; print(urllib.parse.quote_plus(os.environ['PG_PASSWORD']))")"

export AIRFLOW_HOME="${ETL_DIR}/airflow_home"
export AIRFLOW__CORE__DAGS_FOLDER="${ETL_DIR}/dags"
export AIRFLOW__CORE__LOAD_EXAMPLES=False
export AIRFLOW__CORE__DEFAULT_UI_TIMEZONE=Asia/Kolkata
export AIRFLOW__CORE__EXECUTOR=LocalExecutor
export AIRFLOW__CORE__PARALLELISM=4
export AIRFLOW__DATABASE__SQL_ALCHEMY_CONN="postgresql+psycopg2://${PG_USER}:${ENC_PASS}@${PG_HOST}:${PG_PORT}/${PG_AIRFLOW_DATABASE}"

_ensure_etl_database() {
  PYTHONPATH="${ETL_DIR}" "${ETL_DIR}/venv/bin/python3" -c \
    "from db.init_schema import ensure_database_exists; ensure_database_exists()"
}

_ensure_etl_schema() {
  export PGPASSWORD="${PG_PASSWORD}"
  psql -h "${PG_HOST}" -p "${PG_PORT}" -U "${PG_USER}" -d "${PG_DATABASE}" -v ON_ERROR_STOP=1 <<SQL
CREATE SCHEMA IF NOT EXISTS ${PG_ETL_SCHEMA};
SQL
}

_ensure_airflow_database() {
  export PGPASSWORD="${PG_PASSWORD}"
  local exists
  exists="$(psql -h "${PG_HOST}" -p "${PG_PORT}" -U "${PG_USER}" -d postgres -tAc \
    "SELECT 1 FROM pg_database WHERE datname = '${PG_AIRFLOW_DATABASE}'" || true)"
  exists="${exists// /}"
  if [[ "${exists}" == "1" ]]; then
    return 0
  fi
  echo "Creating Airflow metadata database ${PG_AIRFLOW_DATABASE}" >&2
  psql -h "${PG_HOST}" -p "${PG_PORT}" -U "${PG_USER}" -d postgres -v ON_ERROR_STOP=1 \
    -c "CREATE DATABASE ${PG_AIRFLOW_DATABASE} OWNER ${PG_USER}"
}

_ensure_airflow_tables() {
  _ensure_etl_database
  _ensure_etl_schema
  _ensure_airflow_database
  "${AF}" db migrate
}

_ensure_admin_user() {
  if ! "${AF}" users list 2>/dev/null | grep -qE '[[:space:]]admin[[:space:]]'; then
    echo "Creating Airflow admin user (admin / admin — change in UI)" >&2
    "${AF}" users create \
      --username admin --password admin \
      --firstname CCTNS --lastname Admin --role Admin --email admin@example.com
  fi
}

if [[ "$1" == "db" && "$2" == "migrate" ]]; then
  _ensure_airflow_tables
  exit 0
fi

if [[ "$1" == "scheduler" || "$1" == "webserver" ]]; then
  _ensure_airflow_tables
  if [[ "$1" == "scheduler" ]]; then
    _ensure_admin_user
  fi
fi

exec "${AF}" "$@"
