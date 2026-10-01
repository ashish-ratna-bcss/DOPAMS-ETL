"""
V1 source adapter.

V1 has no per-row run-id column on any business table -- the only way to
know which run touched which row is the join:

    cctns_v1_etl_run_log (one row per run, per entity, has a status)
         --(run_id, a UUID -- NOT the bigint `id` primary key)-->
    cctns_v1_etl_row_action (one row per record touched by that run)
         --(record_key)-->
    business table (cctns_fir / cctns_accused / cctns_accused_details / cctns_court)

Confirmed live this phase (not assumed from the design docs, and this one
caught a real bug while building it): cctns_v1_etl_run_log has BOTH a
bigint `id` (the PK used throughout this whole project's earlier audits for
"run 54", "run 56", etc.) AND a separate `run_id` UUID column. The join to
cctns_v1_etl_row_action is on `run_id`, not `id` -- they are different
columns with different types. An earlier draft of this adapter used `id`
and failed immediately with a Postgres type error
(`invalid input syntax for type uuid: "28"`) the first time it was run
against live data, which is exactly why `source_run_id` below is `run_id`
(the UUID), not `id`. `id` remains useful for the human-readable "run N"
references used elsewhere in this project's prior audits, but it is not
what ETL-3 uses to look up what a run touched.

Also confirmed live this phase:
  - cctns_v1_etl_run_log.entity values: accused, accused_details, court, fir
  - cctns_v1_etl_row_action.action values: insert, update (no delete -- V1
    never deletes, confirmed across this whole project)
  - business table PKs: cctns_fir.fir_reg_num, cctns_accused.accused_id,
    cctns_accused_details.accused_id, cctns_court.court_id

Only runs whose status is a success state are considered discoverable --
extract_failed/extract_partial_failed runs did not durably commit data, so
treating them as a source of "changed records" would be wrong.
"""
from typing import Optional

from etl3.db import connections
from etl3.sources.base import GapEntry, RecordRef, RunMetadata, SourceAdapter

SUCCESS_STATUSES = ("loaded", "loaded_with_known_gaps")

MODULE_TABLE = {
    "fir": ("cctns_fir", "fir_reg_num"),
    "accused": ("cctns_accused", "accused_id"),
    "accused_details": ("cctns_accused_details", "accused_id"),
    "court": ("cctns_court", "court_id"),
}


class V1Adapter(SourceAdapter):
    source_system = "V1"

    def supported_modules(self):
        return list(MODULE_TABLE.keys())

    def discover_new_runs(self, module: str, known_run_ids: Optional[set] = None) -> list:
        if module not in MODULE_TABLE:
            raise ValueError(f"Unsupported V1 module: {module!r}")
        known_run_ids = known_run_ids or set()
        conn = connections.get_v1_source_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT run_id, id, started_at, finished_at, status,
                           rows_fetched, rows_inserted, rows_updated
                    FROM cctns.cctns_v1_etl_run_log
                    WHERE entity = %s AND status = ANY(%s)
                    ORDER BY id
                    """,
                    (module, list(SUCCESS_STATUSES)),
                )
                rows = cur.fetchall()
        finally:
            conn.rollback()
            conn.close()

        runs = []
        for run_id, run_log_id, started_at, finished_at, status, fetched, ins, upd in rows:
            if str(run_id) in known_run_ids:
                continue
            runs.append(
                RunMetadata(
                    source_system=self.source_system,
                    source_module=module,
                    source_run_id=str(run_id),  # the UUID -- what row_action joins on
                    started_at=started_at,
                    finished_at=finished_at,
                    status=status,
                    row_count=(ins or 0) + (upd or 0) if ins is not None else fetched,
                )
            )
        return runs

    def get_changed_records(self, module: str, run_id: str) -> list:
        if module not in MODULE_TABLE:
            raise ValueError(f"Unsupported V1 module: {module!r}")
        table_name, _ = MODULE_TABLE[module]
        conn = connections.get_v1_source_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT record_key, action
                    FROM cctns.cctns_v1_etl_row_action
                    WHERE run_id = %s AND table_name = %s
                    """,
                    (run_id, table_name),
                )
                rows = cur.fetchall()
        finally:
            conn.rollback()
            conn.close()
        return [
            RecordRef(
                source_system=self.source_system,
                source_module=module,
                source_run_id=str(run_id),
                source_record_id=record_key,
                action=action,
            )
            for record_key, action in rows
        ]

    def get_source_record(self, module: str, record_id: str) -> Optional[dict]:
        """
        Fetch one record by the identifier get_changed_records() handed back
        as source_record_id.

        IMPORTANT, found while testing this live: cctns_v1_etl_row_action's
        record_key is NOT the literal primary key for every module. Confirmed
        by direct inspection:
          - 'fir':  record_key IS fir_reg_num exactly (0 mismatches against
            every row currently in cctns_fir -- verified by anti-join).
          - 'court', 'accused', 'accused_details': record_key is a
            pipe-delimited composite of that module's natural-key fields
            (fir_reg_num first, then other fields that vary per module),
            NOT the module's actual PK (court_id / accused_id). Example
            actually observed: 'court' record_key =
            '2044001210144|2022-01-29T...|...|CC-509/2022|...'.

        This means a record_key from get_changed_records() can be resolved
        straight back to a row ONLY for 'fir'. For the other three modules,
        this method deliberately raises rather than guessing at a resolution
        -- V1's own natural-key churn (the same issue documented throughout
        ETL3_MERGER_IMPLEMENTATION_PLAN.md section 10) means a composite
        natural-key string does not map 1:1 to a single current accused_id/
        court_id anyway. Resolving this correctly is Phase 3's job (the
        source-observation layer), which will correlate via the fir_reg_num
        prefix and read that FIR's full current row set rather than trying
        to pinpoint one historical record_key -- not invented here.
        """
        if module not in MODULE_TABLE:
            raise ValueError(f"Unsupported V1 module: {module!r}")
        if module != "fir":
            raise NotImplementedError(
                f"get_source_record('{module}', ...) is not supported: "
                "cctns_v1_etl_row_action.record_key for this module is a "
                "composite natural-key string, not the table's primary key "
                "(confirmed live; see this method's docstring). Resolving a "
                "specific record_key to a current row for this module is "
                "deferred to Phase 3's source-observation design."
            )
        table_name, pk_col = MODULE_TABLE[module]
        conn = connections.get_v1_source_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(f"SELECT * FROM cctns.{table_name} WHERE {pk_col} = %s", (record_id,))
                row = cur.fetchone()
                if row is None:
                    return None
                cols = [d[0] for d in cur.description]
                return dict(zip(cols, row))
        finally:
            conn.rollback()
            conn.close()

    def get_source_gap_state(self) -> list:
        conn = connections.get_v1_source_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT entity, window_start, window_end, error, status,
                           first_seen_at, attempt_count
                    FROM cctns.cctns_v1_failed_fetch_window
                    """
                )
                rows = cur.fetchall()
        finally:
            conn.rollback()
            conn.close()
        return [
            GapEntry(
                source_system=self.source_system,
                gap_type="ora_06502_window",
                gap_key=f"{entity}:{window_start}:{window_end}",
                first_seen_at=first_seen_at,
                status=status,
                source_evidence_table="cctns_v1_failed_fetch_window",
                detail={"entity": entity, "window_start": str(window_start),
                        "window_end": str(window_end), "error": error,
                        "attempt_count": attempt_count},
            )
            for entity, window_start, window_end, error, status, first_seen_at, attempt_count in rows
        ]
