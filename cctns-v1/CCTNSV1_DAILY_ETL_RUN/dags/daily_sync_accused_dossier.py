"""
Daily CCTNS V1 sync: Accused dossier (date-range POST, 7-day chunks).

Airflow DAG id: cctns_v1_daily_sync_accused_dossier
Schedule: 01:30 IST daily — but only after FIR/Court/Accused-Details DAG succeeds.

Graph:
    wait_for_fir_court_accused_details → bootstrap_database → sync_accused_dossier
"""
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from airflow.decorators import dag, task
from airflow.sensors.external_task import ExternalTaskSensor

from dags.cctnsv1_dag_common import (
    DAG_ID_ACCUSED_DOSSIER,
    DAG_ID_FIR_COURT_ACCUSED_DETAILS,
    DEFAULT_ARGS,
    SCHEDULE_ACCUSED_DOSSIER,
    START_DATE,
    TAGS_BASE,
    UPSTREAM_EXECUTION_DELTA,
)

DAG_DOC = """
## CCTNS V1 — daily sync: Accused dossier (**01:30 IST**)

Loads the **slow date-range Accused POST** into `cctns_accused` (7-day chunks,
adaptive split on ORA-06502).

| Task | What it does |
|------|----------------|
| `wait_for_fir_court_accused_details` | Blocks until today's FIR/Court/Accused-Details DAG **succeeds** |
| `bootstrap_database` | Ensure DB / DDL |
| `sync_accused_dossier` | POST date-range → validate → upsert `cctns_accused` |

Pull window: `ACCUSED_FULL_PULL_START_DATE` → today (`.env`).
Any failed date window → task fails (fail-closed); no partial upsert.
"""


@dag(
    dag_id=DAG_ID_ACCUSED_DOSSIER,
    description=(
        "Daily 01:30 IST: sync CCTNS Accused dossier by date range "
        "(7-day chunks → upsert cctns_accused); waits for FIR/Court/Details DAG"
    ),
    schedule=SCHEDULE_ACCUSED_DOSSIER,
    start_date=START_DATE,
    catchup=False,
    max_active_runs=1,
    dagrun_timeout=timedelta(hours=10),
    default_args=DEFAULT_ARGS,
    tags=[*TAGS_BASE, "accused-dossier", "date-range", "chunked"],
    doc_md=DAG_DOC,
)
def cctns_v1_daily_sync_accused_dossier():
    wait_for_upstream = ExternalTaskSensor(
        task_id="wait_for_fir_court_accused_details",
        external_dag_id=DAG_ID_FIR_COURT_ACCUSED_DETAILS,
        external_task_id=None,
        allowed_states=["success"],
        failed_states=["failed"],
        execution_delta=UPSTREAM_EXECUTION_DELTA,
        mode="reschedule",
        poke_interval=120,
        timeout=60 * 60 * 4,
        check_existence=True,
        doc_md=(
            f"Wait until `{DAG_ID_FIR_COURT_ACCUSED_DETAILS}` for this night "
            f"(execution_delta={UPSTREAM_EXECUTION_DELTA}) finishes successfully."
        ),
    )

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
        task_id="sync_accused_dossier",
        execution_timeout=timedelta(hours=8),
        doc_md=(
            "Accused date-range POST from ACCUSED_FULL_PULL_START_DATE → today, "
            "7 days at a time with halving on ORA-06502. Fail-closed on any window."
        ),
    )
    def sync_accused_dossier() -> dict:
        from dags.pipeline_run import raise_if_task_failed, run_single_entity

        result = run_single_entity("accused")
        raise_if_task_failed("accused", result)
        return result

    boot = bootstrap_database()
    sync = sync_accused_dossier()
    wait_for_upstream >> boot >> sync


cctns_v1_daily_sync_accused_dossier()
