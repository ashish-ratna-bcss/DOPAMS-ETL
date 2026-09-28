"""Shared Airflow defaults for CCTNS V1 DAGs."""
from datetime import datetime, timedelta

# Airflow cron is UTC. India (IST = UTC+5:30):
#   06:00 IST → 00:30 UTC — simple APIs (FIR, court, accused details)
SCHEDULE_DAILY_0030_UTC = "30 0 * * *"
#   07:00 IST → 01:30 UTC — accused dossier (date-range POST; runs after simple APIs)
SCHEDULE_DAILY_0130_UTC = "30 1 * * *"
START_DATE = datetime(2026, 9, 28)

DEFAULT_ARGS = {
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
}

TAGS_BASE = ["cctns", "v1", "etl", "postgres", "nightly"]
