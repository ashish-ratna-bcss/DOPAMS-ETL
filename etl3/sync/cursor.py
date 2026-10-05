"""Cursor high-water mark.

Exclusion of already-captured source runs is the set of source_run_id
values already stored on observations. The cursor row is only a monitoring
mark and moves forward after those observations have been consolidated.
An empty batch does not clear it. A lower source order does not replace it.
"""
from etl3.sync.catalog import MODULES, SENTINELS, adapter_for


def known_run_ids(conn, source_system: str, source_table: str, obs_table: str) -> set:
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT DISTINCT source_run_id
            FROM {obs_table}
            WHERE source_system = %s AND source_table = %s
            """,
            (source_system, source_table),
        )
        return {row[0] for row in cur.fetchall() if row[0] and row[0] not in SENTINELS}


def select_high_water(observed_ids: set, orders: dict):
    """Highest order among run ids that were actually observed.

    Run ids with no order are ignored, so a cursor cannot jump to a run
    the source cannot place. Ties break on the run id string, which is
    stable and does not depend on discovery order.
    """
    best_id = None
    best_order = None
    for run_id in observed_ids:
        if run_id in SENTINELS or "#m:" in run_id:
            continue
        order = orders.get(run_id)
        if order is None:
            continue
        if best_id is None or order > best_order or (order == best_order and run_id > best_id):
            best_id = run_id
            best_order = order
    return best_id, best_order


def _restore_idle(conn, source_system: str, module: str):
    """A skipped or refused advance must not leave status=running, and must
    not clear or rewrite last_processed_source_run_id."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE consolidation_cursor
            SET status = 'idle'
            WHERE source_system = %s AND source_module = %s AND status = 'running'
            """,
            (source_system, module),
        )


def advance_cursor(conn, source_system: str, module: str, candidate_run_id, candidate_order, current_order) -> str:
    """Returns advanced, unchanged, refused_regression, or skipped_empty.

    Does not delete or null an existing cursor. Does not store sentinel
    run ids. Status returns to idle only when the candidate is accepted
    or already current.
    """
    if not candidate_run_id or candidate_run_id in SENTINELS:
        _restore_idle(conn, source_system, module)
        return "skipped_empty"
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT last_processed_source_run_id
            FROM consolidation_cursor
            WHERE source_system = %s AND source_module = %s
            """,
            (source_system, module),
        )
        row = cur.fetchone()
        current = row[0] if row else None
        if current == candidate_run_id:
            cur.execute(
                """
                UPDATE consolidation_cursor
                SET last_processed_at = now(), status = 'idle'
                WHERE source_system = %s AND source_module = %s
                """,
                (source_system, module),
            )
            return "unchanged"
        if current and current not in SENTINELS:
            # A missing candidate order cannot be shown to be newer.
            # A missing current order means the stored run is no longer
            # placeable (the source retired that etl_run_id). Keeping it
            # would freeze the cursor, so the observed high water replaces it.
            if candidate_order is None:
                _restore_idle(conn, source_system, module)
                return "refused_regression"
            if current_order is not None and candidate_order < current_order:
                _restore_idle(conn, source_system, module)
                return "refused_regression"
            if candidate_order == current_order and candidate_run_id < current:
                _restore_idle(conn, source_system, module)
                return "refused_regression"
        cur.execute(
            """
            INSERT INTO consolidation_cursor
                (source_system, source_module, last_processed_source_run_id, last_processed_at, status)
            VALUES (%s, %s, %s, now(), 'idle')
            ON CONFLICT (source_system, source_module) DO UPDATE SET
                last_processed_source_run_id = EXCLUDED.last_processed_source_run_id,
                last_processed_at = EXCLUDED.last_processed_at,
                status = 'idle'
            """,
            (source_system, module, candidate_run_id),
        )
    return "advanced"


def mark_cursors_failed(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE consolidation_cursor
            SET status = 'failed'
            WHERE status = 'running'
            """
        )


def advance_observed_cursors(conn) -> dict:
    """Move each module cursor to the highest observed source run.

    Source adapters are read-only. A source lookup failure leaves that
    module's cursor where it is.
    """
    results = {}
    for spec in MODULES:
        system = spec["source_system"]
        module = spec["module"]
        key = f"{system}:{module}"
        try:
            observed = known_run_ids(conn, system, spec["source_table"], spec["obs_table"])
            orders = adapter_for(system).run_orders(module, list(observed))
            candidate, candidate_order = select_high_water(observed, orders)
            current_order = None
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT last_processed_source_run_id
                    FROM consolidation_cursor
                    WHERE source_system = %s AND source_module = %s
                    """,
                    (system, module),
                )
                current_row = cur.fetchone()
            if current_row and current_row[0] and current_row[0] not in SENTINELS:
                current_order = orders.get(current_row[0])
                if current_order is None and current_row[0] not in observed:
                    current_order = adapter_for(system).run_orders(module, [current_row[0]]).get(current_row[0])
            results[key] = advance_cursor(
                conn, system, module, candidate, candidate_order, current_order
            )
        except Exception as exc:
            _restore_idle(conn, system, module)
            results[key] = f"skipped:{type(exc).__name__}"
    return results
