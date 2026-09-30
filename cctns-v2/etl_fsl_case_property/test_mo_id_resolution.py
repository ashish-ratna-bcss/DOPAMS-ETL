#!/usr/bin/env python3
"""Regression tests: FSL MO_ID ObjectId → mo_seizure_id → mo_id label remap.

Does not modify production data.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
FSL_DIR = Path(__file__).resolve().parent
if str(FSL_DIR) not in sys.path:
    sys.path.insert(0, str(FSL_DIR))


def _bare_fsl():
    import etl_fsl_case_property as mod

    etl = object.__new__(mod.FSLCasePropertyETL)
    etl.db_conn = MagicMock()
    etl.db_cursor = MagicMock()
    etl.stats = {
        'total_records_failed': 0,
        'total_records_failed_crime_id': 0,
        'total_records_failed_mo_id': 0,
        'errors': [],
    }
    return etl, mod


class TestFslMoIdResolution(unittest.TestCase):
    def test_A_objectid_remaps_to_parent_mo_id_label(self):
        etl, _mod = _bare_fsl()
        # First query (mo_id label) → miss; second (mo_seizure_id) → hit MO7
        etl.db_cursor.fetchone.side_effect = [None, ('MO7',)]
        resolved, status = etl.resolve_mo_reference(
            'crime-1', '64a79ae8986b641e4291c3d6', etl.db_cursor
        )
        self.assertEqual(status, 'mo_seizure_id_remap')
        self.assertEqual(resolved, 'MO7')
        # Confirm second SQL used mo_seizure_id
        sqls = [c.args[0] for c in etl.db_cursor.execute.call_args_list]
        self.assertTrue(any('mo_seizure_id' in s for s in sqls))
        self.assertTrue(any('mo_id = %s' in s for s in sqls))

    def test_B_existing_mo_id_label_preserved(self):
        etl, _mod = _bare_fsl()
        etl.db_cursor.fetchone.side_effect = [('MO1',)]
        resolved, status = etl.resolve_mo_reference('crime-1', 'MO1', etl.db_cursor)
        self.assertEqual(status, 'mo_id_label')
        self.assertEqual(resolved, 'MO1')
        # Only one query needed
        self.assertEqual(etl.db_cursor.execute.call_count, 1)

    def test_C_true_orphan_unresolved(self):
        etl, _mod = _bare_fsl()
        etl.db_cursor.fetchone.side_effect = [None, None]
        resolved, status = etl.resolve_mo_reference(
            'crime-1', 'deadbeefdeadbeefdeadbeef', etl.db_cursor
        )
        self.assertEqual(status, 'unresolved')
        self.assertEqual(resolved, 'deadbeefdeadbeefdeadbeef')

    def test_empty_mo_id_allowed(self):
        etl, _mod = _bare_fsl()
        resolved, status = etl.resolve_mo_reference('crime-1', None, etl.db_cursor)
        self.assertEqual(status, 'empty')
        self.assertIsNone(resolved)

    def test_retry_remaps_before_insert(self):
        etl, _mod = _bare_fsl()
        record = {
            'crime_id': None,
            '_original_crime_id': 'crime-1',
            'mo_id': '64a79ae8986b641e4291c3d6',
            'case_property_id': 'cp-1',
        }
        conn = MagicMock()
        cur = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cur
        # crime exists; resolve: miss label, hit seizure_id → MO7
        cur.fetchone.side_effect = [('crime-1',), None, ('MO7',)]
        etl.insert_fsl_case_property = MagicMock(return_value=(True, 'inserted'))

        ok = etl._retry_fsl_case_property_record(conn, record)
        self.assertTrue(ok)
        self.assertEqual(record['mo_id'], 'MO7')
        etl.insert_fsl_case_property.assert_called_once()

    def test_retry_orphan_stays_queued(self):
        etl, _mod = _bare_fsl()
        record = {
            '_original_crime_id': 'crime-1',
            'mo_id': 'orphan-id',
            'case_property_id': 'cp-1',
        }
        conn = MagicMock()
        cur = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cur
        cur.fetchone.side_effect = [('crime-1',), None, None]  # crime ok, both MO lookups miss
        etl.insert_fsl_case_property = MagicMock()

        ok = etl._retry_fsl_case_property_record(conn, record)
        self.assertFalse(ok)
        etl.insert_fsl_case_property.assert_not_called()
        self.assertEqual(record['mo_id'], 'orphan-id')


if __name__ == '__main__':
    unittest.main()
