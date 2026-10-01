"""Shared Airflow defaults for CCTNS V1 DAGs."""
from datetime import datetime, timedelta

from dags.alerts import notify_task_failure

# Airflow cron is UTC. India (IST = UTC+5:30):
#   00:30 IST → 19:00 UTC (previous calendar day in UTC) — simple APIs
SCHEDULE_SIMPLE_APIS_IST = "0 19 * * *"
#   01:30 IST → 20:00 UTC — accused dossier (1 hour after simple APIs)
SCHEDULE_ACCUSED_DOSSIER_IST = "0 20 * * *"
START_DATE = datetime(2026, 9, 28)

DEFAULT_ARGS = {
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "on_failure_callback": notify_task_failure,
}

TAGS_BASE = ["cctns", "v1", "etl", "postgres", "nightly"]
