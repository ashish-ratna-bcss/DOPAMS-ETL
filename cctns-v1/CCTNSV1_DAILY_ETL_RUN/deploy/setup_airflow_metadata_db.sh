#!/usr/bin/env bash
# Optional manual run — scheduler/webserver do this automatically on start.
set -euo pipefail

ETL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
chmod +x "${ETL_DIR}/deploy/airflow_with_env.sh"
"${ETL_DIR}/deploy/airflow_with_env.sh" db migrate
echo "Done. Airflow metadata lives in PG_DATABASE (default: cctns_v1), same as ETL tables."
