"""V1 daily-cycle lock, order, marker, and CLI rules.

Run from CCTNSV1_DAILY_ETL_RUN:
    python -m unittest tests.test_daily_cycle
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from db.cycle_lock import CycleRunLock  # noqa: E402
from db.cycle_rules import (  # noqa: E402
    IST,
    assess_cycle_marker,
    assess_entity_rows,
    cycle_boundary,
    cycle_slot_start,
    pick_latest_cycle,
)
from db.orchestrate_cycle import (  # noqa: E402
    orchestrate_daily_cycle,
    orchestrate_partial_cycle,
)

NOW = datetime(2026, 10, 6, 2, 0, tzinfo=IST)


class MemoryCycleLog:
    def __init__(self):
        self.cycle = None
        self.rows = []
        self.statuses = []
        self._lock = threading.Lock()

    def abandon_stale(self):
        return None

    def start(self, run_id, cycle_start):
        self.cycle = {
            "run_id": run_id,
            "status": "running",
            "started_at": NOW,
            "finished_at": None,
            "cycle_start": cycle_start,
        }

    def finish(self, run_id, status, error):
        self.statuses.append(status)
        if self.cycle and str(self.cycle["run_id"]) == str(run_id):
            self.cycle["status"] = status
            self.cycle["finished_at"] = NOW + timedelta(hours=3)
            self.cycle["error_message"] = error

    def add_entity(self, row):
        with self._lock:
            self.rows.append(row)

    def read_entities(self, run_id):
        with self._lock:
            return [dict(row) for row in self.rows if str(row["run_id"]) == str(run_id)]


class ScriptedRunner:
    def __init__(self, log, outcomes, start):
        self.log = log
        self.outcomes = outcomes
        self.tick = start
        self.calls = []
        self.run_ids = []
        self._lock = threading.Lock()

    def __call__(self, entity, run_id):
        with self._lock:
            self.calls.append(entity)
            self.run_ids.append(run_id)
            started = self.tick
            self.tick += timedelta(seconds=1)
        status = self.outcomes[entity]["status"]
        if self.outcomes[entity].get("raise"):
            raise RuntimeError(self.outcomes[entity]["raise"])
        with self._lock:
            finished = self.tick
            self.tick += timedelta(seconds=1)
        self.log.add_entity(
            {
                "entity": entity,
                "run_id": run_id,
                "status": status,
                "started_at": started,
                "finished_at": finished,
            }
        )
        return {"status": status}


def _loaded(entity="accused"):
    return {
        "fir": {"status": "loaded"},
        "court": {"status": "loaded"},
        "accused_details": {"status": "loaded"},
        "accused": {"status": "loaded_with_known_gaps" if entity == "gaps" else "loaded"},
    }


class CycleOrchestratorTests(unittest.TestCase):
    def _run(self, outcomes, log=None):
        log = log or MemoryCycleLog()
        runner = ScriptedRunner(log, outcomes, NOW)
        result = orchestrate_daily_cycle(
            execute_entity=runner,
            open_log=lambda: log,
            clock=lambda: NOW,
        )
        return result, log, runner

    def test_full_cycle_one_run_id_and_marker(self):
        result, log, runner = self._run(_loaded("gaps"))
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(runner.calls[0], "fir")
        self.assertEqual(set(runner.calls[1:3]), {"court", "accused_details"})
        self.assertEqual(runner.calls[3], "accused")
        self.assertEqual(len(set(runner.run_ids)), 1)
        self.assertEqual(log.statuses, ["succeeded"])
        self.assertIn("succeeded", log.statuses)
        self.assertNotIn("incomplete", log.statuses)

    def test_court_failure_does_not_run_accused_or_mark_success(self):
        outcomes = _loaded()
        outcomes["court"] = {"status": "extract_failed"}
        result, log, runner = self._run(outcomes)
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("accused", runner.calls)
        self.assertNotIn("succeeded", log.statuses)

    def test_entity_exception_marks_the_cycle_failed(self):
        outcomes = _loaded()
        outcomes["court"] = {"status": "loaded", "raise": "court blew up"}
        log = MemoryCycleLog()
        runner = ScriptedRunner(log, outcomes, NOW)
        with self.assertRaises(RuntimeError):
            orchestrate_daily_cycle(
                execute_entity=runner,
                open_log=lambda: log,
                clock=lambda: NOW,
            )
        self.assertEqual(log.cycle["status"], "failed")
        self.assertNotIn("accused", runner.calls)
        self.assertNotIn("succeeded", log.statuses)

    def test_fir_failure_stops_the_cycle(self):
        outcomes = _loaded()
        outcomes["fir"] = {"status": "load_failed"}
        result, log, runner = self._run(outcomes)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(runner.calls, ["fir"])
        self.assertEqual(log.cycle["status"], "failed")

    def test_stored_rows_must_pass_order_before_marker(self):
        log = MemoryCycleLog()
        real_read = log.read_entities

        def tampered(run_id):
            rows = real_read(run_id)
            for row in rows:
                if row["entity"] == "accused":
                    row["started_at"] = NOW - timedelta(hours=5)
            return rows

        log.read_entities = tampered
        result, log, _runner = self._run(_loaded(), log)
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("succeeded", log.statuses)

    def test_missing_stored_rows_are_not_a_cycle(self):
        log = MemoryCycleLog()
        runner = ScriptedRunner(log, _loaded(), NOW)
        runner.log = MemoryCycleLog()  # statuses return loaded, but the cycle log has no rows
        result = orchestrate_daily_cycle(
            execute_entity=runner,
            open_log=lambda: log,
            clock=lambda: NOW,
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(log.cycle["status"], "failed")

    def test_partial_cli_cannot_write_success_marker(self):
        log = MemoryCycleLog()
        runner = ScriptedRunner(log, _loaded(), NOW)
        simple = orchestrate_partial_cycle(
            ("fir", "court", "accused_details"),
            execute_entity=runner,
            open_log=lambda: log,
            clock=lambda: NOW,
        )
        self.assertEqual(simple["status"], "incomplete")
        self.assertEqual(log.statuses, ["incomplete"])
        accused_log = MemoryCycleLog()
        accused = orchestrate_partial_cycle(
            ("accused",),
            execute_entity=ScriptedRunner(accused_log, _loaded(), NOW),
            open_log=lambda: accused_log,
            clock=lambda: NOW,
        )
        self.assertEqual(accused["status"], "incomplete")
        self.assertNotEqual(simple["run_id"], accused["run_id"])
        ok, reason = assess_cycle_marker(log.cycle, log.read_entities(simple["run_id"]), cycle_boundary(NOW))
        self.assertFalse(ok, reason)

    def test_overlapping_cycles_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"CCTNS_V1_ETL_LOCK_DIR": tmp}):
                started = threading.Event()
                release = threading.Event()
                opened = []

                def blocking(entity, run_id):
                    started.set()
                    self.assertTrue(release.wait(5))
                    return {"status": "loaded"}

                def open_log():
                    log = MemoryCycleLog()
                    opened.append(log)
                    return log

                def first():
                    orchestrate_partial_cycle(
                        ("fir",),
                        execute_entity=blocking,
                        open_log=open_log,
                        clock=lambda: NOW,
                    )

                worker = threading.Thread(target=first)
                worker.start()
                self.assertTrue(started.wait(5))
                with self.assertRaises(RuntimeError) as ctx:
                    orchestrate_daily_cycle(
                        execute_entity=blocking,
                        open_log=open_log,
                        clock=lambda: NOW,
                    )
                self.assertIn("already running", str(ctx.exception))
                release.set()
                worker.join(5)
                self.assertEqual(len(opened), 1)
                self.assertEqual(opened[0].cycle["status"], "incomplete")

    def test_six_hour_tick_does_not_enter_while_the_previous_cycle_holds_the_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"CCTNS_V1_ETL_LOCK_DIR": tmp}):
                started = threading.Event()
                release = threading.Event()

                def blocking(entity, run_id):
                    started.set()
                    self.assertTrue(release.wait(5))
                    return {"status": "loaded"}

                def first():
                    orchestrate_daily_cycle(
                        execute_entity=blocking,
                        open_log=MemoryCycleLog,
                        clock=lambda: NOW,
                    )

                worker = threading.Thread(target=first)
                worker.start()
                self.assertTrue(started.wait(5))
                with self.assertRaises(RuntimeError) as ctx:
                    orchestrate_daily_cycle(
                        execute_entity=blocking,
                        open_log=MemoryCycleLog,
                        clock=lambda: NOW + timedelta(hours=6),
                    )
                self.assertIn("already running", str(ctx.exception))
                release.set()
                worker.join(5)

    def test_next_slot_gets_a_new_run_id_and_marker(self):
        first, log1, _runner1 = self._run(_loaded())
        later = NOW + timedelta(hours=6)
        log2 = MemoryCycleLog()
        runner2 = ScriptedRunner(log2, _loaded(), later)
        second = orchestrate_daily_cycle(
            execute_entity=runner2,
            open_log=lambda: log2,
            clock=lambda: later,
        )
        self.assertEqual(first["status"], "succeeded")
        self.assertEqual(second["status"], "succeeded")
        self.assertNotEqual(first["run_id"], second["run_id"])
        self.assertEqual(log1.cycle["cycle_start"], datetime(2026, 10, 5, 23, 30, tzinfo=IST))
        self.assertEqual(log2.cycle["cycle_start"], datetime(2026, 10, 6, 5, 30, tzinfo=IST))
        self.assertEqual({row["run_id"] for row in log1.read_entities(first["run_id"])}, {first["run_id"]})
        self.assertEqual({row["run_id"] for row in log2.read_entities(second["run_id"])}, {second["run_id"]})


class CycleRuleTests(unittest.TestCase):
    def _rows(self, **overrides):
        boundary = cycle_boundary(NOW)
        cursor = boundary + timedelta(minutes=10)
        rows = []
        for entity in ("fir", "court", "accused_details", "accused"):
            started = cursor
            cursor += timedelta(minutes=5)
            finished = cursor
            cursor += timedelta(minutes=1)
            rows.append(
                {
                    "entity": entity,
                    "run_id": "cycle-1",
                    "status": "loaded",
                    "started_at": started,
                    "finished_at": finished,
                }
            )
        # court and accused_details must both start after fir finishes.
        # The sequential builder above already does that. Parallel is allowed,
        # so move accused_details up beside court.
        by_name = {row["entity"]: row for row in rows}
        by_name["accused_details"]["started_at"] = by_name["court"]["started_at"]
        by_name["accused_details"]["finished_at"] = by_name["court"]["finished_at"]
        for entity, changes in overrides.items():
            by_name[entity].update(changes)
        return list(by_name.values()), boundary

    def test_ordered_rows_pass(self):
        rows, boundary = self._rows()
        ok, reason = assess_entity_rows(rows, "cycle-1", boundary)
        self.assertTrue(ok, reason)

    def test_mixed_run_id_fails(self):
        rows, boundary = self._rows(accused={"run_id": "other-cycle"})
        ok, _reason = assess_entity_rows(rows, "cycle-1", boundary)
        self.assertFalse(ok)

    def test_wrong_order_fails(self):
        rows, boundary = self._rows()
        by_name = {row["entity"]: row for row in rows}
        by_name["accused"]["started_at"] = by_name["fir"]["started_at"]
        ok, _reason = assess_entity_rows(rows, "cycle-1", boundary)
        self.assertFalse(ok)


class SixHourScheduleTests(unittest.TestCase):
    def test_slots_match_the_live_v2_clock(self):
        def at(day, hour, minute):
            return datetime(2026, 10, day, hour, minute, tzinfo=IST)

        self.assertEqual(cycle_slot_start(at(6, 5, 29)), at(5, 23, 30))
        self.assertEqual(cycle_slot_start(at(6, 5, 30)), at(6, 5, 30))
        self.assertEqual(cycle_slot_start(at(6, 11, 29)), at(6, 5, 30))
        self.assertEqual(cycle_slot_start(at(6, 11, 30)), at(6, 11, 30))
        self.assertEqual(cycle_slot_start(at(6, 17, 30)), at(6, 17, 30))
        self.assertEqual(cycle_slot_start(at(6, 23, 30)), at(6, 23, 30))
        # A finish at 09:45 does not move the next 11:30 slot.
        self.assertEqual(cycle_slot_start(at(6, 9, 45)), at(6, 5, 30))
        self.assertEqual(cycle_slot_start(at(6, 11, 30)), at(6, 11, 30))
        self.assertEqual(cycle_boundary(at(6, 8, 0)), at(5, 23, 30))

    def test_latest_failure_hides_an_older_success(self):
        floor = datetime(2026, 10, 6, 0, 0, tzinfo=IST)
        early = {
            "run_id": "morning",
            "status": "succeeded",
            "started_at": datetime(2026, 10, 6, 0, 40, tzinfo=IST),
        }
        later = {
            "run_id": "afternoon",
            "status": "failed",
            "started_at": datetime(2026, 10, 6, 6, 40, tzinfo=IST),
        }
        picked = pick_latest_cycle([early, later], floor)
        self.assertEqual(picked["run_id"], "afternoon")
        self.assertEqual(picked["status"], "failed")

    def test_airflow_schedule_is_six_hours_and_keeps_the_twelve_hour_attempt(self):
        from dags.cctnsv1_dag_common import SCHEDULE_DAILY_CYCLE

        self.assertEqual(SCHEDULE_DAILY_CYCLE, "0 0,6,12,18 * * *")
        text = (Path(ROOT) / "dags" / "daily_cycle.py").read_text(encoding="utf-8")
        self.assertIn("max_active_runs=1", text)
        self.assertIn("timedelta(hours=12)", text)


class CycleLockTests(unittest.TestCase):
    def test_second_lock_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"CCTNS_V1_ETL_LOCK_DIR": tmp}):
                with CycleRunLock():
                    with self.assertRaises(RuntimeError):
                        with CycleRunLock():
                            pass


if __name__ == "__main__":
    unittest.main()
