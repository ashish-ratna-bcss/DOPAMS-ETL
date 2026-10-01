"""Smoke tests for failure alert callback (no network)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dags import alerts  # noqa: E402


class NotifyTaskFailureTests(unittest.TestCase):
    def test_writes_failure_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "etl_failures.log"
            with patch.object(alerts, "_FAILURE_LOG", log_path), patch.object(
                alerts, "_webhook_url", return_value=""
            ):
                ti = MagicMock()
                ti.dag_id = "cctns_v1_daily_sync_fir_court_accused_details"
                ti.task_id = "sync_fir"
                ti.run_id = "manual__test"
                ti.try_number = 1
                ti.log_url = "http://example/log"
                alerts.notify_task_failure(
                    {
                        "task_instance": ti,
                        "exception": RuntimeError("boom"),
                        "dag": MagicMock(dag_id="cctns_v1_daily_sync_fir_court_accused_details"),
                        "dag_run": MagicMock(run_id="manual__test"),
                    }
                )
            lines = log_path.read_text().strip().splitlines()
            self.assertEqual(len(lines), 1)
            row = json.loads(lines[0])
            self.assertEqual(row["task_id"], "sync_fir")
            self.assertIn("boom", row["exception"])


if __name__ == "__main__":
    unittest.main()
