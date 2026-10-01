"""
DAG 1 of 2 — FIR, Court, Accused Details (single GET each, no date chunking).

Graph (Airflow UI):
    bootstrap_database → sync_fir → sync_court
                                  ↘ sync_accused_details
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from airflow.decorators import dag, task

from dags.cctnsv1_dag_common import (
    DEFAULT_ARGS,
    SCHEDULE_SIMPLE_APIS_IST,
    START_DATE,
    TAGS_BASE,
)

DAG_DOC = """
## CCTNS V1 — simple APIs (daily **00:30 IST**)

Full pull every run; Postgres upsert decides insert / update / skip.

| Task | CCTNS API | Target table | Load today? |
|------|-----------|--------------|-------------|
| `bootstrap_database` | — | `cctns_v1` DB + ETL + Airflow tables | always |
| `sync_fir` | FIR GET | `cctns_fir` | **Yes** (PK `fir_reg_num`) |
| `sync_court` | Court GET | `cctns_court` | Fetch only — needs `natural_key` |
| `sync_accused_details` | Accused Details GET | `cctns_accused_details` | Fetch only — needs `natural_key` |

Court and accused details run **in parallel** after FIR (FK to `cctns_fir` when load is enabled).
See `db/sql/001_schema_fix.sql` for the pending upsert keys.
"""


@dag(
    dag_id="cctnsv1_simple_apis_etl",
    description=(
        "CCTNS V1 nightly: FIR upsert + Court & Accused Details fetch "
        "(unfiltered GET APIs → Postgres cctns_v1)"
    ),
    schedule=SCHEDULE_SIMPLE_APIS_IST,
    start_date=START_DATE,
    catchup=False,
    default_args=DEFAULT_ARGS,
    tags=[*TAGS_BASE, "fir", "court", "accused-details", "simple-apis"],
    doc_md=DAG_DOC,
)
def cctnsv1_simple_apis_etl():
    @task(
        task_id="bootstrap_database",
        doc_md=(
            "Create `PG_DATABASE` if missing; ETL DDL (`init_schema.sql`); "
            "Airflow metadata tables in dedicated `PG_AIRFLOW_DATABASE` (`airflow db migrate`)."
        ),
    )
    def bootstrap_database() -> dict:
        from db.airflow_metadata import ensure_airflow_metadata
        from db.init_schema import ensure_schema

        ensure_schema()
        ensure_airflow_metadata()
        return {"status": "schema_ready", "database": "cctns_v1"}

    @task(
        task_id="sync_fir",
        doc_md="Unfiltered FIR GET (~7.3k rows) → upsert into `cctns_fir` on `fir_reg_num`.",
    )
    def sync_fir() -> dict:
        from dags.pipeline_run import raise_if_task_failed, run_single_entity

        result = run_single_entity("fir")
        raise_if_task_failed("fir", result)
        return result

    @task(
        task_id="sync_court",
        doc_md="Unfiltered Court GET (~7.7k rows). **Fetch logged only** until `natural_key` is applied.",
    )
    def sync_court() -> dict:
        from dags.pipeline_run import raise_if_task_failed, run_single_entity

        result = run_single_entity("court")
        raise_if_task_failed("court", result)
        return result

    @task(
        task_id="sync_accused_details",
        doc_md="Unfiltered Accused Details GET (~20k rows). **Fetch logged only** until `natural_key` is applied.",
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


cctnsv1_simple_apis_etl()
