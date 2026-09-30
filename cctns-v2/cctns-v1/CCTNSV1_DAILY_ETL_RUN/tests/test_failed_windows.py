"""Minimal unit tests: fail-closed failed windows + ledger helpers."""
from __future__ import annotations

import os
import sys
import unittest
from datetime import date
from unittest.mock import MagicMock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from db.failed_windows import (  # noqa: E402
    failed_windows_for_run_log,
    normalize_failed_windows,
)
from dags.pipeline_run import (  # noqa: E402
    STATUS_EXTRACT_PARTIAL_FAILED,
    raise_if_task_failed,
)


class RaiseIfTaskFailedTests(unittest.TestCase):
    def test_loaded_ok(self):
        raise_if_task_failed("accused", {"status": "loaded", "failed_windows": 0})

    def test_not_loaded_pending_ok(self):
        raise_if_task_failed("court", {"status": "not_loaded_pending_key"})

    def test_extract_failed(self):
        with self.assertRaises(RuntimeError):
            raise_if_task_failed("fir", {"status": "extract_failed"})

    def test_load_failed(self):
        with self.assertRaises(RuntimeError):
            raise_if_task_failed("accused", {"status": "load_failed"})

    def test_extract_partial_failed(self):
        with self.assertRaises(RuntimeError):
            raise_if_task_failed(
                "accused",
                {"status": STATUS_EXTRACT_PARTIAL_FAILED, "failed_windows": 180},
            )


class NormalizeFailedWindowsTests(unittest.TestCase):
    def test_parse_string(self):
        raw = [
            "05-05-2002 to 05-05-2002: Error fetching data from Dao: ORA-06502: buffer",
        ]
        windows = normalize_failed_windows(raw)
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0]["window_start"], date(2002, 5, 5))
        self.assertEqual(windows[0]["window_end"], date(2002, 5, 5))
        self.assertIn("ORA-06502", windows[0]["error"])

    def test_parse_dict(self):
        windows = normalize_failed_windows(
            [
                {
                    "window_start": "01-01-2022",
                    "window_end": "07-01-2022",
                    "error": "timeout",
                }
            ]
        )
        self.assertEqual(windows[0]["window_start"], date(2022, 1, 1))
        self.assertEqual(windows[0]["window_end"], date(2022, 1, 7))
        snap = failed_windows_for_run_log(windows)
        self.assertEqual(snap[0]["window_start"], "01-01-2022")

    def test_empty(self):
        self.assertEqual(normalize_failed_windows(None), [])
        self.assertEqual(normalize_failed_windows([]), [])


class LedgerSqlSmokeTests(unittest.TestCase):
    """Cursor SQL shape for OPEN upsert / RESOLVE (no live DB)."""

    def test_record_open_executes_upsert(self):
        from db.failed_windows import record_open_failed_windows

        cur = MagicMock()
        windows = [
            {
                "window_start": date(2002, 5, 5),
                "window_end": date(2002, 5, 5),
                "error": "ORA-06502",
            }
        ]
        n = record_open_failed_windows(cur, "accused", "11111111-1111-1111-1111-111111111111", windows)
        self.assertEqual(n, 1)
        self.assertEqual(cur.execute.call_count, 1)
        sql = cur.execute.call_args[0][0]
        self.assertIn("ON CONFLICT", sql)
        self.assertIn("OPEN", sql)

    def test_resolve_all_when_none_failing(self):
        from db.failed_windows import resolve_windows_not_failing

        cur = MagicMock()
        cur.rowcount = 3
        n = resolve_windows_not_failing(
            cur, "accused", "11111111-1111-1111-1111-111111111111", []
        )
        self.assertEqual(n, 3)
        sql = cur.execute.call_args[0][0]
        self.assertIn("RESOLVED", sql)
        self.assertIn("status = 'OPEN'", sql)


if __name__ == "__main__":
    unittest.main()
