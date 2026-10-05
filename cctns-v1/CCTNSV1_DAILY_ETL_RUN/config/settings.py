"""
Single place every other module reads config from. Nothing here talks to the
API or the DB directly -- it just loads .env and exposes typed values.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# Always load from ETL project root (Airflow task cwd is not guaranteed).
_ETL_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ETL_ROOT / ".env")

# --- CCTNS V1 API (source) --- no auth required, confirmed live against all 4 endpoints
FIR_API_URL = os.environ.get("FIR_API_URL")
COURT_API_URL = os.environ.get("COURT_API_URL")
ACCUSED_DETAILS_API_URL = os.environ.get("ACCUSED_DETAILS_API_URL")
ACCUSED_API_URL = os.environ.get("ACCUSED_API_URL")
ALFRESCO_DOWNLOAD_API_URL = os.environ.get("ALFRESCO_DOWNLOAD_API_URL")

# --- Media Storage Configuration ---
MEDIA_BASE_DIR = os.environ.get("MEDIA_BASE_DIR", "/home/tganb/dopams/media_cctnsv1")
try:
    MEDIA_DOWNLOAD_CONCURRENCY = int(os.environ.get("MEDIA_DOWNLOAD_CONCURRENCY", "5"))
except ValueError:
    MEDIA_DOWNLOAD_CONCURRENCY = 5

try:
    MEDIA_DOWNLOAD_TIMEOUT_SECS = int(os.environ.get("MEDIA_DOWNLOAD_TIMEOUT_SECS", "60"))
except ValueError:
    MEDIA_DOWNLOAD_TIMEOUT_SECS = 60


# --- Postgres (destination: cctns_v1 database on dopams-new) ---
# PG_HOST has no default — missing .env must fail, not silently hit a LAN IP.
PG_HOST = os.environ.get("PG_HOST")
PG_PORT = os.environ.get("PG_PORT", "5432")
PG_DATABASE = os.environ.get("PG_DATABASE", "cctns_v1")
PG_ETL_SCHEMA = os.environ.get("PG_ETL_SCHEMA", "cctns")
PG_AIRFLOW_SCHEMA = os.environ.get("PG_AIRFLOW_SCHEMA", "airflow")
PG_USER = os.environ.get("PG_USER")
PG_PASSWORD = os.environ.get("PG_PASSWORD")
# Explicit TLS mode for Postgres clients (prefer|require|verify-full|disable|…).
# Default prefer: use SSL when the server offers it, else fall back (LAN-safe).
# Set PG_SSLMODE=require once the Postgres server has TLS certificates.
PG_SSLMODE = os.environ.get("PG_SSLMODE", "prefer")

# Optional: POST JSON failure alerts (Slack/Teams/webhook). Empty = file+log only.
CCTNS_ALERT_WEBHOOK_URL = os.environ.get("CCTNS_ALERT_WEBHOOK_URL", "").strip()

# Fail-closed: max orphan FIR rows allowed before validate fails (default 0 = any orphan fails).
try:
    CCTNS_ORPHAN_FIR_MAX = int(os.environ.get("CCTNS_ORPHAN_FIR_MAX", "0"))
except ValueError:
    CCTNS_ORPHAN_FIR_MAX = 0

# Same Postgres database (PG_DATABASE); ETL tables in PG_ETL_SCHEMA, Airflow in PG_AIRFLOW_SCHEMA.

# --- Pull behavior ---
# How far back the Accused date-range endpoint pulls, every night, in full.
# The other 3 endpoints (FIR/Court/Accused Details) take no date range at all
# -- confirmed against real captured responses that a single unfiltered GET
# returns the full dataset in a few seconds, no chunking needed.
ACCUSED_FULL_PULL_START_DATE = os.environ.get("ACCUSED_FULL_PULL_START_DATE", "01-01-2002")


def require(*names):
    """Fail fast and clearly if required config is missing, instead of a
    confusing error deep inside a request or DB call."""
    missing = [n for n in names if not globals().get(n)]
    if missing:
        raise RuntimeError(f"Missing required .env values: {', '.join(missing)}")
