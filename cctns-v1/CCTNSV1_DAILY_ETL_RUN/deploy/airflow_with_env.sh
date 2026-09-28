#!/usr/bin/env bash
# PM2 entrypoint: .env → same Postgres DB as ETL (PG_DATABASE) → airflow CLI.
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
PG_AIRFLOW_SCHEMA="${PG_AIRFLOW_SCHEMA:-airflow}"

ENC_PASS="$("${ETL_DIR}/venv/bin/python3" -c "import urllib.parse, os; print(urllib.parse.quote_plus(os.environ['PG_PASSWORD']))")"

export AIRFLOW_HOME="${ETL_DIR}/airflow_home"
export AIRFLOW__CORE__DAGS_FOLDER="${ETL_DIR}/dags"
export AIRFLOW__CORE__LOAD_EXAMPLES=False
export AIRFLOW__CORE__EXECUTOR=LocalExecutor
export AIRFLOW__CORE__PARALLELISM=4
export AIRFLOW__DATABASE__SQL_ALCHEMY_CONN="postgresql+psycopg2://${PG_USER}:${ENC_PASS}@${PG_HOST}:${PG_PORT}/${PG_DATABASE}"
export AIRFLOW__DATABASE__SQL_ALCHEMY_SCHEMA="${PG_AIRFLOW_SCHEMA}"

_ensure_postgres_schemas() {
  export PGPASSWORD="${PG_PASSWORD}"
  psql -h "${PG_HOST}" -p "${PG_PORT}" -U "${PG_USER}" -d "${PG_DATABASE}" -v ON_ERROR_STOP=1 <<SQL
CREATE SCHEMA IF NOT EXISTS ${PG_ETL_SCHEMA};
CREATE SCHEMA IF NOT EXISTS ${PG_AIRFLOW_SCHEMA};
SQL
}

_ensure_cctns_database() {
  PYTHONPATH="${ETL_DIR}" "${ETL_DIR}/venv/bin/python3" -c \
    "from db.init_schema import ensure_database_exists; ensure_database_exists()"
}

_ensure_airflow_tables() {
  _ensure_cctns_database
  _ensure_postgres_schemas
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
