"""Shared Airflow defaults and clear DAG identity for CCTNS V1."""
from datetime import datetime, timedelta

# Prefer package import (PYTHONPATH=ETL root). Fall back for direct loads.
try:
    from dags.alerts import notify_task_failure
except ImportError:  # pragma: no cover - Airflow may load siblings under dags/
    from alerts import notify_task_failure

# Clear DAG ids (shown in Airflow UI) — name = what the DAG loads.
DAG_ID_FIR_COURT_ACCUSED_DETAILS = "cctns_v1_daily_sync_fir_court_accused_details"
DAG_ID_ACCUSED_DOSSIER = "cctns_v1_daily_sync_accused_dossier"
DAG_ID_MEDIA_ATTACHMENTS = "cctns_v1_daily_sync_media_attachments"

# Airflow cron is UTC. India (IST = UTC+5:30):
#   00:30 IST → 19:00 UTC — FIR + Court + Accused Details
SCHEDULE_FIR_COURT_ACCUSED_DETAILS = "0 19 * * *"
#   01:30 IST → 20:00 UTC — Accused dossier (1h later; also waits on DAG above)
SCHEDULE_ACCUSED_DOSSIER = "0 20 * * *"
#   05:00 IST → 23:30 UTC — Media attachments (downloads new PDFs to disk)
SCHEDULE_MEDIA_ATTACHMENTS = "30 23 * * *"
# Accused sensor: look back 1 hour for the upstream DAG's scheduled run.

UPSTREAM_EXECUTION_DELTA = timedelta(hours=1)


START_DATE = datetime(2026, 9, 28)

DEFAULT_ARGS = {
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "on_failure_callback": notify_task_failure,
}

TAGS_BASE = ["cctns", "v1", "etl", "postgres", "nightly"]

# Back-compat aliases (older docs / imports)
SCHEDULE_SIMPLE_APIS_IST = SCHEDULE_FIR_COURT_ACCUSED_DETAILS
SCHEDULE_ACCUSED_DOSSIER_IST = SCHEDULE_ACCUSED_DOSSIER
