"""
Airflow internal tables live in the same Postgres database as ETL data (PG_DATABASE).

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
    logger.info("ensuring Airflow metadata tables in database (same as PG_DATABASE)")
    subprocess.run(
        [str(_WRAPPER), "db", "migrate"],
        cwd=str(_ETL_ROOT),
        check=True,
    )
