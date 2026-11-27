"""
Connection layer for ETL-3 -- the only place in this codebase that opens a
database connection.

Hard invariant (see dopams_cctns/schema/ETL3_MERGER_IMPLEMENTATION_PLAN.md
section 13 and dopams_cctns/schema/ETL3_RISK_REGISTER.md R12): ETL-3 reads
V1/V2 and writes only to the configured unified database
(dopams_cctns or the parallel enhanced dopams_cctns_v2). This is enforced
two independent ways, not just one, so a single mistake can't silently
violate it:

  1. Every V1/V2 connection forces a read-only Postgres session
     (default_transaction_read_only=on + conn.set_session(readonly=True)) --
     the same pattern already proven throughout this project's prior
     read-only audit tooling (see the session's own q.py). This holds even
     though the underlying credentials (reused from each source ETL's own
     .env) likely have write privilege on their own database -- the session
     itself refuses to allow writes regardless of what the role can do.

  2. Immediately after ANY connection is opened, the actual
     current_database() is checked against a hardcoded expected name (not
     read from the .env, so a misconfigured .env can't silently redirect a
     write). A mismatch closes the connection and raises before a single
     query is allowed to run.
"""
import sys
from pathlib import Path

import psycopg2
from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import settings  # noqa: E402


class WrongDatabaseError(RuntimeError):
    """Raised when a connection did not land on the database it was supposed to."""


def _assert_database(conn, expected_dbname):
    with conn.cursor() as cur:
        cur.execute("SELECT current_database()")
        actual = cur.fetchone()[0]
    if actual != expected_dbname:
        conn.close()
        raise WrongDatabaseError(
            f"Expected to connect to {expected_dbname!r} but landed on {actual!r}. "
            "Connection refused and closed before any query ran."
        )


def get_v1_source_connection():
    """Read-only connection to cctns_v1 (Postgres-enforced, not just application discipline)."""
    e = dotenv_values(settings.V1_SOURCE_ENV_PATH)
    conn = psycopg2.connect(
        host=e["PG_HOST"],
        port=e["PG_PORT"],
        dbname=e["PG_DATABASE"],
        user=e["PG_USER"],
        password=e["PG_PASSWORD"],
        connect_timeout=15,
        application_name="etl3_v1_reader",
        options="-c default_transaction_read_only=on -c statement_timeout=300000",
    )
    conn.set_session(readonly=True, autocommit=False)
    _assert_database(conn, settings.EXPECTED_V1_DBNAME)
    return conn


def get_v2_source_connection():
    """Read-only connection to cctns-v2 (Postgres-enforced, not just application discipline)."""
    e = dotenv_values(settings.V2_SOURCE_ENV_PATH)
    conn = psycopg2.connect(
        host=e["POSTGRES_HOST"],
        port=e["POSTGRES_PORT"],
        dbname=e["POSTGRES_DB"],
        user=e["POSTGRES_USER"],
        password=e["POSTGRES_PASSWORD"],
        connect_timeout=15,
        application_name="etl3_v2_reader",
        options="-c default_transaction_read_only=on -c statement_timeout=300000",
    )
    conn.set_session(readonly=True, autocommit=False)
    _assert_database(conn, settings.EXPECTED_V2_DBNAME)
    return conn


def get_unified_connection(readonly=False):
    """
    Read/write connection to the configured unified database
    (settings.EXPECTED_UNIFIED_DBNAME: dopams_cctns or dopams_cctns_v2) --
    the ONLY database ETL-3 ever writes to. `readonly=True` is available for
    reconciliation/reporting code paths that should never write.

    This is the connection that holds the session-scoped consolidation
    advisory lock (etl3/sync/run_lock.py, key 874533) for the duration of a
    run. The server's own tcp_keepalives_idle is 7200s (confirmed on the
    real host) -- if this process dies without a clean TCP close (killed,
    OOM, network drop), the backend would otherwise sit as a zombie holding
    the lock for up to ~2 hours before the server notices. The keepalive
    overrides below are client-side libpq options that apply only to this
    connection and shrink that detection window to well under a minute,
    without changing the lock's semantics (still session-scoped, still
    released explicitly via pg_advisory_unlock on the happy path).
    """
    cfg = settings.UNIFIED_DB
    conn = psycopg2.connect(
        host=cfg["host"],
        port=cfg["port"],
        dbname=cfg["dbname"],
        user=cfg["user"],
        password=cfg["password"],
        connect_timeout=15,
        application_name="etl3_unified",
        keepalives=1,
        keepalives_idle=20,
        keepalives_interval=10,
        keepalives_count=3,
        options="-c idle_in_transaction_session_timeout=600000",
    )
    if readonly:
        conn.set_session(readonly=True, autocommit=False)
    _assert_database(conn, settings.EXPECTED_UNIFIED_DBNAME)
    return conn
