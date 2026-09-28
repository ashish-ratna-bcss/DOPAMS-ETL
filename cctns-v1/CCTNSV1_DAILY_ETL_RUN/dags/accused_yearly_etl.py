"""
DAG 2 of 2: the Accused date-range endpoint only.

This is the one endpoint that needs date chunking -- its Oracle backend
throws ORA-06502 "buffer too small" on wide date ranges (confirmed from
real captured runs in cctnsv1/response/accused_list_yearly_range/, which
show failedWindows even at yearly granularity). apis/accused.py pulls this
one month by month across the full history, with adaptive halving down to
single days on failure.

Kept as its own DAG (separate from simple_apis_etl.py) since this pull is
slower and more failure-prone than the other 3 -- easier to monitor/retry
independently.

Logic lives in pipeline_run.run_accused_yearly(); this file is just the
Airflow schedule wrapper around it.
"""
import os
import sys
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def run_accused_yearly_task(**_context):
    from dags.pipeline_run import run_accused_yearly
    run_id, summary = run_accused_yearly()
    failures = [e for e, r in summary.items() if r.get("status") in ("extract_failed", "load_failed")]
    if failures:
        raise RuntimeError(f"run_id={run_id} entities failed: {failures}")


with DAG(
    dag_id="cctnsv1_accused_yearly_etl",
    description="Nightly full month-chunked pull + upsert of CCTNS V1 Accused date-range endpoint into Postgres cctns_v1",
    schedule="30 0 * * *",
    start_date=datetime(2026, 9, 28),
    catchup=False,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=5)},
    tags=["cctns", "v1", "etl", "accused-yearly"],
) as dag:

    run_accused_yearly_op = PythonOperator(
        task_id="run_accused_yearly",
        python_callable=run_accused_yearly_task,
    )
