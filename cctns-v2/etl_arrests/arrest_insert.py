"""Atomic arrest insert keyed by (crime_id, accused_seq_no)."""


def arrest_insert_sql(table: str) -> str:
    """Insert one arrest. A concurrent insert of the same business key inserts nothing."""
    return f"""
        INSERT INTO {table} (
            crime_id, person_id, accused_seq_no, accused_code, accused_type,
            is_arrested, arrested_date, is_41a_crpc, is_41a_explain_submitted,
            date_of_issue_41a, is_ccl, is_apprehended, is_absconding, is_died,
            date_created, date_modified,
            source_system, source_endpoint, fetched_at, etl_run_id
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s
        )
        ON CONFLICT (crime_id, accused_seq_no) DO NOTHING
        RETURNING id
    """


def execute_arrest_insert(cursor, table: str, params):
    """Return the new id, or None when that business key is already present."""
    cursor.execute(arrest_insert_sql(table), params)
    return cursor.fetchone()
