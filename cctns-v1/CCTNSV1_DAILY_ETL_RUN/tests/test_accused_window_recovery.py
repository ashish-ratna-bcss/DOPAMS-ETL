"""Bounded whole-window retry, failure classification, and unresolved-window
handling for the CCTNS V1 Accused date-range extraction.

Covers (see task): all-success, transient-then-recover (single and multiple
failures), retry exhaustion, one unresolved window among many successful,
known ORA-06502 gap vs permanent HTTP error classification, no duplicate
records across retry attempts, entity-level status classification, and that
successfully-fetched records are still loaded when some windows remain
unresolved.

Run from CCTNSV1_DAILY_ETL_RUN:
    python -m unittest tests.test_accused_window_recovery
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import date
from unittest import mock
from unittest.mock import MagicMock

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from apis import client as client_mod  # noqa: E402
from apis.client import (  # noqa: E402
    KIND_PERMANENT,
    KIND_TRANSIENT,
    fetch_full_range_chunked,
)
from dags.pipeline_run import (  # noqa: E402
    STATUS_LOADED_WITH_KNOWN_GAPS,
    STATUS_LOADED_WITH_UNRESOLVED_GAPS,
    _classify_load_status,
    raise_if_task_failed,
)
from db.cycle_rules import DONE_STATUSES  # noqa: E402


def _http_response(json_body):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = json_body
    return resp


def _ok_payload(n=1):
    return {"message": "Success", "data": [{"id": i} for i in range(n)]}


class WindowClassificationTests(unittest.TestCase):
    """Scenarios 6, 7: known ORA gap vs permanent HTTP error are classified
    correctly and never get an extra whole-window retry."""

    @mock.patch("apis.client.requests.request")
    def test_known_ora_gap_single_day_not_retried(self, mock_request):
        mock_request.return_value = _http_response(
            {"message": "Error: ORA-06502: PL/SQL numeric or value error", "data": []}
        )
        records, failed, attempt_log = client_mod._fetch_window_with_recovery(
            "POST", "http://x", {}, date(2002, 5, 5), date(2002, 5, 5)
        )
        self.assertEqual(records, [])
        self.assertIn("ORA-06502", failed)
        self.assertEqual(len(attempt_log), 1)

    @mock.patch("apis.client.time.sleep")
    @mock.patch("apis.client.requests.request")
    def test_permanent_http_400_not_retried(self, mock_request, mock_sleep):
        resp = MagicMock()
        resp.raise_for_status.side_effect = requests.exceptions.HTTPError(
            response=MagicMock(status_code=400)
        )
        mock_request.return_value = resp
        records, failed, attempt_log = client_mod._fetch_window_with_recovery(
            "POST", "http://x", {}, date(2022, 1, 1), date(2022, 1, 7)
        )
        self.assertEqual(records, [])
        self.assertIsNotNone(failed)
        self.assertEqual(len(attempt_log), 1)
        self.assertIn(KIND_PERMANENT, attempt_log[0][1])


class WindowRetryTests(unittest.TestCase):
    """Scenarios 2, 3, 4, 12: bounded whole-window retry with backoff for
    transient failures, exhaustion, and no duplicate records."""

    @mock.patch("apis.client.time.sleep")
    @mock.patch("apis.client.requests.request")
    def test_one_timeout_then_success(self, mock_request, mock_sleep):
        mock_request.side_effect = [
            requests.exceptions.ConnectTimeout("boom"),
            requests.exceptions.ConnectTimeout("boom"),
            requests.exceptions.ConnectTimeout("boom"),  # exhausts the 3 HTTP-level attempts
            _http_response(_ok_payload(5)),  # window-level retry #1 succeeds
        ]
        records, failed, attempt_log = client_mod._fetch_window_with_recovery(
            "POST", "http://x", {}, date(2022, 1, 1), date(2022, 1, 7)
        )
        self.assertIsNone(failed)
        self.assertEqual(len(records), 5)
        self.assertEqual(len(attempt_log), 2)
        self.assertEqual(attempt_log[1][1], "success")
        mock_sleep.assert_called()

    @mock.patch("apis.client.time.sleep")
    @mock.patch("apis.client.requests.request")
    def test_multiple_timeouts_then_success(self, mock_request, mock_sleep):
        timeout_batch = [requests.exceptions.ConnectTimeout("boom")] * 3
        mock_request.side_effect = (
            timeout_batch + timeout_batch + [_http_response(_ok_payload(2))]
        )
        records, failed, attempt_log = client_mod._fetch_window_with_recovery(
            "POST", "http://x", {}, date(2022, 1, 1), date(2022, 1, 7)
        )
        self.assertIsNone(failed)
        self.assertEqual(len(records), 2)
        self.assertEqual(len(attempt_log), 3)

    @mock.patch("apis.client.time.sleep")
    @mock.patch("apis.client.requests.request")
    def test_retry_exhaustion_reports_unresolved(self, mock_request, mock_sleep):
        mock_request.side_effect = requests.exceptions.ConnectTimeout("boom")
        records, failed, attempt_log = client_mod._fetch_window_with_recovery(
            "POST", "http://x", {}, date(2022, 1, 1), date(2022, 1, 7)
        )
        self.assertEqual(records, [])
        self.assertIsNotNone(failed)
        self.assertEqual(len(attempt_log), client_mod.WINDOW_MAX_RETRIES + 1)
        self.assertTrue(all(KIND_TRANSIENT in outcome for _, outcome in attempt_log))

    @mock.patch("apis.client.time.sleep")
    @mock.patch("apis.client.requests.request")
    def test_no_duplicate_records_across_retry_attempts(self, mock_request, mock_sleep):
        mock_request.side_effect = [
            requests.exceptions.ConnectTimeout("boom"),
            requests.exceptions.ConnectTimeout("boom"),
            requests.exceptions.ConnectTimeout("boom"),
            _http_response(_ok_payload(3)),
        ]
        records, failed, _ = client_mod._fetch_window_with_recovery(
            "POST", "http://x", {}, date(2022, 1, 1), date(2022, 1, 7)
        )
        self.assertEqual(len(records), 3)  # not 6 -- the failed attempt's (empty) records are not kept


class FullRangeChunkedIntegrationTests(unittest.TestCase):
    """Scenarios 1, 5: one unresolved window among many successful ones."""

    @mock.patch("apis.client.time.sleep")
    @mock.patch("apis.client.requests.request")
    def test_all_windows_successful(self, mock_request, mock_sleep):
        mock_request.return_value = _http_response(_ok_payload(1))
        records, failed = fetch_full_range_chunked(
            "POST", "http://x", {}, date(2022, 1, 1), date(2022, 1, 14)
        )
        self.assertEqual(failed, [])
        self.assertEqual(len(records), 2)  # 2 windows, 1 record each

    @mock.patch("apis.client.time.sleep")
    @mock.patch("apis.client.requests.request")
    def test_one_unresolved_window_among_many_successful(self, mock_request, mock_sleep):
        exhaust = [requests.exceptions.ConnectTimeout("boom")] * (
            (client_mod.WINDOW_MAX_RETRIES + 1) * 3
        )
        mock_request.side_effect = (
            [_http_response(_ok_payload(1))]  # window 1 (01-07): success
            + exhaust  # window 2 (08-14): exhausts every retry
            + [_http_response(_ok_payload(1))]  # window 3 (15-21): success
        )
        records, failed = fetch_full_range_chunked(
            "POST", "http://x", {}, date(2022, 1, 1), date(2022, 1, 21)
        )
        self.assertEqual(len(records), 2)  # windows 1 and 3 preserved
        self.assertEqual(len(failed), 1)  # only window 2 reported
        self.assertIn("08-01-2022 to 14-01-2022", failed[0])


class LoadStatusClassificationTests(unittest.TestCase):
    """Scenario 11: entity-level status reflects unresolved vs known-gap vs clean."""

    def test_clean_load(self):
        self.assertEqual(_classify_load_status([], []), "loaded")

    def test_known_gaps_only(self):
        self.assertEqual(_classify_load_status([{"error": "ORA"}], []), STATUS_LOADED_WITH_KNOWN_GAPS)

    def test_unresolved_gaps_take_priority(self):
        status = _classify_load_status([{"error": "ORA"}], [{"error": "timeout"}])
        self.assertEqual(status, STATUS_LOADED_WITH_UNRESOLVED_GAPS)

    def test_unresolved_gaps_alone(self):
        self.assertEqual(_classify_load_status([], [{"error": "timeout"}]), STATUS_LOADED_WITH_UNRESOLVED_GAPS)

    def test_unresolved_not_a_done_status(self):
        # The cycle must not succeed while unresolved windows remain --
        # orchestrate_cycle.py's _ok() relies on this membership check.
        self.assertNotIn(STATUS_LOADED_WITH_UNRESOLVED_GAPS, DONE_STATUSES)
        self.assertIn(STATUS_LOADED_WITH_KNOWN_GAPS, DONE_STATUSES)

    def test_raise_if_task_failed_raises_on_unresolved(self):
        with self.assertRaises(RuntimeError):
            raise_if_task_failed(
                "accused",
                {"status": STATUS_LOADED_WITH_UNRESOLVED_GAPS, "unresolved_gaps": 3},
            )


class UnresolvedWindowPreservesGoodDataTests(unittest.TestCase):
    """Scenario 5 (the core fix): _run_one_entity must still upsert the
    successfully-fetched records even when some windows remain unresolved."""

    def _run(self, failed_windows_raw):
        import dags.pipeline_run as pr

        conn = MagicMock()
        with mock.patch.object(pr, "abandon_stale_running", return_value=0), \
                mock.patch.object(pr, "start_entity_run", return_value="log-1"), \
                mock.patch.object(pr, "finish_entity_run") as mock_finish, \
                mock.patch.object(pr, "dedupe_batch", side_effect=lambda entity, recs: (recs, 0)), \
                mock.patch.object(pr, "filter_orphan_fir", side_effect=lambda entity, recs, c: (recs, 0)), \
                mock.patch.object(pr, "upsert_records") as mock_upsert:
            mock_upsert.return_value = {"inserted": 2, "updated": 0, "unchanged": 0}
            cfg = {
                "fetch": lambda: ([{"id": 1}, {"id": 2}], failed_windows_raw),
                "table": "cctns_accused",
                "conflict_col": "natural_key",
                "upsert_ready": True,
            }
            result = pr._run_one_entity("accused", cfg, "run-1", conn)
        return result, mock_upsert, mock_finish

    def test_unresolved_window_does_not_skip_upsert(self):
        failed = ["01-01-2022 to 07-01-2022: ConnectTimeout"]
        result, mock_upsert, mock_finish = self._run(failed)
        mock_upsert.assert_called_once()
        self.assertEqual(result["status"], STATUS_LOADED_WITH_UNRESOLVED_GAPS)
        self.assertEqual(result["unresolved_gaps"], 1)
        self.assertEqual(mock_finish.call_args.kwargs["status"], STATUS_LOADED_WITH_UNRESOLVED_GAPS)

    def test_known_ora_gap_still_loads_as_before(self):
        failed = ["05-05-2002 to 05-05-2002: ORA-06502 buffer"]
        result, mock_upsert, _ = self._run(failed)
        mock_upsert.assert_called_once()
        self.assertEqual(result["status"], STATUS_LOADED_WITH_KNOWN_GAPS)

    def test_no_failures_loads_cleanly(self):
        result, mock_upsert, _ = self._run([])
        mock_upsert.assert_called_once()
        self.assertEqual(result["status"], "loaded")


class ExtractInterruptionTests(unittest.TestCase):
    """Scenario 9: an unexpected exception during extraction must still be
    fail-closed -- no upsert, no silent success -- same as before this fix."""

    def test_unexpected_exception_during_fetch_is_fail_closed(self):
        import dags.pipeline_run as pr

        conn = MagicMock()
        with mock.patch.object(pr, "abandon_stale_running", return_value=0), \
                mock.patch.object(pr, "start_entity_run", return_value="log-1"), \
                mock.patch.object(pr, "finish_entity_run") as mock_finish, \
                mock.patch.object(pr, "upsert_records") as mock_upsert:
            cfg = {
                "fetch": mock.Mock(side_effect=ConnectionError("process interrupted")),
                "table": "cctns_accused",
                "conflict_col": "natural_key",
                "upsert_ready": True,
            }
            result = pr._run_one_entity("accused", cfg, "run-1", conn)
        self.assertEqual(result["status"], "extract_failed")
        mock_upsert.assert_not_called()
        self.assertEqual(mock_finish.call_args.kwargs["status"], "extract_failed")
        conn.rollback.assert_called_once()


class LedgerReflectsFullFailureSetTests(unittest.TestCase):
    """Scenario 10: a window that was previously OPEN and now succeeds must
    be eligible for resolution -- the ledger sync must see the complete,
    current failure set (known + unresolved), not a subset, regardless of
    which branch the rest of the function takes."""

    def test_ledger_sync_receives_full_failed_window_set(self):
        import dags.pipeline_run as pr

        conn = MagicMock()
        failed = [
            "05-05-2002 to 05-05-2002: ORA-06502 buffer",
            "01-01-2022 to 07-01-2022: ConnectTimeout",
        ]
        with mock.patch.object(pr, "abandon_stale_running", return_value=0), \
                mock.patch.object(pr, "start_entity_run", return_value="log-1"), \
                mock.patch.object(pr, "finish_entity_run"), \
                mock.patch.object(pr, "dedupe_batch", side_effect=lambda entity, recs: (recs, 0)), \
                mock.patch.object(pr, "filter_orphan_fir", side_effect=lambda entity, recs, c: (recs, 0)), \
                mock.patch.object(pr, "upsert_records", return_value={"inserted": 0, "updated": 0, "unchanged": 0}), \
                mock.patch.object(pr, "_sync_failed_window_ledger") as mock_sync:
            cfg = {
                "fetch": lambda: ([{"id": 1}], failed),
                "table": "cctns_accused",
                "conflict_col": "natural_key",
                "upsert_ready": True,
            }
            pr._run_one_entity("accused", cfg, "run-1", conn)
        mock_sync.assert_called_once()
        synced_windows = mock_sync.call_args[0][3]
        self.assertEqual(len(synced_windows), 2)  # both the ORA gap and the unresolved window


if __name__ == "__main__":
    unittest.main()
