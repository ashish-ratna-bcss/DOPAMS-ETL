"""ETL-3 must not start on a partial V1 cycle or a failed V2 run.

Run from the repository root:
    python etl3/tests/test_daily_source_gate.py
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.source_readiness import IST, judge, parse_v2_last_run, v2_watermark_ready

NOW = datetime(2026, 10, 6, 6, 0, tzinfo=IST)
BOUNDARY = datetime(2026, 10, 6, 0, 30, tzinfo=IST)


def _rows(run_id="cycle-1"):
    cursor = BOUNDARY + timedelta(minutes=20)
    built = []
    for entity in ("fir", "court", "accused_details", "accused"):
        started = cursor
        cursor += timedelta(minutes=10)
        finished = cursor
        cursor += timedelta(minutes=1)
        built.append(
            {
                "entity": entity,
                "run_id": run_id,
                "status": "loaded_with_known_gaps" if entity == "accused" else "loaded",
                "started_at": started,
                "finished_at": finished,
            }
        )
    by_name = {row["entity"]: row for row in built}
    by_name["accused_details"]["started_at"] = by_name["court"]["started_at"]
    by_name["accused_details"]["finished_at"] = by_name["court"]["finished_at"]
    return list(by_name.values())


def _cycle(status="succeeded", run_id="cycle-1"):
    return {
        "run_id": run_id,
        "status": status,
        "started_at": BOUNDARY + timedelta(minutes=5),
        "finished_at": BOUNDARY + timedelta(hours=4),
        "cycle_start": BOUNDARY,
    }


class DailySourceGateTests(unittest.TestCase):
    def _judge(self, **overrides):
        snapshot = {
            "now": NOW,
            "commands": "",
            "cycle": _cycle(),
            "entities": _rows(),
            "v2_last_run": "2026-10-06",
            "etl3_prior_started_at": None,
        }
        snapshot.update(overrides)
        return judge(**snapshot)

    def test_complete_cycle_and_v2_watermark_runs_once(self):
        action, reason, boundary = self._judge()
        self.assertEqual(action, "run", reason)
        self.assertEqual(boundary, BOUNDARY)
        finished = _cycle()["finished_at"]
        action, reason, _boundary = self._judge(etl3_prior_started_at=finished + timedelta(minutes=1))
        self.assertEqual(action, "skip", reason)

    def test_partial_cycle_is_not_accepted(self):
        entities = [row for row in _rows() if row["entity"] != "accused"]
        action, reason, _boundary = self._judge(entities=entities)
        self.assertEqual(action, "wait", reason)
        self.assertIn("accused", reason)

    def test_mixed_cycle_successes_are_not_accepted(self):
        entities = _rows()
        for row in entities:
            if row["entity"] == "court":
                row["run_id"] = "other-cycle"
        action, _reason, _boundary = self._judge(entities=entities)
        self.assertEqual(action, "wait")

    def test_failed_marker_and_wrong_order_are_not_accepted(self):
        action, _reason, _boundary = self._judge(cycle=_cycle(status="failed"))
        self.assertEqual(action, "wait")
        entities = _rows()
        for row in entities:
            if row["entity"] == "accused":
                row["started_at"] = BOUNDARY
        action, _reason, _boundary = self._judge(entities=entities)
        self.assertEqual(action, "wait")

    def test_failed_v2_watermark_does_not_release_the_gate(self):
        action, reason, _boundary = self._judge(v2_last_run="2026-10-05")
        self.assertEqual(action, "wait", reason)
        self.assertIn("watermark", reason)
        action, reason, _boundary = self._judge(v2_last_run=None)
        self.assertEqual(action, "wait", reason)

    def test_running_v2_does_not_release_the_gate_even_with_todays_watermark(self):
        action, reason, _boundary = self._judge(
            commands="python3 /data/etl_master/master_etl.py --pure-cctns\n"
        )
        self.assertEqual(action, "wait", reason)
        self.assertIn("master_etl", reason)

    def test_media_process_does_not_block_a_finished_data_cycle(self):
        action, _reason, _boundary = self._judge(
            commands="airflow tasks run cctns_v1_daily_sync_media_attachments sync_media\n"
        )
        self.assertEqual(action, "run")

    def test_watermark_must_be_a_date_on_or_after_the_cycle(self):
        ok, _reason = v2_watermark_ready("2026-10-06", BOUNDARY)
        self.assertTrue(ok)
        ok, _reason = v2_watermark_ready('"2026-10-06"', BOUNDARY)
        self.assertTrue(ok)
        ok, _reason = v2_watermark_ready("2026-10-04", BOUNDARY)
        self.assertFalse(ok)
        self.assertIsNone(parse_v2_last_run("  "))

    def test_etl3_from_an_older_cycle_does_not_count_as_this_cycle(self):
        finished = _cycle()["finished_at"]
        action, _reason, _boundary = self._judge(
            etl3_prior_started_at=finished - timedelta(days=1)
        )
        self.assertEqual(action, "run")

    def test_later_success_is_used_and_is_not_mixed_with_the_earlier_cycle(self):
        later_start = datetime(2026, 10, 6, 6, 40, tzinfo=IST)
        entities = _rows(run_id="afternoon")
        base = min(row["started_at"] for row in entities)
        shift = later_start - base
        for row in entities:
            row["started_at"] = row["started_at"] + shift
            row["finished_at"] = row["finished_at"] + shift
        cycle = _cycle(run_id="afternoon")
        cycle["started_at"] = later_start
        cycle["finished_at"] = later_start + timedelta(hours=1)
        cycle["cycle_start"] = datetime(2026, 10, 6, 6, 30, tzinfo=IST)
        now = datetime(2026, 10, 6, 9, 0, tzinfo=IST)
        action, reason, slot = self._judge(now=now, cycle=cycle, entities=entities)
        self.assertEqual(action, "run", reason)
        self.assertEqual(slot, datetime(2026, 10, 6, 6, 30, tzinfo=IST))

        mixed = [dict(row) for row in entities]
        mixed[0]["run_id"] = "morning"
        action, _reason, _slot = self._judge(now=now, cycle=cycle, entities=mixed)
        self.assertEqual(action, "wait")

    def test_long_previous_slot_still_counts_and_two_slots_ago_does_not(self):
        now = datetime(2026, 10, 6, 8, 0, tzinfo=IST)
        started = datetime(2026, 10, 6, 0, 40, tzinfo=IST)
        entities = _rows()
        cycle = _cycle()
        cycle["started_at"] = started
        cycle["finished_at"] = datetime(2026, 10, 6, 8, 10, tzinfo=IST)
        cycle["cycle_start"] = datetime(2026, 10, 6, 0, 30, tzinfo=IST)
        action, reason, _slot = self._judge(now=now, cycle=cycle, entities=entities)
        self.assertEqual(action, "run", reason)

        stale = _cycle()
        stale["started_at"] = datetime(2026, 10, 5, 12, 40, tzinfo=IST)
        stale["cycle_start"] = datetime(2026, 10, 5, 12, 30, tzinfo=IST)
        stale["finished_at"] = datetime(2026, 10, 5, 16, 0, tzinfo=IST)
        action, _reason, _slot = self._judge(now=now, cycle=stale, entities=_rows())
        self.assertEqual(action, "wait")

    def test_newer_failed_cycle_is_not_replaced_by_an_older_success(self):
        failed = _cycle(status="failed", run_id="afternoon")
        failed["started_at"] = datetime(2026, 10, 6, 6, 40, tzinfo=IST)
        failed["cycle_start"] = datetime(2026, 10, 6, 6, 30, tzinfo=IST)
        action, _reason, _slot = self._judge(
            now=datetime(2026, 10, 6, 9, 0, tzinfo=IST),
            cycle=failed,
            entities=_rows(run_id="morning"),
        )
        self.assertEqual(action, "wait")


if __name__ == "__main__":
    unittest.main()
