"""
One CCTNS V1 data cycle, every 6 hours.

Airflow DAG id: cctns_v1_daily_cycle
Schedule: 00:00, 06:00, 12:00, 18:00 IST (cron 30 0,6,12,18 * * * UTC)

The task holds one cycle lock and one run_id:
    FIR, then Court and Accused Details together, then Accused.
The cycle-success marker is written only after all four succeed.
Media stays on its own DAG and is not part of this cycle.

max_active_runs=1 keeps the next 6-hour tick queued while this run is active.
The cycle lock refuses a second process, including CLI, for the same reason:
an accused pull can run for about 8 hours, longer than the 6-hour gap.
The 12-hour attempt limit is unchanged so that pull is not cut off at 6 hours.
"""
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from airflow.decorators import dag, task

from dags.cctnsv1_dag_common import (
    DAG_ID_DAILY_CYCLE,
    DEFAULT_ARGS,
    SCHEDULE_DAILY_CYCLE,
    START_DATE,
    TAGS_BASE,
)

# Accused alone is allowed 8 hours, which is longer than the 6-hour schedule.
# One attempt gets 12 hours so the next tick cannot truncate it. max_active_runs
# below stops that next tick from executing until this run leaves the slot.
# The DAG run ceiling still covers the two configured retries.
CYCLE_ATTEMPT_TIMEOUT = timedelta(hours=12)
CYCLE_DAGRUN_TIMEOUT = timedelta(hours=37)

DAG_DOC = """
## CCTNS V1 — one data cycle every 6 hours (**00:00, 06:00, 12:00, 18:00 IST**)

| Step | Entities | Rule |
|------|----------|------|
| 1 | FIR | Must finish `loaded` or `loaded_with_known_gaps` |
| 2 | Court and Accused Details | Together, only after FIR |
| 3 | Accused | Only after both step-2 entities succeed |
| 4 | Cycle marker | `cctns_v1_etl_cycle.status = succeeded` for this run's `run_id` |

`max_active_runs=1` plus the cycle lock: a tick that arrives while an accused
pull is still running does not start a second cycle. Each finished cycle has
its own `run_id` and marker. Media is a separate DAG and is not part of the marker.
"""


@dag(
    dag_id=DAG_ID_DAILY_CYCLE,
    description=(
        "Every 6 hours from 00:00 IST: one CCTNS V1 cycle "
        "(FIR, then court + accused details, then accused)"
    ),
    schedule=SCHEDULE_DAILY_CYCLE,
    start_date=START_DATE,
    catchup=False,
    max_active_runs=1,
    dagrun_timeout=CYCLE_DAGRUN_TIMEOUT,
    default_args=DEFAULT_ARGS,
    tags=[*TAGS_BASE, "daily-cycle", "fir", "court", "accused"],
    doc_md=DAG_DOC,
)
def cctns_v1_daily_cycle():
    @task(
        task_id="run_daily_cycle",
        execution_timeout=CYCLE_ATTEMPT_TIMEOUT,
        doc_md="Hold the cycle lock, run the four entities, write the marker only on full success.",
    )
    def run_daily_cycle_task() -> dict:
        from db.airflow_metadata import ensure_airflow_metadata
        from db.init_schema import ensure_schema
        from dags.pipeline_run import run_daily_cycle

        ensure_schema()
        ensure_airflow_metadata()
        result = run_daily_cycle()
        if result.get("status") != "succeeded":
            raise RuntimeError(f"V1 daily cycle failed: {result}")
        return result

    run_daily_cycle_task()


cctns_v1_daily_cycle()
