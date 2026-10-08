"""
Applies a migration SQL file to the configured unified database
(dopams_cctns or dopams_cctns_v2), with explicit safety gates:

  1. Connects via db.connections.get_unified_connection(), which already
     refuses to proceed if current_database() != settings.EXPECTED_UNIFIED_DBNAME.
  2. Before running any DDL, additionally confirms the migration has not
     already been applied (checks for a _migrations tracking table and
     whether this filename is recorded there) and, for the very first
     migration, that the public schema is actually empty -- so this can't
     accidentally re-run DDL against a database that already has data in it.
  3. Runs the whole file in a single transaction: commits only if everything
     succeeds, rolls back entirely otherwise.
  4. Records the applied migration in _migrations on success.

Usage: python etl3/migrations/run_migration.py 001_initial_schema.sql
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from etl3.db import connections

MIGRATIONS_DIR = Path(__file__).resolve().parent


def ensure_migrations_table(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS _migrations (
                filename    TEXT PRIMARY KEY,
                applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    conn.commit()


def already_applied(conn, filename):
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM _migrations WHERE filename = %s", (filename,))
        return cur.fetchone() is not None


def public_schema_table_count(conn):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema = 'public'"
        )
        return cur.fetchone()[0]


def apply_migration(filename):
    path = MIGRATIONS_DIR / filename
    if not path.exists():
        raise FileNotFoundError(path)
    sql = path.read_text(encoding="utf-8")

    conn = connections.get_unified_connection()
    try:
        ensure_migrations_table(conn)

        if already_applied(conn, filename):
            print(f"[SKIP] {filename} is already recorded as applied. Nothing to do.")
            return

        if filename == "001_initial_schema.sql":
            n = public_schema_table_count(conn)
            # _migrations itself is now the only table at this point.
            if n > 1:
                raise RuntimeError(
                    f"Refusing to apply {filename}: public schema already has "
                    f"{n} table(s) (expected only _migrations). This migration "
                    "is meant for an empty database only."
                )

        print(f"Applying {filename} to {connections.settings.EXPECTED_UNIFIED_DBNAME} ...")
        with conn.cursor() as cur:
            cur.execute(sql)
            cur.execute(
                "INSERT INTO _migrations (filename) VALUES (%s)", (filename,)
            )
        conn.commit()
        print(f"[OK] {filename} applied and recorded.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python run_migration.py <filename>")
        sys.exit(1)
    apply_migration(sys.argv[1])
