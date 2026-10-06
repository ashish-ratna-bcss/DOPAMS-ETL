"""ETL-3 starts only when the same 6-hour slot succeeded on both V1 and V2.

Run from the repository root:
    python etl3/tests/test_daily_source_gate.py
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.source_readiness import (
    IST,
    discover_v2_runs,
    judge,
    parse_v2_last_run,
    v2_watermark_ready,
)

SLOT = datetime(2026, 10, 6, 5, 30, tzinfo=IST)
NOW = datetime(2026, 10, 6, 9, 0, tzinfo=IST)


def _rows(run_id="cycle-1", slot=SLOT):
    cursor = slot + timedelta(minutes=20)
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


def _cycle(status="succeeded", run_id="cycle-1", slot=SLOT, started_minute=5, hours=3):
    return {
        "run_id": run_id,
        "status": status,
        "started_at": slot + timedelta(minutes=started_minute),
        "finished_at": slot + timedelta(hours=hours),
        "cycle_start": slot,
    }


def _v2(slot=SLOT, succeeded=True, minute=2):
    return {"started_at": slot + timedelta(minutes=minute), "succeeded": succeeded}


class DailySourceGateTests(unittest.TestCase):
    def _judge(self, **overrides):
        cycle = overrides.pop("cycle", _cycle())
        entities = overrides.pop("entities", _rows(run_id=cycle["run_id"]) if cycle else [])
        if "cycles" not in overrides:
            overrides["cycles"] = [cycle] if cycle else []
        if "entities_by_run_id" not in overrides:
            overrides["entities_by_run_id"] = (
                {str(cycle["run_id"]): entities} if cycle else {}
            )
        snapshot = {
            "now": NOW,
            "commands": "",
            "v2_runs": [_v2()],
            "v2_last_run": "2026-10-06",
            "etl3_successes": [],
        }
        snapshot.update(overrides)
        return judge(**snapshot)

    def test_same_slot_runs_once_with_no_extra_delay(self):
        action, reason, slot = self._judge()
        self.assertEqual(action, "run", reason)
        self.assertEqual(slot, SLOT)
        finished = _cycle()["finished_at"]
        action, reason, _slot = self._judge(etl3_successes=[finished + timedelta(minutes=1)])
        self.assertEqual(action, "skip", reason)

        # V1 done at 09:00, V2 done at 10:14. Checker at 10:15 starts now.
        six = datetime(2026, 10, 6, 11, 30, tzinfo=IST)
        cycle = _cycle(slot=six, run_id="six", hours=3)
        self.assertEqual(cycle["finished_at"], datetime(2026, 10, 6, 14, 30, tzinfo=IST))
        action, reason, slot = self._judge(
            now=datetime(2026, 10, 6, 15, 45, tzinfo=IST),
            cycle=cycle,
            entities=_rows(run_id="six", slot=six),
            v2_runs=[_v2(slot=six, minute=10)],
            etl3_successes=[SLOT + timedelta(hours=2)],
        )
        self.assertEqual(action, "run", reason)
        self.assertEqual(slot, six)

    def test_v1_finishing_first_waits_for_v2_and_the_reverse(self):
        six = datetime(2026, 10, 6, 11, 30, tzinfo=IST)
        cycle = _cycle(slot=six, run_id="six", hours=3)
        rows = _rows(run_id="six", slot=six)
        action, reason, _slot = self._judge(
            now=datetime(2026, 10, 6, 14, 45, tzinfo=IST),
            cycle=cycle,
            entities=rows,
            v2_runs=[_v2(slot=six, succeeded=False)],
            etl3_successes=[SLOT + timedelta(hours=1)],
        )
        self.assertEqual(action, "wait", reason)
        self.assertIn("V2", reason)

        action, reason, slot = self._judge(
            now=datetime(2026, 10, 6, 15, 45, tzinfo=IST),
            cycle=cycle,
            entities=rows,
            v2_runs=[_v2(slot=six)],
            etl3_successes=[SLOT + timedelta(hours=1)],
        )
        self.assertEqual(action, "run", reason)
        self.assertEqual(slot, six)

        late_v1 = _cycle(slot=six, run_id="six", hours=5)
        action, _reason, _slot = self._judge(
            now=datetime(2026, 10, 6, 15, 0, tzinfo=IST),
            cycle=_cycle(slot=six, run_id="six", status="running", hours=5),
            entities=rows,
            v2_runs=[_v2(slot=six)],
            etl3_successes=[SLOT + timedelta(hours=1)],
        )
        self.assertEqual(action, "wait")
        action, reason, slot = self._judge(
            now=datetime(2026, 10, 6, 16, 40, tzinfo=IST),
            cycle=late_v1,
            entities=rows,
            v2_runs=[_v2(slot=six)],
            etl3_successes=[SLOT + timedelta(hours=1)],
        )
        self.assertEqual(action, "run", reason)
        self.assertEqual(slot, six)

    def test_partial_cycle_is_not_accepted(self):
        entities = [row for row in _rows() if row["entity"] != "accused"]
        action, reason, _slot = self._judge(entities=entities)
        self.assertEqual(action, "wait", reason)
        self.assertIn("accused", reason)

    def test_mixed_v1_entities_and_mixed_v1_v2_slots_are_rejected(self):
        entities = _rows()
        for row in entities:
            if row["entity"] == "court":
                row["run_id"] = "other-cycle"
        action, _reason, _slot = self._judge(entities=entities)
        self.assertEqual(action, "wait")

        six = datetime(2026, 10, 6, 11, 30, tzinfo=IST)
        action, reason, _slot = self._judge(
            now=datetime(2026, 10, 6, 15, 0, tzinfo=IST),
            cycle=_cycle(slot=six, run_id="six"),
            entities=_rows(run_id="six", slot=six),
            v2_runs=[_v2(slot=SLOT)],
            etl3_successes=[SLOT + timedelta(hours=2)],
        )
        self.assertEqual(action, "wait", reason)
        self.assertNotIn("both succeeded", reason)

    def test_failed_marker_and_wrong_order_are_not_accepted(self):
        action, _reason, _slot = self._judge(cycle=_cycle(status="failed"))
        self.assertEqual(action, "wait")
        entities = _rows()
        for row in entities:
            if row["entity"] == "accused":
                row["started_at"] = SLOT
        action, _reason, _slot = self._judge(entities=entities)
        self.assertEqual(action, "wait")

    def test_v1_success_with_v2_failure_and_the_reverse(self):
        action, reason, _slot = self._judge(v2_runs=[_v2(succeeded=False)])
        self.assertEqual(action, "wait", reason)
        self.assertIn("V2", reason)
        action, reason, _slot = self._judge(cycle=_cycle(status="failed"), v2_runs=[_v2()])
        self.assertEqual(action, "wait", reason)
        self.assertIn("V1", reason)

    def test_failed_v2_watermark_does_not_release_the_gate(self):
        action, reason, _slot = self._judge(v2_last_run="2026-10-05")
        self.assertEqual(action, "wait", reason)
        self.assertIn("watermark", reason)
        action, reason, _slot = self._judge(v2_last_run=None)
        self.assertEqual(action, "wait", reason)

    def test_running_sources_block_only_their_own_unfinished_slot(self):
        action, reason, _slot = self._judge(
            commands="python3 /data/etl_master/master_etl.py --pure-cctns\n",
            v2_runs=[_v2(succeeded=False)],
        )
        self.assertEqual(action, "wait", reason)
        self.assertIn("master_etl", reason)

        action, reason, slot = self._judge(
            now=datetime(2026, 10, 6, 11, 40, tzinfo=IST),
            commands="airflow scheduler cctns_v1_daily_cycle run\n",
            etl3_successes=[],
        )
        self.assertEqual(action, "run", reason)
        self.assertEqual(slot, SLOT)

        action, _reason, _slot = self._judge(
            commands="python3 master_etl.py\n",
            v2_runs=[_v2(succeeded=True)],
        )
        self.assertEqual(action, "run")

    def test_media_process_does_not_block_a_finished_data_cycle(self):
        action, _reason, _slot = self._judge(
            commands="airflow tasks run cctns_v1_daily_sync_media_attachments sync_media\n"
        )
        self.assertEqual(action, "run")

    def test_watermark_must_be_a_date_on_or_after_the_cycle(self):
        ok, _reason = v2_watermark_ready("2026-10-06", SLOT)
        self.assertTrue(ok)
        ok, _reason = v2_watermark_ready('"2026-10-06"', SLOT)
        self.assertTrue(ok)
        ok, _reason = v2_watermark_ready("2026-10-04", SLOT)
        self.assertFalse(ok)
        self.assertIsNone(parse_v2_last_run("  "))

    def test_etl3_from_an_older_cycle_does_not_count_as_this_cycle(self):
        finished = _cycle()["finished_at"]
        action, _reason, _slot = self._judge(etl3_successes=[finished - timedelta(days=1)])
        self.assertEqual(action, "run")

    def test_newer_failed_cycle_is_not_replaced_by_an_older_success(self):
        six = datetime(2026, 10, 6, 11, 30, tzinfo=IST)
        morning = _cycle(run_id="morning")
        failed = _cycle(status="failed", run_id="afternoon", slot=six, started_minute=40)
        action, reason, slot = self._judge(
            now=datetime(2026, 10, 6, 14, 0, tzinfo=IST),
            cycles=[morning, failed],
            entities_by_run_id={
                "morning": _rows(run_id="morning"),
                "afternoon": _rows(run_id="morning"),
            },
            v2_runs=[_v2(slot=SLOT), _v2(slot=six, succeeded=False)],
            etl3_successes=[morning["finished_at"] + timedelta(minutes=1)],
        )
        self.assertEqual(action, "wait", reason)
        self.assertNotEqual(slot, SLOT)

    def test_unprocessed_previous_slot_runs_before_a_failed_newer_slot(self):
        six = datetime(2026, 10, 6, 11, 30, tzinfo=IST)
        morning = _cycle(run_id="morning")
        failed = _cycle(status="failed", run_id="afternoon", slot=six)
        action, reason, slot = self._judge(
            now=datetime(2026, 10, 6, 14, 0, tzinfo=IST),
            cycles=[morning, failed],
            entities_by_run_id={"morning": _rows(run_id="morning")},
            v2_runs=[_v2(slot=SLOT), _v2(slot=six, succeeded=False)],
        )
        self.assertEqual(action, "run", reason)
        self.assertEqual(slot, SLOT)

    def test_long_previous_slot_still_counts_and_two_slots_ago_does_not(self):
        now = datetime(2026, 10, 6, 12, 0, tzinfo=IST)
        cycle = _cycle(hours=8)
        cycle["finished_at"] = datetime(2026, 10, 6, 12, 10, tzinfo=IST)
        action, reason, slot = self._judge(
            now=now,
            cycle=cycle,
            v2_runs=[_v2(slot=SLOT)],
        )
        self.assertEqual(action, "run", reason)
        self.assertEqual(slot, SLOT)

        stale_slot = datetime(2026, 10, 5, 12, 0, tzinfo=IST)
        action, _reason, _slot = self._judge(
            now=now,
            cycle=_cycle(slot=stale_slot, run_id="stale"),
            entities=_rows(run_id="stale", slot=stale_slot),
            v2_runs=[_v2(slot=stale_slot)],
        )
        self.assertEqual(action, "wait")

    def test_v2_log_discovery_requires_the_persist_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ok_dir = root / "20261006_053001"
            ok_dir.mkdir()
            (ok_dir / "master.log").write_text(
                "All ETL processes finished successfully.\nLAST_RUN persisted: 2026-10-06\n",
                encoding="utf-8",
            )
            bad_dir = root / "20261006_113001"
            bad_dir.mkdir()
            (bad_dir / "master.log").write_text(
                "Master ETL execution stopped due to step failure.\n",
                encoding="utf-8",
            )
            runs = discover_v2_runs([root, Path(tmp) / "missing"])
        by_start = {run["started_at"]: run["succeeded"] for run in runs}
        self.assertEqual(by_start[datetime(2026, 10, 6, 5, 30, 1, tzinfo=IST)], True)
        self.assertEqual(by_start[datetime(2026, 10, 6, 11, 30, 1, tzinfo=IST)], False)

    def test_live_v2_start_pairs_only_with_the_same_slot(self):
        started = datetime(2026, 10, 6, 5, 30, 1, tzinfo=IST)
        action, reason, slot = self._judge(v2_runs=[{"started_at": started, "succeeded": True}])
        self.assertEqual(action, "run", reason)
        self.assertEqual(slot, SLOT)
        later = datetime(2026, 10, 6, 11, 30, 1, tzinfo=IST)
        action, reason, _slot = self._judge(v2_runs=[{"started_at": later, "succeeded": True}])
        self.assertEqual(action, "wait", reason)

    def test_gate_source_has_no_fixed_delay(self):
        text = Path(__file__).resolve().parent.parent.joinpath("source_readiness.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("timedelta(hours=2)", text)
        self.assertNotIn("hours=2", text)


if __name__ == "__main__":
    unittest.main()
