"""Half-open API dates and non-retryable source-failure bookkeeping."""

import unittest
from pathlib import Path

from etl_fk_retry_queue import (
    missing_accused_record_key,
    record_source_failure,
)
from etl_run_config import half_open_api_to_date

ROOT = Path(__file__).resolve().parents[1]

HALF_OPEN_FETCHES = (
    "etl-crimes/etl_crimes.py",
    "etl-accused/etl_accused.py",
    "etl-disposal/etl_disposal.py",
    "etl_arrests/etl_arrests.py",
    "etl-properties/etl_properties.py",
    "etl-ir/ir_etl.py",
    "etl_mo_seizures/etl_mo_seizure.py",
    "etl_fsl_case_property/etl_fsl_case_property.py",
)

INCLUSIVE_FETCHES = (
    "etl_chargesheets/etl_chargesheets.py",
    "etl_updated_chargesheet/etl_update_chargesheet.py",
)


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self.conn.statements.append((sql, params))


class FakeConn:
    def __init__(self):
        self.statements = []
        self.commits = 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1


class HalfOpenWindowTests(unittest.TestCase):
    def test_single_day_window_requests_the_next_day(self):
        self.assertEqual(half_open_api_to_date("2026-09-30"), "2026-10-01")

    def test_inclusive_end_day_is_inside_the_request(self):
        self.assertEqual(half_open_api_to_date("2026-09-20T23:59:59+05:30"), "2026-09-21")

    def test_half_open_fetches_use_the_exclusive_end(self):
        for relative in HALF_OPEN_FETCHES:
            source = (ROOT / relative).read_text()
            self.assertIn("half_open_api_to_date", source, relative)

    def test_inclusive_endpoints_keep_the_given_end_day(self):
        for relative in INCLUSIVE_FETCHES:
            source = (ROOT / relative).read_text()
            self.assertNotIn("half_open_api_to_date", source, relative)
            self.assertIn("'toDate': to_date", source, relative)


class SourceFailureTests(unittest.TestCase):
    def test_failure_row_is_upserted_and_committed(self):
        conn = FakeConn()
        record_source_failure(
            conn, "crimes", "crime-1", "missing_ps_code",
            {"crime_id": "crime-1"}, ensure=False,
        )
        sql, params = conn.statements[0]
        self.assertIn("VALUES ('failure'", sql)
        self.assertIn("WHERE kind = 'failure'", sql)
        self.assertNotIn("fk_retry", sql)
        self.assertEqual(params[0], "crimes")
        self.assertEqual(params[1], "crime-1")
        self.assertEqual(params[2], "missing_ps_code")
        self.assertIn("crime-1", params[3])
        self.assertEqual(conn.commits, 1)

    def test_missing_accused_key_uses_crime_person_and_seq(self):
        key = missing_accused_record_key({
            "crime_id": "c1", "person_id": "p1", "seq_num": "7",
        })
        self.assertEqual(key, "c1|p1|7")
        self.assertEqual(missing_accused_record_key({}), "missing_accused_id")

    def test_four_skip_classes_are_persisted_and_not_retried(self):
        crimes = (ROOT / "etl-crimes/etl_crimes.py").read_text()
        accused = (ROOT / "etl-accused/etl_accused.py").read_text()
        persons = (ROOT / "etl-persons/etl_persons.py").read_text()
        self.assertIn("'missing_ps_code'", crimes)
        self.assertIn("'ps_code_not_found'", crimes)
        self.assertIn("persist_source_failure", crimes)
        self.assertIn("'missing_accused_id'", accused)
        self.assertIn("persist_source_failure", accused)
        self.assertIn("'http_400'", persons)
        self.assertIn("persist_source_failure", persons)
        queue = (ROOT / "etl_fk_retry_queue.py").read_text()
        start = queue.index("def persist_source_failure")
        body = queue[start:start + 500]
        self.assertNotIn("drain_fk_queue", body)


if __name__ == "__main__":
    unittest.main()
