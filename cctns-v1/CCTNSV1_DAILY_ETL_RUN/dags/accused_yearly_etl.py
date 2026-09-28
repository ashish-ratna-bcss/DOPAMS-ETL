"""
DAG 2 of 2 — Accused dossier date-range API (month-chunked, adaptive halving).

Graph (Airflow UI):
    bootstrap_database → sync_accused_dossier

Same load pipeline as simple-apis DAG: extract → dedupe + FIR check → upsert → run/row logs.
"""
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from airflow.decorators import dag, task

from dags.cctnsv1_dag_common import (
    DEFAULT_ARGS,
    SCHEDULE_ACCUSED_DOSSIER_IST,
    START_DATE,
    TAGS_BASE,
)

DAG_DOC = """
## CCTNS V1 — accused dossier (daily **01:30 IST**, after simple-apis DAG)

Separate DAG because this endpoint is **slow** and **Oracle-sensitive**
(ORA-06502 buffer errors → month chunks + adaptive day splitting).

| Task | CCTNS API | Target table | Load today? |
|------|-----------|--------------|-------------|
| `bootstrap_database` | — | `cctns_v1` DB + ETL + Airflow tables | always |
| `sync_accused_dossier` | Accused POST date-range | `cctns_accused` | **Yes** — upsert on `natural_key` |

Pull window: `ACCUSED_FULL_PULL_START_DATE` → today (see `.env`).
"""


@dag(
    dag_id="cctnsv1_accused_yearly_etl",
    description=(
        "CCTNS V1 nightly: month-chunked Accused dossier API "
        "(date-range POST → Postgres cctns_accused, month-chunked full pull)"
    ),
    schedule=SCHEDULE_ACCUSED_DOSSIER_IST,
    start_date=START_DATE,
    catchup=False,
    default_args=DEFAULT_ARGS,
    tags=[*TAGS_BASE, "accused-dossier", "date-range", "yearly-chunked"],
    doc_md=DAG_DOC,
)
def cctnsv1_accused_yearly_etl():
    @task(
        task_id="bootstrap_database",
        doc_md=(
            "Create `PG_DATABASE` if missing; ETL DDL; Airflow metadata in the same DB."
        ),
    )
    def bootstrap_database() -> dict:
        from db.airflow_metadata import ensure_airflow_metadata
        from db.init_schema import ensure_schema

        ensure_schema()
        ensure_airflow_metadata()
        return {"status": "schema_ready", "database": "cctns_v1"}

    @task(
        task_id="sync_accused_dossier",
        execution_timeout=timedelta(hours=8),
        doc_md=(
            "Accused date-range POST from ACCUSED_FULL_PULL_START_DATE → today, "
            "month-by-month with halving on ORA-06502. Then validate + upsert + logs. "
            "Check task log for `failed_windows` if any chunk never succeeded."
        ),
    )
    def sync_accused_dossier() -> dict:
        from dags.pipeline_run import raise_if_task_failed, run_single_entity

        result = run_single_entity("accused")
        raise_if_task_failed("accused", result)
        return result

    boot = bootstrap_database()
    sync = sync_accused_dossier()
    boot >> sync


cctnsv1_accused_yearly_etl()
