"""Idempotent writes into dopams_cctns enrichment tables.

A row whose input_hash is already stored is left untouched and does not
append a change_log row. Canonical *_unified columns are not updated.
"""
from psycopg2.extras import Json, execute_values


def _db_value(value):
    if isinstance(value, (dict, list)):
        return Json(value)
    return value

_BATCH = 500


def _chunks(items, size):
    batch = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def upsert_rows(conn, table, pk, rows, columns, run_id, entity):
    """Insert or update rows. Returns {'inserted': n, 'updated': n, 'unchanged': n}."""
    if not rows:
        return {"inserted": 0, "updated": 0, "unchanged": 0}
    inserted = updated = unchanged = 0
    data_cols = [c for c in columns if c != pk]
    for batch in _chunks(rows, _BATCH):
        keys = [row[pk] for row in batch]
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {pk}, input_hash FROM {table} WHERE {pk} = ANY(%s)",
                (keys,),
            )
            existing = {key: digest for key, digest in cur.fetchall()}
        changed = []
        for row in batch:
            previous = existing.get(row[pk])
            if previous == row["input_hash"]:
                unchanged += 1
                continue
            changed.append((row, previous))
        if not changed:
            continue
        values = []
        for row, _previous in changed:
            values.append(tuple(_db_value(row.get(c)) for c in columns) + (run_id,))
        set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in data_cols)
        sql = f"""
            INSERT INTO {table} ({", ".join(columns)}, enrichment_run_id)
            VALUES %s
            ON CONFLICT ({pk}) DO UPDATE SET
                {set_clause},
                enrichment_run_id = EXCLUDED.enrichment_run_id,
                computed_at = now()
            WHERE {table}.input_hash IS DISTINCT FROM EXCLUDED.input_hash
        """
        with conn.cursor() as cur:
            execute_values(cur, sql, values, page_size=_BATCH)
            log_rows = []
            for row, previous in changed:
                classification = "initial_observation" if previous is None else "business_change"
                log_rows.append((
                    entity, row[pk], "input_hash", previous, row["input_hash"],
                    row.get("source_system") or "V2", str(run_id), classification,
                ))
                if previous is None:
                    inserted += 1
                else:
                    updated += 1
            execute_values(
                cur,
                """
                INSERT INTO change_log
                    (entity, unified_id, field, old_value, new_value, observed_at,
                     source_system, source_run_id, change_classification)
                VALUES %s
                """,
                log_rows,
                template="(%s, %s, %s, %s, %s, now(), %s, %s, %s)",
                page_size=_BATCH,
            )
    return {"inserted": inserted, "updated": updated, "unchanged": unchanged}


def replace_provenance(conn, provenance, rows, columns, run_id):
    """Upsert drug rows for one provenance and delete ids no longer produced.

    etl3_ai is not deleted here; the AI pass replaces a crime only after a
    successful response.
    """
    stats = upsert_rows(
        conn, "drug_extractions", "extraction_id", rows, columns, run_id, "drug_extraction",
    )
    keep = [row["extraction_id"] for row in rows]
    with conn.cursor() as cur:
        if keep:
            cur.execute(
                """
                DELETE FROM drug_extractions
                WHERE provenance = %s AND NOT (extraction_id = ANY(%s))
                """,
                (provenance, keep),
            )
        else:
            cur.execute("DELETE FROM drug_extractions WHERE provenance = %s", (provenance,))
        stats["deleted"] = cur.rowcount
    return stats


def record_ai_attempt(
    conn,
    crime_id,
    input_hash,
    model,
    status,
    attempt_count,
    error_message,
    source_system=None,
    source_module=None,
    raw_response=None,
    parsed_response=None,
    validation_status=None,
    validation_errors=None,
):
    """Persist one AI attempt. Does not store full source text (use input_hash)."""
    # Bound raw response to keep the audit table manageable.
    if raw_response is not None:
        raw_response = str(raw_response)
        if len(raw_response) > 50000:
            raw_response = raw_response[:50000] + "…[truncated]"
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ai_extraction_attempts
                (crime_id, input_hash, model, status, attempt_count, error_message,
                 source_system, source_module, raw_response, parsed_response,
                 validation_status, validation_errors)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (crime_id, input_hash, status) DO UPDATE SET
                attempt_count = EXCLUDED.attempt_count,
                error_message = EXCLUDED.error_message,
                model = EXCLUDED.model,
                source_system = COALESCE(EXCLUDED.source_system, ai_extraction_attempts.source_system),
                source_module = COALESCE(EXCLUDED.source_module, ai_extraction_attempts.source_module),
                raw_response = COALESCE(EXCLUDED.raw_response, ai_extraction_attempts.raw_response),
                parsed_response = COALESCE(EXCLUDED.parsed_response, ai_extraction_attempts.parsed_response),
                validation_status = COALESCE(EXCLUDED.validation_status, ai_extraction_attempts.validation_status),
                validation_errors = COALESCE(EXCLUDED.validation_errors, ai_extraction_attempts.validation_errors),
                created_at = now()
            """,
            (
                crime_id, input_hash, model, status, attempt_count, error_message,
                source_system, source_module, raw_response,
                Json(parsed_response) if parsed_response is not None else None,
                validation_status,
                Json(validation_errors) if validation_errors is not None else None,
            ),
        )


def as_json(value):
    if value is None:
        return None
    return Json(value)
