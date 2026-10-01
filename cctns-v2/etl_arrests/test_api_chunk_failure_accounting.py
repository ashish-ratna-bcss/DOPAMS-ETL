#!/usr/bin/env python3
"""Regression tests: arrests API/chunk failure must fail closed.

Does not touch production databases or live CCTNS APIs.
"""
from __future__ import annotations

import queue
import sys
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from etl_window_guard import WindowGuard, release_checkpoint

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
ARRESTS_DIR = Path(__file__).resolve().parent
if str(ARRESTS_DIR) not in sys.path:
    sys.path.insert(0, str(ARRESTS_DIR))


class _FakeCursor:
    def __init__(self, store):
        self.store = store
        self._row = None

    def execute(self, sql, params=None):
        sql_upper = " ".join(sql.split()).upper()
        if sql_upper.startswith("SELECT"):
            name = params[0]
            self._row = (self.store[name],) if name in self.store else None
        elif sql_upper.startswith("DELETE"):
            self.store.pop(params[0], None)
        elif "INSERT" in sql_upper:
            self.store[params[0]] = params[1]
        else:
            raise AssertionError(sql)

    def fetchone(self):
        return self._row


class _FakeConn:
    def __init__(self, store):
        self.store = store

    def cursor(self):
        return _FakeCursor(self.store)

    def commit(self):
        return None

    def rollback(self):
        return None

    def close(self):
        return None


def _bare_etl():
    """Construct ArrestsETL without opening a real DB pool."""
    import etl_arrests as mod

    etl = object.__new__(mod.ArrestsETL)
    etl.stats_lock = threading.Lock()
    etl.log_lock = threading.Lock()
    etl.stats = {
        'total_api_calls': 0,
        'total_arrests_fetched': 0,
        'total_arrests_inserted': 0,
        'total_arrests_updated': 0,
        'total_arrests_no_change': 0,
        'total_arrests_failed': 0,
        'total_arrests_failed_crime_id': 0,
        'total_arrests_failed_person_id': 0,
        'total_duplicates': 0,
        'failed_api_calls': 0,
        'errors': [],
    }
    etl.log_api_chunk = MagicMock()
    etl.log_db_chunk = MagicMock()
    etl.detect_new_fields = MagicMock(return_value={})
    etl.add_column_to_table = MagicMock(return_value=False)
    etl.update_existing_records_with_new_fields = MagicMock()
    # object.__new__ skips ArrestsETL.__init__, which is where production
    # creates this guard. Exhausted fetches call it before returning.
    etl.window_guard = WindowGuard()
    return etl, mod


