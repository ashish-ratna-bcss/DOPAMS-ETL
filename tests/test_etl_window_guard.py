"""Isolated regression tests for CCTNS V2 date-window checkpointing.

These tests do not connect to a database and do not run the ETL.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from etl_window_guard import (  # noqa: E402
    WindowGuard,
    apply_replay_floor,
    begin_run,
    clamp_iso,
    release_checkpoint,
    run_ordered_windows,
)


class FakeCursor:
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
            self._row = None
        elif "INSERT" in sql_upper:
            self.store[params[0]] = params[1]
            self._row = None
        else:
            raise AssertionError(sql)

    def fetchone(self):
        return self._row


class FakeConn:
    def __init__(self, store):
        self.store = store

    def cursor(self):
        return FakeCursor(self.store)

    def commit(self):
        return None

    def rollback(self):
        return None

    def close(self):
        return None


class Run:
    def __init__(self, store=None):
        self.window_guard = WindowGuard()
        self.store = {} if store is None else store
        self.checkpoint_writes = []
        self._replay_connect = lambda: FakeConn(self.store)

    def advance_checkpoint(self, end_date):
        if not release_checkpoint(self, "crimes"):
            return False
        self.checkpoint_writes.append(end_date)
        return True


WINDOWS = [
    ("2026-09-01", "2026-09-05"),
    ("2026-09-05", "2026-09-09"),
    ("2026-09-09", "2026-09-13"),
]


MODULES = [
    "etl-crimes/etl_crimes.py",
    "etl-hierarchy/etl_hierarchy.py",
    "etl-accused/etl_accused.py",
    "etl-persons/etl_persons.py",
    "etl-properties/etl_properties.py",
    "etl-ir/ir_etl.py",
    "etl-disposal/etl_disposal.py",
    "etl_arrests/etl_arrests.py",
    "etl_mo_seizures/etl_mo_seizure.py",
    "etl_chargesheets/etl_chargesheets.py",
    "etl_updated_chargesheet/etl_update_chargesheet.py",
    "etl_fsl_case_property/etl_fsl_case_property.py",
]


class WindowCheckpointTests(unittest.TestCase):
    def test_fetch_modules_stop_and_gate_the_checkpoint(self):
        for rel in MODULES:
            text = (ROOT / rel).read_text()
            self.assertIn("window_guard.fail(from_date, to_date)", text, rel)
            self.assertIn("begin_run(", text, rel)
            self.assertIn("release_checkpoint(", text, rel)
            self.assertIn("apply_replay_floor(", text, rel)
            if rel == "etl-persons/etl_persons.py":
                self.assertIn("window_guard.begin(", text, rel)
            else:
                self.assertIn("run_ordered_windows(", text, rel)
    def test_successful_window_advances_checkpoint_once(self):
        run = Run()
        seen = []

        def process(from_date, to_date):
            seen.append((from_date, to_date))

        run_ordered_windows(WINDOWS, process, run.window_guard)
        self.assertEqual(seen, WINDOWS)
        self.assertTrue(run.advance_checkpoint("2026-09-13T23:59:59+05:30"))
        self.assertEqual(run.checkpoint_writes, ["2026-09-13T23:59:59+05:30"])
        self.assertEqual(run.store, {})

    def test_api_failure_does_not_advance_checkpoint(self):
        run = Run()
        seen = []

        def process(from_date, to_date):
            seen.append((from_date, to_date))
            if from_date == "2026-09-01":
                run.window_guard.fail(from_date, to_date)

        run_ordered_windows(WINDOWS, process, run.window_guard)
        self.assertEqual(seen, [WINDOWS[0]])
        self.assertFalse(run.advance_checkpoint("2026-09-13T23:59:59+05:30"))
        self.assertEqual(run.checkpoint_writes, [])
        self.assertEqual(
            run.store["crimes__replay_from"],
            "2026-09-01T00:00:00+05:30",
        )

    def test_db_write_failure_does_not_advance_checkpoint(self):
        run = Run()
        seen = []

        def process(from_date, to_date):
            seen.append((from_date, to_date))
            if from_date == "2026-09-05":
                run.window_guard.fail_current()

        run_ordered_windows(WINDOWS, process, run.window_guard)
        self.assertEqual(seen, WINDOWS[:2])
        self.assertFalse(run.advance_checkpoint("2026-09-13T23:59:59+05:30"))
        self.assertEqual(run.store["crimes__replay_from"][:10], "2026-09-05")

    def test_crash_before_checkpoint_keeps_the_start_replayable(self):
        run = Run()
        begin_run(run, "crimes", "2026-09-01T00:00:00+05:30")
        committed = []

        def process(from_date, to_date):
            committed.append(from_date)
            if from_date == "2026-09-01":
                # Process dies after this window's commits, before release.
                raise SystemExit

        try:
            run_ordered_windows(WINDOWS, process, run.window_guard)
        except SystemExit:
            pass
        self.assertEqual(committed, ["2026-09-01"])
        self.assertEqual(run.checkpoint_writes, [])
        resumed = apply_replay_floor(run, "crimes", "2026-09-05T00:00:00+05:30")
        self.assertEqual(resumed, "2026-09-01T00:00:00+05:30")

    def test_process_restart_replays_failed_window(self):
        store = {}
        first = Run(store)
        committed = []

        def process(from_date, to_date):
            if from_date == "2026-09-05":
                first.window_guard.fail(from_date, to_date)
                return
            committed.append((from_date, to_date))

        run_ordered_windows(WINDOWS, process, first.window_guard)
        self.assertFalse(first.advance_checkpoint("2026-09-13T23:59:59+05:30"))

        # A later commit would have left the table cursor on 2026-09-09.
        # The replay floor pulls the next start back to the failed window.
        resumed = apply_replay_floor(first, "crimes", "2026-09-09T00:00:00+05:30")
        self.assertEqual(resumed, "2026-09-05T00:00:00+05:30")

        second = Run(store)
        replayed = []

        def replay(from_date, to_date):
            replayed.append((from_date, to_date))

        run_ordered_windows(
            [("2026-09-05", "2026-09-09"), ("2026-09-09", "2026-09-13")],
            replay,
            second.window_guard,
        )
        self.assertEqual(replayed[0], ("2026-09-05", "2026-09-09"))
        self.assertTrue(second.advance_checkpoint("2026-09-09T23:59:59+05:30"))
        self.assertNotIn("crimes__replay_from", store)

    def test_same_date_modified_is_not_skipped_on_replay(self):
        stamp = "2026-09-30 10:00:00"
        source = [
            {"id": "A", "date_modified": stamp},
            {"id": "B", "date_modified": stamp},
            {"id": "C", "date_modified": stamp},
        ]
        table = {}

        def upsert(rows):
            for row in rows:
                table[row["id"]] = row["date_modified"]

        run = Run()

        def process(from_date, to_date):
            upsert(source)
            run.window_guard.fail_current()

        run_ordered_windows([("2026-09-30", "2026-09-30")], process, run.window_guard)
        self.assertFalse(run.advance_checkpoint("2026-09-30T23:59:59+05:30"))
        self.assertEqual(set(table), {"A", "B", "C"})

        resumed = clamp_iso("2026-09-30T10:00:00+05:30", run.store["crimes__replay_from"])
        self.assertTrue(resumed.startswith("2026-09-30"))
        upsert(source)
        self.assertEqual(table["A"], stamp)
        self.assertEqual(len(table), 3)

    def test_failed_window_retry_keeps_earlier_window(self):
        store = {}
        committed = []
        run = Run(store)

        def process(from_date, to_date):
            if from_date == "2026-09-05":
                run.window_guard.fail(from_date, to_date)
                return
            committed.append(from_date)

        run_ordered_windows(WINDOWS, process, run.window_guard)
        self.assertEqual(committed, ["2026-09-01"])
        self.assertFalse(run.advance_checkpoint("2026-09-13T23:59:59+05:30"))

        retry = Run(store)
        retried = []

        def process_retry(from_date, to_date):
            retried.append(from_date)

        start = apply_replay_floor(retry, "crimes", "2026-09-09T00:00:00+05:30")
        self.assertEqual(start[:10], "2026-09-05")
        run_ordered_windows(
            [("2026-09-05", "2026-09-09"), ("2026-09-09", "2026-09-13")],
            process_retry,
            retry.window_guard,
        )
        self.assertEqual(retried[0], "2026-09-05")
        self.assertNotIn("2026-09-01", retried)
        self.assertTrue(retry.advance_checkpoint("2026-09-13T23:59:59+05:30"))

    def test_partial_batch_does_not_move_checkpoint_past_failure(self):
        run = Run()
        written = []

        def process(from_date, to_date):
            if from_date == "2026-09-01":
                written.extend(["r1", "r2"])
                run.window_guard.fail_current()
                return
            written.append("later-window")

        run_ordered_windows(WINDOWS, process, run.window_guard)
        self.assertEqual(written, ["r1", "r2"])
        self.assertFalse(run.advance_checkpoint("2026-09-13T23:59:59+05:30"))
        self.assertEqual(run.store["crimes__replay_from"][:10], "2026-09-01")

    def test_boundary_resume_includes_failed_window_day(self):
        # The repository does not prove whether the CCTNS API treats fromDate
        # or toDate as inclusive. This test only proves the ETL resume floor:
        # a failed window day is requested again, and a later cursor cannot
        # move the start past that day.
        self.assertEqual(
            clamp_iso("2026-09-30T10:00:00+05:30", "2026-09-30T00:00:00+05:30"),
            "2026-09-30T10:00:00+05:30",
        )
        self.assertEqual(
            clamp_iso("2026-10-01T00:00:00+05:30", "2026-09-30T10:00:00+05:30"),
            "2026-09-30T00:00:00+05:30",
        )
        self.assertTrue(
            clamp_iso("2026-09-30T00:00:00+05:30", "2026-09-30T23:59:59+05:30").startswith(
                "2026-09-30"
            )
        )


    def test_persons_source_marks_db_write_failure(self):
        source = (ROOT / "etl-persons/etl_persons.py").read_text()
        upsert_at = source.index("Error upserting person")
        upsert_tail = source[upsert_at:upsert_at + 400]
        self.assertIn("self.window_guard.fail_current()", upsert_tail)
        worker_at = source.index("Error processing person")
        worker_tail = source[worker_at:worker_at + 700]
        self.assertIn("self.window_guard.fail_current()", worker_tail)
        self.assertIn("pending.cancel()", worker_tail)
        failed_return = source.index("if self.window_guard.failed:\n                release_checkpoint(self, 'persons')")
        self.assertLess(failed_return, source.index("update_run_checkpoint('persons'"))

    def test_persons_db_write_failure_keeps_replay_floor(self):
        run = Run()
        begin_run(run, "persons", "2026-09-05T00:00:00+05:30")
        seen = []
        windows = [("2026-09-05", "2026-09-05"), ("2026-09-06", "2026-09-06")]

        def process_window(from_date, to_date):
            run.window_guard.begin(from_date, to_date)
            seen.append(from_date)
            run.window_guard.fail_current()

        for from_date, to_date in windows:
            if run.window_guard.failed:
                break
            process_window(from_date, to_date)
        self.assertEqual(seen, ["2026-09-05"])
        self.assertFalse(release_checkpoint(run, "persons"))
        self.assertTrue(run.store["persons__replay_from"].startswith("2026-09-05"))
        resumed = apply_replay_floor(run, "persons", "2026-09-06T00:00:00+05:30")
        self.assertEqual(resumed, "2026-09-05T00:00:00+05:30")

    def test_ir_source_marks_db_write_failure(self):
        source = (ROOT / "etl-ir/ir_etl.py").read_text()
        at = source.index("Error processing IR")
        tail = source[at:at + 450]
        self.assertIn("self.window_guard.fail_current()", tail)
        self.assertIn("break", tail)

    def test_boundary_day_replay_keeps_same_timestamp_rows(self):
        stamp = "2026-09-05 23:59:59"
        rows = [
            {"id": "start", "date_modified": "2026-09-05 00:00:00"},
            {"id": "end-a", "date_modified": stamp},
            {"id": "end-b", "date_modified": stamp},
            {"id": "next", "date_modified": "2026-09-06 00:00:00"},
        ]
        table = {}
        run = Run()
        begin_run(run, "crimes", "2026-09-05")

        def upsert(batch):
            for row in batch:
                table[row["id"]] = row["date_modified"]

        def process(from_date, to_date):
            if from_date == "2026-09-05":
                upsert([row for row in rows if row["date_modified"].startswith("2026-09-05")])
                run.window_guard.fail_current()
                return
            upsert([row for row in rows if row["date_modified"].startswith(from_date)])

        run_ordered_windows(
            [("2026-09-05", "2026-09-05"), ("2026-09-06", "2026-09-06")],
            process,
            run.window_guard,
        )
        self.assertNotIn("next", table)
        self.assertFalse(release_checkpoint(run, "crimes"))
        resumed = apply_replay_floor(run, "crimes", "2026-09-06T00:00:00+05:30")
        self.assertEqual(resumed[:10], "2026-09-05")
        upsert([row for row in rows if row["date_modified"][:10] >= resumed[:10]])
        self.assertEqual(set(table), {"start", "end-a", "end-b", "next"})
        self.assertEqual(len(table), 4)

    def test_release_then_crash_before_watermark_is_safe_after_success(self):
        run = Run()
        begin_run(run, "crimes", "2026-09-01T00:00:00+05:30")
        run_ordered_windows(
            [("2026-09-01", "2026-09-05")],
            lambda from_date, to_date: None,
            run.window_guard,
        )
        self.assertTrue(release_checkpoint(run, "crimes"))
        self.assertNotIn("crimes__replay_from", run.store)
        resumed = apply_replay_floor(run, "crimes", "2026-09-05T00:00:00+05:30")
        self.assertEqual(resumed, "2026-09-05T00:00:00+05:30")


    def test_pinned_success_clears_floor_and_advances_watermark(self):
        run = Run()
        begin_run(run, "crimes", "2026-09-01T00:00:00+05:30")
        run_ordered_windows(WINDOWS, lambda from_date, to_date: None, run.window_guard)
        self.assertTrue(run.advance_checkpoint("2026-09-13T23:59:59+05:30"))
        self.assertEqual(run.checkpoint_writes, ["2026-09-13T23:59:59+05:30"])
        self.assertNotIn("crimes__replay_from", run.store)

    def test_crimes_connection_error_fails_the_window(self):
        source = (ROOT / "etl-crimes/etl_crimes.py").read_text()
        at = source.index('logger.error(f"Connection error for {crime_id}: {e}")')
        self.assertIn("self.window_guard.fail_current()", source[at:at + 180])
        run = Run()
        begin_run(run, "crimes", "2026-09-01T00:00:00+05:30")
        seen = []

        def process(from_date, to_date):
            seen.append(from_date)
            if from_date == "2026-09-05":
                run.window_guard.fail_current()

        run_ordered_windows(WINDOWS, process, run.window_guard)
        self.assertEqual(seen, ["2026-09-01", "2026-09-05"])
        self.assertFalse(run.advance_checkpoint("2026-09-13T23:59:59+05:30"))
        self.assertEqual(run.store["crimes__replay_from"][:10], "2026-09-05")

    def test_properties_generic_and_integrity_failures_are_window_fatal(self):
        source = (ROOT / "etl-properties/etl_properties.py").read_text()
        prop_at = source.index('logger.error(f"Error in process_prop: {e}")')
        self.assertIn("self.window_guard.fail_current()", source[prop_at:prop_at + 220])
        integrity_at = source.index("Integrity error for property")
        self.assertIn("self.window_guard.fail_current()", source[integrity_at:integrity_at + 280])
        run = Run()
        begin_run(run, "properties", "2026-09-01T00:00:00+05:30")
        seen = []

        def process(from_date, to_date):
            seen.append(from_date)
            run.window_guard.fail_current()

        run_ordered_windows(WINDOWS, process, run.window_guard)
        self.assertEqual(seen, ["2026-09-01"])
        self.assertFalse(release_checkpoint(run, "properties"))
        self.assertEqual(run.store["properties__replay_from"][:10], "2026-09-01")

    def test_accused_worker_exception_fails_the_window(self):
        source = (ROOT / "etl-accused/etl_accused.py").read_text()
        at = source.index("Record generated an exception")
        self.assertIn("self.window_guard.fail_current()", source[at:at + 280])
        stub_at = source.index("Failed to batch create person stubs")
        self.assertIn("self.window_guard.fail_current()", source[stub_at:stub_at + 220])

    def test_hierarchy_non_duplicate_integrity_error_fails_the_window(self):
        source = (ROOT / "etl-hierarchy/etl_hierarchy.py").read_text()
        at = source.index("Integrity error for hierarchy")
        self.assertIn("self.window_guard.fail_current()", source[at:at + 280])
        self.assertIn("duplicate", source[at - 400:at].lower())

    def test_accused_integrity_error_fails_the_window(self):
        source = (ROOT / "etl-accused/etl_accused.py").read_text()
        at = source.index("Integrity error for accused")
        self.assertIn("self.window_guard.fail_current()", source[at:at + 320])
        run = Run()
        begin_run(run, "accused", "2026-09-01T00:00:00+05:30")
        seen = []

        def process(from_date, to_date):
            seen.append(from_date)
            if from_date == "2026-09-05":
                run.window_guard.fail_current()

        run_ordered_windows(WINDOWS, process, run.window_guard)
        self.assertEqual(seen, ["2026-09-01", "2026-09-05"])
        self.assertFalse(release_checkpoint(run, "accused"))
        self.assertNotIn("accused", "".join(run.checkpoint_writes))
        self.assertEqual(run.store["accused__replay_from"][:10], "2026-09-05")

    def test_replay_floor_write_failure_stops_before_windows(self):
        run = Run()

        def fail_connect():
            raise RuntimeError("bookkeeping write failed")

        run._replay_connect = fail_connect
        seen = []
        with self.assertRaises(RuntimeError):
            begin_run(run, "crimes", "2026-09-01T00:00:00+05:30")
            run_ordered_windows(WINDOWS, lambda from_date, to_date: seen.append(from_date), run.window_guard)
            run.advance_checkpoint("2026-09-13T23:59:59+05:30")
        self.assertEqual(seen, [])
        self.assertEqual(run.checkpoint_writes, [])
        self.assertNotIn("crimes__replay_from", run.store)

    def test_replay_floor_read_failure_does_not_replace_floor(self):
        store = {"crimes__replay_from": "2026-09-01T00:00:00+05:30"}
        run = Run(store)

        def fail_connect():
            raise RuntimeError("bookkeeping read failed")

        run._replay_connect = fail_connect
        seen = []
        with self.assertRaises(RuntimeError):
            start = apply_replay_floor(run, "crimes", "2026-09-09T00:00:00+05:30")
            begin_run(run, "crimes", start)
            run_ordered_windows(WINDOWS, lambda from_date, to_date: seen.append(from_date), run.window_guard)
            run.advance_checkpoint("2026-09-13T23:59:59+05:30")
        self.assertEqual(seen, [])
        self.assertEqual(run.checkpoint_writes, [])
        self.assertEqual(store["crimes__replay_from"], "2026-09-01T00:00:00+05:30")


if __name__ == "__main__":
    unittest.main()
