#!/usr/bin/env python3
"""Concurrency regression: parallel push_fk_failure must not deadlock or lose rows.

Uses namespaced record_keys (__fk_deadlock_test__|...) and deletes them in
tearDown so production data is not left behind.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

for line in (PROJECT_ROOT / ".env").read_text().splitlines():
    s = line.strip()
    if not s or s.startswith("#") or "=" not in s:
        continue
    k, v = s.split("=", 1)
    os.environ.setdefault(k, v.strip().strip("\"'"))

import psycopg2  # noqa: E402

import etl_fk_retry_queue as q  # noqa: E402

TEST_PREFIX = "__fk_deadlock_test__"
MODULE = f"{TEST_PREFIX}_mod"


def _connect():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST") or os.environ.get("DB_HOST"),
        dbname=os.environ.get("POSTGRES_DB") or os.environ.get("DB_NAME"),
        user=os.environ.get("POSTGRES_USER") or os.environ.get("DB_USER"),
        password=os.environ.get("POSTGRES_PASSWORD") or os.environ.get("DB_PASSWORD"),
        port=os.environ.get("POSTGRES_PORT") or os.environ.get("DB_PORT") or "5432",
    )


class TestFkRetryConcurrency(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Force DDL path to run once under concurrency in these tests.
        q._table_ready = False
        cls.run_id = uuid.uuid4().hex[:12]

    def setUp(self):
        q._table_ready = False
        self.conn = _connect()
        self.conn.autocommit = False

    def tearDown(self):
        try:
            self.conn.rollback()
        except Exception:
            pass
        # Cleanup all namespaced test rows
        cleanup = _connect()
        cleanup.autocommit = True
        with cleanup.cursor() as cur:
            cur.execute(
                """
                DELETE FROM etl_bookkeeping
                WHERE kind='fk_retry'
                  AND (
                    module_name = %s
                    OR record_key LIKE %s
                  )
                """,
                (MODULE, TEST_PREFIX + "|%"),
            )
        cleanup.close()
        try:
            self.conn.close()
        except Exception:
            pass

    def test_parallel_unique_pushes_zero_deadlocks_one_row_each(self):
        n = 40
        keys = [f"{TEST_PREFIX}|{self.run_id}|{i}" for i in range(n)]
        errors = []
        lock = threading.Lock()

        def worker(key: str):
            conn = _connect()
            try:
                q.push_fk_failure(
                    conn,
                    MODULE,
                    record_id=key,
                    record_json=json.dumps({"id": key, "crime_id": "missing"}),
                    missing_fk_column="crime_id",
                    missing_fk_value="missing",
                )
                conn.commit()
            except Exception as exc:
                with lock:
                    errors.append((key, str(exc)))
                try:
                    conn.rollback()
                except Exception:
                    pass
            finally:
                conn.close()

        with ThreadPoolExecutor(max_workers=20) as ex:
            list(ex.map(worker, keys))

        self.assertEqual(errors, [], f"workers failed: {errors[:5]}")

        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT record_key, COUNT(*)
                FROM etl_bookkeeping
                WHERE kind='fk_retry' AND module_name=%s
                  AND record_key LIKE %s
                GROUP BY 1
                """,
                (MODULE, f"{TEST_PREFIX}|{self.run_id}|%"),
            )
            rows = cur.fetchall()
        self.assertEqual(len(rows), n)
        self.assertTrue(all(c == 1 for _, c in rows))

    def test_duplicate_push_is_idempotent(self):
        key = f"{TEST_PREFIX}|{self.run_id}|dup"
        for _ in range(2):
            q.push_fk_failure(
                self.conn,
                MODULE,
                record_id=key,
                record_json=json.dumps({"id": key}),
                missing_fk_column="crime_id",
                missing_fk_value="x",
            )
            self.conn.commit()
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) FROM etl_bookkeeping
                WHERE kind='fk_retry' AND module_name=%s AND record_key=%s
                """,
                (MODULE, key),
            )
            self.assertEqual(cur.fetchone()[0], 1)

    def test_parallel_duplicate_same_key_idempotent(self):
        key = f"{TEST_PREFIX}|{self.run_id}|same"
        errors = []

        def worker(_i):
            conn = _connect()
            try:
                q.push_fk_failure(
                    conn,
                    MODULE,
                    record_id=key,
                    record_json=json.dumps({"id": key}),
                    missing_fk_column="crime_id",
                    missing_fk_value="x",
                )
                conn.commit()
            except Exception as exc:
                errors.append(str(exc))
                try:
                    conn.rollback()
                except Exception:
                    pass
            finally:
                conn.close()

        with ThreadPoolExecutor(max_workers=16) as ex:
            futs = [ex.submit(worker, i) for i in range(16)]
            for f in as_completed(futs):
                f.result()

        self.assertEqual(errors, [])
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) FROM etl_bookkeeping
                WHERE kind='fk_retry' AND module_name=%s AND record_key=%s
                """,
                (MODULE, key),
            )
            self.assertEqual(cur.fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