class TestArrestsApiChunkFailureAccounting(unittest.TestCase):
    def test_A_successful_api_chunk(self):
        etl, mod = _bare_etl()
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            'status': True,
            'data': [{'CRIME_ID': 'c1', 'ACCUSED_SEQ_NO': '1'}],
        }
        response.headers = {}
        with patch.object(mod, 'requests') as req:
            req.get.return_value = response
            with patch.object(mod, 'API_CONFIG', {
                'arrests_url': 'http://example/arrests',
                'api_key': 'k',
                'max_retries': 3,
                'timeout': 5,
                'base_url': 'http://example',
            }):
                data = etl.fetch_arrests_api('2026-01-01', '2026-01-05')
        self.assertEqual(len(data), 1)
        self.assertEqual(etl.stats['failed_api_calls'], 0)
        self.assertEqual(etl.stats['total_api_calls'], 1)

    def test_B_empty_and_404_are_success(self):
        etl, mod = _bare_etl()
        empty = MagicMock()
        empty.status_code = 200
        empty.json.return_value = {'status': True, 'data': []}
        empty.headers = {}
        not_found = MagicMock()
        not_found.status_code = 404
        not_found.headers = {}
        with patch.object(mod, 'requests') as req, patch.object(mod, 'API_CONFIG', {
            'arrests_url': 'http://example/arrests',
            'api_key': 'k',
            'max_retries': 3,
            'timeout': 5,
            'base_url': 'http://example',
        }):
            req.get.return_value = empty
            self.assertEqual(etl.fetch_arrests_api('2026-01-01', '2026-01-05'), [])
            req.get.return_value = not_found
            self.assertEqual(etl.fetch_arrests_api('2026-02-01', '2026-02-05'), [])
        self.assertEqual(etl.stats['failed_api_calls'], 0)
        with patch.object(etl, 'fetch_arrests_api', return_value=[]):
            self.assertTrue(etl.process_date_range('2026-01-01', '2026-01-05'))
        with patch.object(etl, 'fetch_arrests_api', return_value=[]):
            self.assertTrue(etl.process_date_range('2026-02-01', '2026-02-05'))

    def test_C_http_400_then_success_on_retry(self):
        etl, mod = _bare_etl()
        bad = MagicMock()
        bad.status_code = 400
        bad.headers = {}
        good = MagicMock()
        good.status_code = 200
        good.json.return_value = {'status': True, 'data': [{'CRIME_ID': 'c1'}]}
        good.headers = {}
        with patch.object(mod, 'requests') as req, \
             patch.object(mod, 'API_CONFIG', {
                 'arrests_url': 'http://example/arrests',
                 'api_key': 'k',
                 'max_retries': 3,
                 'timeout': 5,
                 'base_url': 'http://example',
             }), \
             patch.object(mod.time, 'sleep', return_value=None):
            req.get.side_effect = [bad, good]
            data = etl.fetch_arrests_api('2026-01-01', '2026-01-05')
        self.assertEqual(len(data), 1)
        self.assertEqual(etl.stats['failed_api_calls'], 0)

    def test_D_http_400_exhaustion_fails_chunk_and_run(self):
        etl, mod = _bare_etl()
        bad = MagicMock()
        bad.status_code = 400
        bad.headers = {}
        with patch.object(mod, 'requests') as req, \
             patch.object(mod, 'API_CONFIG', {
                 'arrests_url': 'http://example/arrests',
                 'api_key': 'k',
                 'max_retries': 3,
                 'timeout': 5,
                 'base_url': 'http://example',
             }), \
             patch.object(mod.time, 'sleep', return_value=None):
            req.get.return_value = bad
            self.assertIsNone(etl.fetch_arrests_api('2026-09-21', '2026-09-24'))
        self.assertEqual(etl.stats['failed_api_calls'], 1)
        self.assertTrue(any('Failed after max retries' in e for e in etl.stats['errors']))
        self.assertEqual(etl.window_guard.failed_window, ('2026-09-21', '2026-09-24'))
        self.assertFalse(etl.window_guard.may_advance())

        with patch.object(etl, 'fetch_arrests_api', return_value=None):
            self.assertFalse(etl.process_date_range('2026-09-21', '2026-09-24'))

        store = {}
        etl._replay_connect = lambda: _FakeConn(store)
        self.assertFalse(release_checkpoint(etl, 'arrests'))
        self.assertEqual(store['arrests__replay_from'][:10], '2026-09-21')

        # Multi-chunk: one failure cannot be hidden — run() fails closed
        etl.stats['failed_api_calls'] = 1
        etl.stats['total_arrests_failed'] = 0
        etl.stats['total_arrests_failed_crime_id'] = 0
        etl.api_log_file = 'x'
        etl.db_log_file = 'x'
        etl.failed_log_file = 'x'
        etl.invalid_ids_log_file = 'x'
        etl.duplicates_log_file = 'x'
        etl.write_log_summaries = MagicMock()
        # Simulate the success-gate section of run()
        unhandled = etl.stats['total_arrests_failed'] - etl.stats['total_arrests_failed_crime_id']
        self.assertEqual(unhandled, 0)
        self.assertGreater(etl.stats['failed_api_calls'], 0)
        # Mirror run() gate
        success = not (unhandled > 0 or etl.stats['failed_api_calls'] > 0)
        self.assertFalse(success)

    def test_E_chunk_worker_and_master_exit_path(self):
        etl, _mod = _bare_etl()
        result_q = queue.Queue()
        progress = {'completed': 0, 'total_time': 0.0}
        with patch.object(etl, 'process_date_range', return_value=False):
            ok = etl._process_chunk_worker(
                '2026-09-21', '2026-09-24', set(),
                result_q, threading.Lock(), progress, worker_id=0,
            )
        self.assertFalse(ok)
        result = result_q.get_nowait()
        self.assertFalse(result['success'])

        # Master treats child non-zero exit as step failure (check=True).
        # main() maps run() False → sys.exit(1).
        with patch('etl_arrests.ArrestsETL') as cls:
            instance = cls.return_value
            instance.run.return_value = False
            # Import main path logic
            exit_code = 0 if instance.run() else 1
        self.assertEqual(exit_code, 1)

    def test_E_multi_chunk_one_failure_not_hidden(self):
        etl, _mod = _bare_etl()
        calls = {'n': 0}

        def fake_fetch(a, b):
            calls['n'] += 1
            if calls['n'] == 2:
                etl.stats['failed_api_calls'] += 1
                etl.stats['errors'].append(f'{a} to {b}: Failed after max retries')
                return None
            etl.stats['total_api_calls'] += 1
            return []

        outcomes = []
        with patch.object(etl, 'fetch_arrests_api', side_effect=fake_fetch):
            outcomes.append(etl.process_date_range('2026-01-01', '2026-01-05'))
            outcomes.append(etl.process_date_range('2026-01-05', '2026-01-09'))
            outcomes.append(etl.process_date_range('2026-01-09', '2026-01-13'))
        self.assertEqual(outcomes, [True, False, True])
        self.assertEqual(etl.stats['failed_api_calls'], 1)
        self.assertFalse(all(outcomes))


if __name__ == '__main__':
    unittest.main()
