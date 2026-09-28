"""Shared Airflow defaults for CCTNS V1 DAGs."""
from datetime import datetime, timedelta

SCHEDULE_DAILY_0030_UTC = "30 0 * * *"
# Accused dossier runs after simple-apis DAG (long month-chunked POST pull).
SCHEDULE_DAILY_0130_UTC = "30 1 * * *"
START_DATE = datetime(2026, 9, 28)

DEFAULT_ARGS = {
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
}

TAGS_BASE = ["cctns", "v1", "etl", "postgres", "nightly"]
