"""Airflow task failure alerts: durable local log + optional webhook."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, request

logger = logging.getLogger("cctns_v1_etl.alerts")

_ETL_ROOT = Path(__file__).resolve().parent.parent
_FAILURE_LOG = _ETL_ROOT / "logs" / "etl_failures.log"


def _webhook_url() -> str:
    return (os.environ.get("CCTNS_ALERT_WEBHOOK_URL") or "").strip()


def _append_failure_log(payload: dict[str, Any]) -> None:
    try:
        _FAILURE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _FAILURE_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, default=str) + "\n")
    except OSError as err:
        logger.error("Could not write failure log %s: %s", _FAILURE_LOG, err)


def _post_webhook(payload: dict[str, Any]) -> None:
    url = _webhook_url()
    if not url:
        return
    body = json.dumps(
        {
            "text": (
                f"[CCTNS V1 ETL] FAIL dag={payload.get('dag_id')} "
                f"task={payload.get('task_id')} run={payload.get('run_id')}: "
                f"{payload.get('exception')}"
            ),
            **payload,
        },
        default=str,
    ).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=10) as resp:
            resp.read()
    except (error.URLError, TimeoutError, OSError) as err:
        logger.error("Alert webhook POST failed: %s", err)


def notify_task_failure(context: dict[str, Any]) -> None:
    """Airflow on_failure_callback — always logs; webhooks if configured."""
    ti = context.get("task_instance")
    exception = context.get("exception")
    dag = context.get("dag")
    dag_run = context.get("dag_run")
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "dag_id": getattr(dag, "dag_id", None) or getattr(ti, "dag_id", None),
        "task_id": getattr(ti, "task_id", None),
        "run_id": getattr(dag_run, "run_id", None) or getattr(ti, "run_id", None),
        "try_number": getattr(ti, "try_number", None),
        "log_url": getattr(ti, "log_url", None),
        "exception": str(exception) if exception is not None else None,
    }
    logger.error(
        "TASK FAILURE ALERT dag=%s task=%s run=%s try=%s err=%s",
        payload["dag_id"],
        payload["task_id"],
        payload["run_id"],
        payload["try_number"],
        payload["exception"],
    )
    _append_failure_log(payload)
    _post_webhook(payload)
