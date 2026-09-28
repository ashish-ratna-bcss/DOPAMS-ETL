"""
Generic upsert: insert new / update changed / skip unchanged, using
Postgres's own row comparison (no hash column, no manual per-field diff code).

    INSERT ... ON CONFLICT (<key column>) DO UPDATE SET ...
    WHERE <table> IS DISTINCT FROM EXCLUDED

The AFTER UPDATE trigger (cctns_v1_log_row_changes, added by
db/sql/001_schema_fix.sql) records the field-level before/after into
cctns_v1_audit_log automatically whenever a row is actually updated.

IMPORTANT -- see db/sql/001_schema_fix.sql header: the conflict key for
cctns_accused and cctns_accused_details is a provisional composite key.
Real duplicate detection surfaced groups of genuinely different people
(e.g. 18 distinct "unknown" accused sharing one FIR) that a narrow key
would wrongly collapse into one row. Do not treat that constraint as final
until the ambiguous groups have been reviewed.
"""
import logging

from config.settings import PG_ETL_SCHEMA

logger = logging.getLogger("cctns_v1_etl.db")


def get_insertable_columns(cur, table):
    cur.execute(
        """
        SELECT column_name, is_generated, column_default
        FROM information_schema.columns
        WHERE table_name = %s AND table_schema = %s
        ORDER BY ordinal_position
        """,
        (table, PG_ETL_SCHEMA),
    )
    cols = []
    for name, is_generated, default in cur.fetchall():
        if is_generated == "ALWAYS":
            continue
        if default and "nextval" in default:
            continue
        if name in ("created_at", "updated_at"):
            continue
        cols.append(name)
    return cols


def upsert_records(cur, table: str, conflict_col: str, records: list) -> dict:
    columns = get_insertable_columns(cur, table)
    fq = f"{PG_ETL_SCHEMA}.{table}"
    col_list = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))
    update_set = ", ".join(
        [f"{c} = EXCLUDED.{c}" for c in columns if c != conflict_col] + ["updated_at = now()"]
    )
    sql = f"""
        INSERT INTO {fq} ({col_list})
        VALUES ({placeholders})
        ON CONFLICT ({conflict_col})
        DO UPDATE SET {update_set}
        WHERE {fq} IS DISTINCT FROM EXCLUDED
        RETURNING (xmax = 0) AS inserted
    """

    inserted = updated = unchanged = 0
    for rec in records:
        rec_upper = {k.upper(): v for k, v in rec.items()}
        values = [rec_upper.get(c.upper()) for c in columns]
        cur.execute(sql, values)
        row = cur.fetchone()
        if row is None:
            unchanged += 1
        elif row[0]:
            inserted += 1
        else:
            updated += 1

    logger.info("table=%s inserted=%d updated=%d unchanged=%d", table, inserted, updated, unchanged)
    return {"inserted": inserted, "updated": updated, "unchanged": unchanged}
