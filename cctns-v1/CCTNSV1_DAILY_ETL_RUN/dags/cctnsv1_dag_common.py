"""Shared Airflow defaults and clear DAG identity for CCTNS V1."""
from datetime import datetime, timedelta

# Prefer package import (PYTHONPATH=ETL root). Fall back for direct loads.
try:
    from dags.alerts import notify_task_failure
except ImportError:  # pragma: no cover - Airflow may load siblings under dags/
    from alerts import notify_task_failure

# Clear DAG ids (shown in Airflow UI) — name = what the DAG loads.
# One data cycle. The previous split DAG ids are not scheduled; ETL-3 still
# treats a live process with those ids as "V1 is running" during rollout.
DAG_ID_DAILY_CYCLE = "cctns_v1_daily_cycle"
DAG_ID_FIR_COURT_ACCUSED_DETAILS = "cctns_v1_daily_sync_fir_court_accused_details"
DAG_ID_ACCUSED_DOSSIER = "cctns_v1_daily_sync_accused_dossier"
DAG_ID_MEDIA_ATTACHMENTS = "cctns_v1_daily_sync_media_attachments"

# Airflow cron is UTC. India (IST = UTC+5:30). This matches the live V2
# crontab `0 */6 * * *` UTC. V2's crontab is not in this repo and is not changed.
#   05:30 IST → 00:00 UTC
#   11:30 IST → 06:00 UTC
#   17:30 IST → 12:00 UTC
#   23:30 IST → 18:00 UTC
SCHEDULE_DAILY_CYCLE = "0 0,6,12,18 * * *"
#   05:00 IST → 23:30 UTC — Media attachments. Not part of the ETL-3 V1 gate.
SCHEDULE_MEDIA_ATTACHMENTS = "30 23 * * *"

# Retired clocks. Kept so older imports fail closed instead of scheduling a second cycle.
SCHEDULE_FIR_COURT_ACCUSED_DETAILS = None
SCHEDULE_ACCUSED_DOSSIER = None


START_DATE = datetime(2026, 9, 28)

DEFAULT_ARGS = {
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "on_failure_callback": notify_task_failure,
}

TAGS_BASE = ["cctns", "v1", "etl", "postgres", "nightly"]

# Back-compat aliases (older docs / imports). Not a schedule.
SCHEDULE_SIMPLE_APIS_IST = SCHEDULE_DAILY_CYCLE
SCHEDULE_ACCUSED_DOSSIER_IST = None
