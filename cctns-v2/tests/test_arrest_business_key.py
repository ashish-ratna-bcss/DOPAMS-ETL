"""Arrest business-key insert: one row per (crime_id, accused_seq_no)."""

import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "etl_arrests"))

from arrest_insert import arrest_insert_sql, execute_arrest_insert


def _params(crime_id, seq, marker="A"):
    return (
        crime_id, "person-1", seq, marker, "Accused",
        True, None, False, False,
        None, False, False, False, False,
        "2026-07-22", "2026-07-25",
        "CCTNS_V2", "/arrests", "fetched", "run-1",
    )


class UniqueArrestTable:
    """In-process stand-in for the unique index ON CONFLICT uses."""

    def __init__(self):
        self._lock = threading.Lock()
        self.rows = {}
        self._next_id = 1

    def cursor(self):
        return UniqueCursor(self)


class UniqueCursor:
    def __init__(self, table):
        self.table = table
        self.row = None
        self.sql = ""

    def execute(self, sql, params):
        self.sql = sql
        conflict = sql.split("ON CONFLICT", 1)[1]
        if "DO NOTHING" not in conflict or "SET" in conflict:
            raise AssertionError(sql)
        key = (params[0], params[2])
        with self.table._lock:
            if key in self.table.rows:
                self.row = None
                return
            self.table.rows[key] = tuple(params)
            self.row = (self.table._next_id,)
            self.table._next_id += 1

    def fetchone(self):
        return self.row


class ArrestBusinessKeyTests(unittest.TestCase):
    def test_first_insert_succeeds(self):
        table = UniqueArrestTable()
        claimed = execute_arrest_insert(table.cursor(), "arrests", _params("crime-a", "seq-1"))
        self.assertIsNotNone(claimed)
        self.assertEqual(len(table.rows), 1)

    def test_replay_does_not_create_another_row_or_change_fields(self):
        table = UniqueArrestTable()
        first = _params("crime-a", "seq-1", marker="A4")
        self.assertIsNotNone(execute_arrest_insert(table.cursor(), "arrests", first))
        replay = _params("crime-a", "seq-1", marker="CHANGED")
        self.assertIsNone(execute_arrest_insert(table.cursor(), "arrests", replay))
        self.assertEqual(len(table.rows), 1)
        self.assertEqual(table.rows[("crime-a", "seq-1")], first)
        self.assertNotIn("SET", arrest_insert_sql("arrests").split("ON CONFLICT", 1)[1])

    def test_concurrent_claims_keep_one_business_row(self):
        table = UniqueArrestTable()
        barrier = threading.Barrier(8)
        claimed = []
        lock = threading.Lock()

        def worker():
            barrier.wait()
            row = execute_arrest_insert(
                table.cursor(), "arrests", _params("6a6106224b1f363353c95536", "202903726102367004")
            )
            with lock:
                claimed.append(row)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sum(1 for row in claimed if row), 1)
        self.assertEqual(len(table.rows), 1)

    def test_different_crimes_and_sequences_insert_independently(self):
        table = UniqueArrestTable()
        self.assertIsNotNone(execute_arrest_insert(table.cursor(), "arrests", _params("crime-a", "seq-1")))
        self.assertIsNotNone(execute_arrest_insert(table.cursor(), "arrests", _params("crime-b", "seq-1")))
        self.assertIsNotNone(execute_arrest_insert(table.cursor(), "arrests", _params("crime-a", "seq-2")))
        self.assertEqual(len(table.rows), 3)

    def test_etl_uses_conflict_insert_and_keeps_checkpoint_and_fk_retry(self):
        source = (ROOT / "etl_arrests/etl_arrests.py").read_text()
        schema = (ROOT / "cctns-v2_schema.sql").read_text()
        self.assertIn("execute_arrest_insert", source)
        self.assertIn("ON CONFLICT (crime_id, accused_seq_no) DO NOTHING", arrest_insert_sql("arrests"))
        self.assertIn(
            "CREATE UNIQUE INDEX uq_arrests_crime_id_accused_seq_no",
            schema,
        )
        self.assertNotIn("INSERT INTO {ARRESTS_TABLE}", source)
        integrity = source.index("Integrity error for arrests")
        self.assertIn("self.window_guard.fail_current()", source[integrity:integrity + 250])
        self.assertIn("push_fk_failure(", source)
        release = source.index("if not release_checkpoint(self, 'arrests'):")
        self.assertLess(release, source.index('return True', release))
        update = source.index("Existing is not NULL, new is NULL")
        self.assertIn("Will keep existing", source[update:update + 400])


if __name__ == "__main__":
    unittest.main()
