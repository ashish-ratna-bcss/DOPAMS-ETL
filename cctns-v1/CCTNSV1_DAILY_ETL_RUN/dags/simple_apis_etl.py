"""
DAG 1 of 2: the 3 plain CCTNS V1 endpoints (FIR, Court, Accused Details).

These 3 all support a single unfiltered GET that returns the full dataset
in a few seconds each (confirmed from real captured responses) -- no date
chunking needed, unlike the Accused date-range endpoint (see
accused_yearly_etl.py for that one).

Logic lives in pipeline_run.run_simple_apis(); this file is just the
Airflow schedule wrapper around it.
"""
import os
import sys
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def run_simple_apis_task(**_context):
    from dags.pipeline_run import run_simple_apis
    run_id, summary = run_simple_apis()
    failures = [e for e, r in summary.items() if r.get("status") in ("extract_failed", "load_failed")]
    if failures:
        raise RuntimeError(f"run_id={run_id} entities failed: {failures}")


with DAG(
    dag_id="cctnsv1_simple_apis_etl",
    description="Nightly full pull + upsert of CCTNS V1 FIR / Court / Accused Details into Postgres cctns_v1",
    schedule="30 0 * * *",
    start_date=datetime(2026, 9, 28),
    catchup=False,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=5)},
    tags=["cctns", "v1", "etl", "simple-apis"],
) as dag:

    run_simple_apis_op = PythonOperator(
        task_id="run_simple_apis",
        python_callable=run_simple_apis_task,
    )
