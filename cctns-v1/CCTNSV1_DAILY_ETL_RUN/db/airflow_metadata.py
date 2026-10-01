"""
Airflow internal tables live in a dedicated Postgres database (PG_AIRFLOW_DATABASE),
not in the ETL/business database (PG_DATABASE / cctns_v1).

Creates/updates them via `airflow db migrate` (idempotent).
"""
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger("cctns_v1_etl.db")

_ETL_ROOT = Path(__file__).resolve().parent.parent
_WRAPPER = _ETL_ROOT / "deploy" / "airflow_with_env.sh"


def ensure_airflow_metadata() -> None:
    if not _WRAPPER.is_file():
        raise FileNotFoundError(_WRAPPER)
    logger.info("ensuring Airflow metadata tables in PG_AIRFLOW_DATABASE (separate from ETL DB)")
    subprocess.run(
        [str(_WRAPPER), "db", "migrate"],
        cwd=str(_ETL_ROOT),
        check=True,
    )
