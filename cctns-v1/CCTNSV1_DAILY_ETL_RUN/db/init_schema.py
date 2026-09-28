"""
Create the Postgres database (if missing) and apply idempotent DDL before ETL runs.

SQL files (in order):
    db/sql/init_schema.sql      -- 4 data tables, sequences, PK/FK, indexes
    db/sql/init_etl_support.sql -- updated_at, audit log, run log, FIR audit trigger

Upsert keys (natural_key) remain in db/sql/001_schema_fix.sql until reviewed.

Airflow internal tables (separate naming) also live in PG_DATABASE — see db/airflow_metadata.py.
"""
import logging
from pathlib import Path

import psycopg2
from psycopg2 import sql
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

from config.settings import PG_DATABASE, PG_HOST, PG_PASSWORD, PG_PORT, PG_USER, require

logger = logging.getLogger("cctns_v1_etl.db")

SQL_DIR = Path(__file__).resolve().parent / "sql"
INIT_SQL_FILES = (
    "002_migrate_etl_to_cctns_schema.sql",
    "init_schema.sql",
    "init_etl_support.sql",
)

_schema_applied = False


def _connect(dbname: str):
    require("PG_USER", "PG_PASSWORD")
    return psycopg2.connect(
        host=PG_HOST,
        port=PG_PORT,
        dbname=dbname,
        user=PG_USER,
        password=PG_PASSWORD,
    )


def ensure_database_exists() -> None:
    """CREATE DATABASE if the configured PG_DATABASE does not exist."""
    conn = _connect("postgres")
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (PG_DATABASE,))
            if cur.fetchone():
                logger.debug("database %s already exists", PG_DATABASE)
                return
            logger.info("creating database %s", PG_DATABASE)
            cur.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(PG_DATABASE))
            )
    except psycopg2.Error as err:
        logger.warning(
            "could not create database %s (connect to an existing DB or grant CREATEDB): %s",
            PG_DATABASE,
            err,
        )
    finally:
        conn.close()


def _split_sql(script: str) -> list[str]:
    """Split a SQL script into statements (respects dollar-quoted blocks and strings)."""
    statements: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(script)
    dollar_tag: str | None = None

    while i < n:
        if dollar_tag is not None:
            if script.startswith(dollar_tag, i):
                buf.append(dollar_tag)
                i += len(dollar_tag)
                dollar_tag = None
                continue
            buf.append(script[i])
            i += 1
            continue

        if script[i] == "'":
            buf.append(script[i])
            i += 1
            while i < n:
                buf.append(script[i])
                if script[i] == "'":
                    if i + 1 < n and script[i + 1] == "'":
                        buf.append(script[i + 1])
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue

        if script[i] == "$":
            j = i + 1
            while j < n and (script[j].isalnum() or script[j] == "_"):
                j += 1
            if j < n and script[j] == "$":
                dollar_tag = script[i : j + 1]
                buf.append(dollar_tag)
                i = j + 1
                continue

        if script[i] == ";":
            piece = "".join(buf).strip()
            if piece:
                statements.append(piece)
            buf = []
            i += 1
            continue

        buf.append(script[i])
        i += 1

    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def _run_sql_file(cur, path: Path) -> None:
    body = path.read_text(encoding="utf-8")
    for stmt in _split_sql(body):
        cur.execute(stmt)


def apply_schema_migrations() -> None:
    conn = _connect(PG_DATABASE)
    try:
        with conn.cursor() as cur:
            for name in INIT_SQL_FILES:
                path = SQL_DIR / name
                if not path.is_file():
                    raise FileNotFoundError(path)
                logger.info("applying schema file %s", name)
                _run_sql_file(cur, path)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def ensure_schema(force: bool = False) -> None:
    """Once per process: ensure DB exists and DDL is applied."""
    global _schema_applied
    if _schema_applied and not force:
        return
    ensure_database_exists()
    apply_schema_migrations()
    _schema_applied = True
    logger.info("schema bootstrap complete for database %s", PG_DATABASE)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ensure_schema(force=True)
    print(f"OK: {PG_DATABASE} on {PG_HOST}:{PG_PORT}")
