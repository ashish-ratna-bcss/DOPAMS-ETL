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
# Must match .env (see .env.example) and config/settings.py defaults.
PG_ETL_SCHEMA="${PG_ETL_SCHEMA:-cctns}"
PG_AIRFLOW_SCHEMA="${PG_AIRFLOW_SCHEMA:-airflow}"
export PG_ETL_SCHEMA PG_AIRFLOW_SCHEMA

ENC_PASS="$("${ETL_DIR}/venv/bin/python3" -c "import urllib.parse, os; print(urllib.parse.quote_plus(os.environ['PG_PASSWORD']))")"
# Custom metadata schema requires search_path on the URI (Airflow 2.10+), or log_template
# is not seeded and DAG trigger fails with a generic UI "Ooops!" / TypeError on log_template_id.
SEARCH_PATH_QUERY="$("${ETL_DIR}/venv/bin/python3" -c "import os, urllib.parse; s=os.environ['PG_AIRFLOW_SCHEMA']; print('options=' + urllib.parse.quote('-csearch_path=' + s, safe=''))")"

export AIRFLOW_HOME="${ETL_DIR}/airflow_home"
export AIRFLOW__CORE__DAGS_FOLDER="${ETL_DIR}/dags"
export AIRFLOW__CORE__LOAD_EXAMPLES=False
export AIRFLOW__CORE__DEFAULT_UI_TIMEZONE=Asia/Kolkata
export AIRFLOW__CORE__EXECUTOR=LocalExecutor
export AIRFLOW__CORE__PARALLELISM=4
export AIRFLOW__DATABASE__SQL_ALCHEMY_CONN="postgresql+psycopg2://${PG_USER}:${ENC_PASS}@${PG_HOST}:${PG_PORT}/${PG_DATABASE}?${SEARCH_PATH_QUERY}"
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

_ensure_log_template_row() {
  export PGPASSWORD="${PG_PASSWORD}"
  local count
  count="$(psql -h "${PG_HOST}" -p "${PG_PORT}" -U "${PG_USER}" -d "${PG_DATABASE}" -tAc \
    "SELECT COUNT(*) FROM ${PG_AIRFLOW_SCHEMA}.log_template" 2>/dev/null || echo 0)"
  count="${count// /}"
  if [[ "${count}" != "0" ]]; then
    return 0
  fi
  echo "Seeding ${PG_AIRFLOW_SCHEMA}.log_template (required for DAG trigger with custom schema)" >&2
  psql -h "${PG_HOST}" -p "${PG_PORT}" -U "${PG_USER}" -d "${PG_DATABASE}" -v ON_ERROR_STOP=1 <<SQL
INSERT INTO ${PG_AIRFLOW_SCHEMA}.log_template (filename, elasticsearch_id)
SELECT p.filename, p.elasticsearch_id
FROM public.log_template p
WHERE NOT EXISTS (SELECT 1 FROM ${PG_AIRFLOW_SCHEMA}.log_template)
ORDER BY p.id DESC
LIMIT 1;
INSERT INTO ${PG_AIRFLOW_SCHEMA}.log_template (filename, elasticsearch_id)
SELECT
  'dag_id={{ ti.dag_id }}/run_id={{ ti.run_id }}/task_id={{ ti.task_id }}/{% if ti.map_index >= 0 %}map_index={{ ti.map_index }}/{% endif %}attempt={{ try_number }}.log',
  '{dag_id}-{task_id}-{run_id}-{map_index}-{try_number}'
WHERE NOT EXISTS (SELECT 1 FROM ${PG_AIRFLOW_SCHEMA}.log_template);
SQL
}

_ensure_airflow_tables() {
  _ensure_cctns_database
  _ensure_postgres_schemas
  "${AF}" db migrate
  _ensure_log_template_row
}

_ensure_admin_user() {
  # Never ship a default password. Require a strong secret from .env.
  local user="${AIRFLOW_ADMIN_USERNAME:-admin}"
  local email="${AIRFLOW_ADMIN_EMAIL:-admin@localhost}"
  if [[ -z "${AIRFLOW_ADMIN_PASSWORD:-}" ]]; then
    echo "AIRFLOW_ADMIN_PASSWORD is required in .env (min 12 chars, not 'admin')." >&2
    exit 1
  fi
  if [[ "${AIRFLOW_ADMIN_PASSWORD}" == "admin" || ${#AIRFLOW_ADMIN_PASSWORD} -lt 12 ]]; then
    echo "AIRFLOW_ADMIN_PASSWORD must be at least 12 characters and must not be 'admin'." >&2
    exit 1
  fi
  if "${AF}" users list 2>/dev/null | grep -qE "[[:space:]]${user}[[:space:]]"; then
    echo "Resetting Airflow user '${user}' password from AIRFLOW_ADMIN_PASSWORD" >&2
    "${AF}" users reset-password --username "${user}" --password "${AIRFLOW_ADMIN_PASSWORD}" >/dev/null
  else
    echo "Creating Airflow Admin user '${user}' (password from AIRFLOW_ADMIN_PASSWORD)" >&2
    "${AF}" users create \
      --username "${user}" --password "${AIRFLOW_ADMIN_PASSWORD}" \
      --firstname CCTNS --lastname Admin --role Admin --email "${email}"
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

# Web UI bind: set AIRFLOW_WEBSERVER_HOST to a private IP (recommended) instead of 0.0.0.0.
if [[ "$1" == "webserver" ]]; then
  AF_PORT="${AIRFLOW_WEBSERVER_PORT:-9001}"
  AF_HOST="${AIRFLOW_WEBSERVER_HOST:-0.0.0.0}"
  shift
  exec "${AF}" webserver --port "${AF_PORT}" --hostname "${AF_HOST}" "$@"
fi

exec "${AF}" "$@"
