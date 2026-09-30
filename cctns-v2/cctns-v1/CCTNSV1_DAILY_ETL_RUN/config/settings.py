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

# --- Postgres (destination: cctns_v1 database on dopams-new) ---
PG_HOST = os.environ.get("PG_HOST", "192.168.103.106")
PG_PORT = os.environ.get("PG_PORT", "5432")
PG_DATABASE = os.environ.get("PG_DATABASE", "cctns_v1")
PG_ETL_SCHEMA = os.environ.get("PG_ETL_SCHEMA", "cctns")
PG_AIRFLOW_SCHEMA = os.environ.get("PG_AIRFLOW_SCHEMA", "airflow")
PG_USER = os.environ.get("PG_USER")
PG_PASSWORD = os.environ.get("PG_PASSWORD")

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
