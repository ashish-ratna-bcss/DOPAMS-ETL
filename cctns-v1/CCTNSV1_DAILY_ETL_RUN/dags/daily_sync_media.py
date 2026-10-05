"""
Daily CCTNS V1 Media Sync: downloads new FIR & Court document attachments from Alfresco.

Airflow DAG id: cctns_v1_daily_sync_media_attachments
Schedule: 05:00 IST daily (30 23 * * * UTC)

Graph:
    bootstrap_database → sync_media_attachments
"""
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from airflow.decorators import dag, task

from dags.cctnsv1_dag_common import (
    DAG_ID_MEDIA_ATTACHMENTS,
    DEFAULT_ARGS,
    SCHEDULE_MEDIA_ATTACHMENTS,
    START_DATE,
    TAGS_BASE,
)

DAG_DOC = """
## CCTNS V1 — daily sync: Media Attachments (**05:00 IST**)

Discovers and downloads document attachments from Alfresco into the server disk:
`/home/tganb/dopams/media_cctnsv1/<attach_path>/<dms_file_name>`

| Task | Description |
|------|-------------|
| `bootstrap_database` | Ensure schema and `cctns_media_files` tracking table exist |
| `sync_media_attachments` | Scan FIR & Court tables, download missing PDFs concurrently |
"""


@dag(
    dag_id=DAG_ID_MEDIA_ATTACHMENTS,
    description=(
        "Daily 05:00 IST: download and sync CCTNS FIR/Court media attachments from Alfresco"
    ),

    schedule=SCHEDULE_MEDIA_ATTACHMENTS,
    start_date=START_DATE,
    catchup=False,
    max_active_runs=1,
    dagrun_timeout=timedelta(hours=3),
    default_args=DEFAULT_ARGS,
    tags=[*TAGS_BASE, "media", "alfresco", "attachments", "pdf"],
    doc_md=DAG_DOC,
)
def cctns_v1_daily_sync_media_attachments():
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
        task_id="sync_media_attachments",
        execution_timeout=timedelta(hours=2),
        doc_md="Scan DB for new attach_path/dms_file_name and download from Alfresco.",
    )
    def sync_media_attachments() -> dict:
        from sync_media import run_media_sync

        result = run_media_sync()
        if result.get("failed", 0) > 0 and result.get("downloaded", 0) == 0 and result.get("total_processed", 0) > 0:
            raise RuntimeError(f"All media downloads failed in run: {result}")
        return result

    boot = bootstrap_database()
    media = sync_media_attachments()

    boot >> media


cctns_v1_daily_sync_media_attachments()
