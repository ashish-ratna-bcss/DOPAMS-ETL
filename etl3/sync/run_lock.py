"""One consolidation run at a time.

The lock is a session advisory lock on the unified connection. It is not a
write to V1 or V2. A second run fails before it opens a run-log row.
The lock releases when the session ends, including a crash.
"""

LOCK_KEY = 874533


class ConcurrentRunError(RuntimeError):
    """Another ETL-3 consolidation run already holds the unified lock."""


def acquire(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,))
        return bool(cur.fetchone()[0])


def release(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
