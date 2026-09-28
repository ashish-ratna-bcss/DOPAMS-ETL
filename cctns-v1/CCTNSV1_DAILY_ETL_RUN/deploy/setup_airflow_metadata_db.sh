#!/usr/bin/env bash
# One-time: create Postgres DB for Airflow metadata and run airflow db migrate.
set -euo pipefail

ETL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ETL_DIR}/.env"

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

: "${PG_USER:?}" "${PG_PASSWORD:?}"
PG_HOST="${PG_HOST:-localhost}"
PG_PORT="${PG_PORT:-5432}"
AIRFLOW_METADATA_DATABASE="${AIRFLOW_METADATA_DATABASE:-cctns_v1_airflow}"

export PGPASSWORD="$PG_PASSWORD"

echo "==> Ensuring database ${AIRFLOW_METADATA_DATABASE} exists on ${PG_HOST}"
exists="$(psql -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d postgres -tAc \
  "SELECT 1 FROM pg_database WHERE datname = '${AIRFLOW_METADATA_DATABASE}'" || true)"
if [[ "$exists" != "1" ]]; then
  psql -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d postgres -c \
    "CREATE DATABASE \"${AIRFLOW_METADATA_DATABASE}\";"
else
  echo "    already exists"
fi

ENC_PASS="$("${ETL_DIR}/venv/bin/python3" -c "import urllib.parse, os; print(urllib.parse.quote_plus(os.environ['PG_PASSWORD']))")"
export AIRFLOW__DATABASE__SQL_ALCHEMY_CONN="postgresql+psycopg2://${PG_USER}:${ENC_PASS}@${PG_HOST}:${PG_PORT}/${AIRFLOW_METADATA_DATABASE}"
export AIRFLOW_HOME="${ETL_DIR}/airflow_home"
export AIRFLOW__CORE__DAGS_FOLDER="${ETL_DIR}/dags"
export AIRFLOW__CORE__LOAD_EXAMPLES=False
export AIRFLOW__CORE__EXECUTOR=LocalExecutor

echo "==> Running airflow db migrate (metadata → Postgres)"
cd "$ETL_DIR"
./venv/bin/airflow db migrate

if ! ./venv/bin/airflow users list 2>/dev/null | grep -q admin; then
  echo "==> Creating Airflow admin user (admin / admin — change password in UI)"
  ./venv/bin/airflow users create \
    --username admin --password admin \
    --firstname CCTNS --lastname Admin --role Admin --email admin@example.com
fi

echo "==> Done. Run: ./deploy/reload_pm2.sh"
