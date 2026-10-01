"""
Daily CCTNS V1 sync: FIR + Court + Accused Details (full unfiltered GET each).

Airflow DAG id: cctns_v1_daily_sync_fir_court_accused_details
Schedule: 00:30 IST daily

Graph:
    bootstrap_database → sync_fir → sync_court
                                  ↘ sync_accused_details
"""
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from airflow.decorators import dag, task

from dags.cctnsv1_dag_common import (
    DAG_ID_FIR_COURT_ACCUSED_DETAILS,
    DEFAULT_ARGS,
    SCHEDULE_FIR_COURT_ACCUSED_DETAILS,
    START_DATE,
    TAGS_BASE,
)

DAG_DOC = """
## CCTNS V1 — daily sync: FIR + Court + Accused Details (**00:30 IST**)

Loads the three **fast, unfiltered GET** APIs into Postgres (`cctns` schema).

| Task | API | Target table | Action |
|------|-----|--------------|--------|
| `bootstrap_database` | — | DB / DDL | Ensure schema |
| `sync_fir` | FIR GET | `cctns_fir` | Upsert on `fir_reg_num` |
| `sync_court` | Court GET | `cctns_court` | Upsert on `natural_key` |
| `sync_accused_details` | Accused Details GET | `cctns_accused_details` | Upsert on `natural_key` |

Court + accused_details run **in parallel after FIR** (parent FIR must exist first).
The accused-dossier DAG waits for **this DAG to succeed** before it runs.
"""


@dag(
    dag_id=DAG_ID_FIR_COURT_ACCUSED_DETAILS,
    description=(
        "Daily 00:30 IST: sync CCTNS FIR + Court + Accused Details "
        "(full GET → upsert into Postgres)"
    ),
    schedule=SCHEDULE_FIR_COURT_ACCUSED_DETAILS,
    start_date=START_DATE,
    catchup=False,
    max_active_runs=1,
    dagrun_timeout=timedelta(hours=2),
    default_args=DEFAULT_ARGS,
    tags=[*TAGS_BASE, "fir", "court", "accused-details", "full-get"],
    doc_md=DAG_DOC,
)
def cctns_v1_daily_sync_fir_court_accused_details():
    @task(
        task_id="bootstrap_database",
        doc_md="Ensure `cctns_v1` DB + ETL DDL + Airflow metadata tables exist.",
    )
    def bootstrap_database() -> dict:
        from db.airflow_metadata import ensure_airflow_metadata
        from db.init_schema import ensure_schema

        ensure_schema()
        ensure_airflow_metadata()
        return {"status": "schema_ready", "database": "cctns_v1"}

    @task(
        task_id="sync_fir",
        execution_timeout=timedelta(minutes=45),
        doc_md="Unfiltered FIR GET → upsert `cctns_fir` on `fir_reg_num`.",
    )
    def sync_fir() -> dict:
        from dags.pipeline_run import raise_if_task_failed, run_single_entity

        result = run_single_entity("fir")
        raise_if_task_failed("fir", result)
        return result

    @task(
        task_id="sync_court",
        execution_timeout=timedelta(minutes=45),
        doc_md="Unfiltered Court GET → upsert `cctns_court` on `natural_key`.",
    )
    def sync_court() -> dict:
        from dags.pipeline_run import raise_if_task_failed, run_single_entity

        result = run_single_entity("court")
        raise_if_task_failed("court", result)
        return result

    @task(
        task_id="sync_accused_details",
        execution_timeout=timedelta(minutes=45),
        doc_md="Unfiltered Accused Details GET → upsert `cctns_accused_details`.",
    )
    def sync_accused_details() -> dict:
        from dags.pipeline_run import raise_if_task_failed, run_single_entity

        result = run_single_entity("accused_details")
        raise_if_task_failed("accused_details", result)
        return result

    boot = bootstrap_database()
    fir = sync_fir()
    court = sync_court()
    accused_details = sync_accused_details()

    boot >> fir >> [court, accused_details]


cctns_v1_daily_sync_fir_court_accused_details()
