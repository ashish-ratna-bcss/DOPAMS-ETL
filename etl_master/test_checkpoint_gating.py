#!/usr/bin/env python3
"""Regression tests: master checkpoint must not advance on partial runs."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
MASTER_DIR = Path(__file__).resolve().parent
if str(MASTER_DIR) not in sys.path:
    sys.path.insert(0, str(MASTER_DIR))


class TestCheckpointGating(unittest.TestCase):
    def test_full_run_may_advance(self):
        import master_etl as mod
        self.assertTrue(mod.is_full_pipeline_run(None, None))

    def test_partial_start_order_must_not_advance(self):
        import master_etl as mod
        self.assertFalse(mod.is_full_pipeline_run(21, None))
        self.assertFalse(mod.is_full_pipeline_run(None, 23))
        self.assertFalse(mod.is_full_pipeline_run(21, 23))

    def test_partial_skips_persist_and_backfill(self):
        """Simulate end-of-main gating without running the pipeline."""
        import master_etl as mod

        persist = MagicMock()
        mark = MagicMock()
        is_complete = MagicMock(return_value=True)

        def end_of_run(start_order, end_order, to_date='2026-09-29'):
            if not mod.is_full_pipeline_run(start_order, end_order):
                return 'skipped'
            persist(to_date)
            if not is_complete():
                mark()
            return 'advanced'

        self.assertEqual(end_of_run(21, None), 'skipped')
        persist.assert_not_called()
        mark.assert_not_called()

        self.assertEqual(end_of_run(None, None), 'advanced')
        persist.assert_called_once_with('2026-09-29')
        # backfill already complete → mark not called
        mark.assert_not_called()


if __name__ == '__main__':
    unittest.main()
