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

        Phase 2 found record_key is the literal PK only for 'fir'. Phase 3
        investigated the other three modules to the root cause (not just the
        symptom), by reading the actual V1 ETL source code
        (db/natural_key.py, db/upsert.py) alongside the live DB triggers
        (pg_get_functiondef on trg_cctns_court_natural_key /
        trg_cctns_accused_details_natural_key):

          - For 'court' and 'accused_details', the DB trigger computes
            natural_key as the SAME pipe-delimited field list, in the SAME
            order, as Python's record_key() -- so in principle record_key
            should equal natural_key exactly. Empirically it does NOT, most
            of the time (confirmed: only 250/7,536 court entries and
            2,245/20,227 accused_details entries resolve via exact
            natural_key match, across this table's full row_action history).
            Root cause: Python's record_key() runs on the RAW API response
            dict (date fields as ISO8601 strings like
            '2022-01-29T18:30:00.000+00:00'), while the DB trigger runs on
            the ALREADY-TYPED, ALREADY-INSERTED row and casts timestamptz
            columns with `::text` (Postgres's own default rendering, e.g.
            '2022-01-29 18:30:00+00') -- two different text representations
            of the same instant, so the strings (and therefore an exact
            match) diverge. This is a pre-existing property of V1's own ETL
            code, not something ETL-3 introduced or can fix (V1 must not be
            modified).
          - For 'accused', record_key IS already an MD5 hash (computed in
            Python), and the DB trigger ALSO computes an MD5 hash -- but
            over data that went through the same raw-API-vs-cast-column
            divergence above, so the two hashes usually differ too.
            Confirmed even on the most recent run that actually inserted
            accused rows: only 7 of 82 record_keys match their row's current
            natural_key exactly.

        Resolution strategy, chosen because it is exact and provably
        correct, not because it is convenient:
          - 'fir': record_key IS the PK. Direct lookup (as before).
          - 'accused': try an exact natural_key match first (precise when it
            works). If it doesn't match, this observation genuinely CANNOT
            be resolved to a specific current row via record_key alone --
            returns None. Callers (the observation writer) must treat None
            as "unresolved," log it explicitly, and NOT guess -- not treat
            it as "record no longer exists."
          - 'court' / 'accused_details': use get_records_for_fir() instead
            of this method -- see its docstring for why single-record
            resolution isn't the right granularity for these two modules.
        """
        if module not in MODULE_TABLE:
            raise ValueError(f"Unsupported V1 module: {module!r}")
        if module in ("court", "accused_details"):
            raise NotImplementedError(
                f"get_source_record('{module}', ...) is not the right method -- "
                "use get_records_for_fir() instead; see this method's docstring "
                "for why single-record_key resolution isn't reliable for this module."
            )
        table_name, pk_col = MODULE_TABLE[module]
        conn = connections.get_v1_source_connection()
        try:
            with conn.cursor() as cur:
                if module == "accused":
                    cur.execute(
                        f"SELECT * FROM cctns.{table_name} WHERE natural_key = %s",
                        (record_id,),
                    )
                else:  # fir
                    cur.execute(f"SELECT * FROM cctns.{table_name} WHERE {pk_col} = %s", (record_id,))
                row = cur.fetchone()
                if row is None:
                    return None
                cols = [d[0] for d in cur.description]
                return dict(zip(cols, row))
        finally:
            conn.rollback()
            conn.close()

    def extract_fir_reg_num(self, module: str, record_key: str) -> Optional[str]:
        """
        Every record_key -- for every module, in every historical format
        found this phase -- starts with fir_reg_num (verified empirically
        this phase: splitting on '|' and checking the first segment against
        live cctns_fir resolved 100% of 7,536 court, 20,227 accused_details,
        and 17,116 historical-format accused row_action entries -- an
        anti-join found zero exceptions). The one case this does NOT work
        for is 'accused' entries already in the current MD5-hash record_key
        format (no '|' present at all) -- those have no extractable
        fir_reg_num and must go through get_source_record()'s natural_key
        match instead.
        """
        if module not in MODULE_TABLE:
            raise ValueError(f"Unsupported V1 module: {module!r}")
        if "|" not in record_key:
            return record_key if module == "fir" else None
        return record_key.split("|", 1)[0] or None

    def get_records_by_ids(self, module: str, ids: list) -> dict:
        """Batch version of get_source_record for 'fir'/'accused' -- one
        query instead of one connection+query per id. Returns {id: row}."""
        if module not in ("fir", "accused"):
            raise ValueError(f"get_records_by_ids is only for fir/accused, got {module!r}")
        if not ids:
            return {}
        table_name, pk_col = MODULE_TABLE[module]
        key_col = "natural_key" if module == "accused" else pk_col
        conn = connections.get_v1_source_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(f"SELECT * FROM cctns.{table_name} WHERE {key_col} = ANY(%s)", (list(set(ids)),))
                rows = cur.fetchall()
                if not rows:
                    return {}
                cols = [d[0] for d in cur.description]
                key_idx = cols.index(key_col)
                return {row[key_idx]: dict(zip(cols, row)) for row in rows}
        finally:
            conn.rollback()
            conn.close()

    def get_records_for_firs(self, module: str, fir_reg_nums: list) -> dict:
        """Batch version of get_records_for_fir -- one query instead of one
        connection+query per FIR. Returns {fir_reg_num: [row, row, ...]}."""
        if module not in ("court", "accused_details"):
            raise ValueError(f"get_records_for_firs is only for court/accused_details, got {module!r}")
        if not fir_reg_nums:
            return {}
        table_name, _ = MODULE_TABLE[module]
        conn = connections.get_v1_source_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT * FROM cctns.{table_name} WHERE fir_reg_num = ANY(%s)",
                    (list(set(fir_reg_nums)),),
                )
                rows = cur.fetchall()
                if not rows:
                    return {}
                cols = [d[0] for d in cur.description]
                fir_idx = cols.index("fir_reg_num")
                out = {}
                for row in rows:
                    d = dict(zip(cols, row))
                    out.setdefault(row[fir_idx], []).append(d)
                return out
        finally:
            conn.rollback()
            conn.close()

    def get_records_for_fir(self, module: str, fir_reg_num: str) -> list:
        """
        For 'court' and 'accused_details': return every CURRENT row for this
        FIR, rather than trying to pinpoint the single row a historical
        record_key referred to (see get_source_record()'s docstring for why
        that single-record resolution is not reliable for these modules).
        This is a deliberate observation-granularity decision -- "something
        about this FIR's court/accused_details rows changed in this run" --
        not an attempt to disguise an unresolved lookup as a resolved one.
        """
        if module not in ("court", "accused_details"):
            raise ValueError(f"get_records_for_fir is only for court/accused_details, got {module!r}")
        table_name, _ = MODULE_TABLE[module]
        conn = connections.get_v1_source_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(f"SELECT * FROM cctns.{table_name} WHERE fir_reg_num = %s", (fir_reg_num,))
                rows = cur.fetchall()
                if not rows:
                    return []
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in rows]
        finally:
            conn.rollback()
            conn.close()

    def get_all_current_records(self, module: str, batch_size: int = 1000):
        """
        Yields every row currently in this module's business table, as
        dicts, batched (not loaded entirely into memory). This is the
        baseline/initial-observation path -- it reads the table directly and
        does NOT go through cctns_v1_etl_run_log/cctns_v1_etl_row_action at
        all, so none of get_source_record()'s record_key-resolution
        limitations apply here: every row's own current natural_key/PK is
        used directly, exactly as stored.
        """
        if module not in MODULE_TABLE:
            raise ValueError(f"Unsupported V1 module: {module!r}")
        table_name, _ = MODULE_TABLE[module]
        conn = connections.get_v1_source_connection()
        try:
            with conn.cursor(name=f"etl3_v1_scan_{module}") as cur:
                cur.itersize = batch_size
                cur.execute(f"SELECT * FROM cctns.{table_name}")
                cols = None
                while True:
                    batch = cur.fetchmany(batch_size)
                    if not batch:
                        break
                    if cols is None:
                        cols = [d[0] for d in cur.description]
                    for row in batch:
                        yield dict(zip(cols, row))
        finally:
            conn.rollback()
            conn.close()

    def list_record_ids(self, module: str) -> list:
        """Current primary keys as text. Read-only. Used to find rows the
        observation layer has not captured yet."""
        if module not in MODULE_TABLE:
            raise ValueError(f"Unsupported V1 module: {module!r}")
        table_name, pk_col = MODULE_TABLE[module]
        conn = connections.get_v1_source_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(f"SELECT {pk_col}::text FROM cctns.{table_name}")
                return [row[0] for row in cur.fetchall()]
        finally:
            conn.rollback()
            conn.close()

    def list_record_stamps(self, module: str) -> list:
        """(pk, updated_at, None). The third slot is the source run id, which
        V1 business rows do not carry. Falls back to pk-only if updated_at
        is absent so catch-up still sees missing keys."""
        if module not in MODULE_TABLE:
            raise ValueError(f"Unsupported V1 module: {module!r}")
        table_name, pk_col = MODULE_TABLE[module]
        conn = connections.get_v1_source_connection()
        try:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        f"SELECT {pk_col}::text, updated_at FROM cctns.{table_name}"
                    )
                    return [(pk, modified, None) for pk, modified in cur.fetchall()]
            except Exception:
                conn.rollback()
                with conn.cursor() as cur:
                    cur.execute(f"SELECT {pk_col}::text FROM cctns.{table_name}")
                    return [(pk, None, None) for (pk,) in cur.fetchall()]
        finally:
            conn.rollback()
            conn.close()

    def fetch_rows_by_pk(self, module: str, ids: list) -> list:
        if module not in MODULE_TABLE:
            raise ValueError(f"Unsupported V1 module: {module!r}")
        if not ids:
            return []
        table_name, pk_col = MODULE_TABLE[module]
        conn = connections.get_v1_source_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT * FROM cctns.{table_name} WHERE {pk_col}::text = ANY(%s)",
                    (list(ids),),
                )
                rows = cur.fetchall()
                if not rows:
                    return []
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in rows]
        finally:
            conn.rollback()
            conn.close()

    def run_orders(self, module: str, run_ids: list) -> dict:
        """{run_uuid: bigint run_log.id} for successful runs of this module.
        The bigint id is the only monotonic order V1 exposes."""
        if not run_ids:
            return {}
        conn = connections.get_v1_source_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT run_id::text, id
                    FROM cctns.cctns_v1_etl_run_log
                    WHERE entity = %s AND run_id::text = ANY(%s)
                    """,
                    (module, list(run_ids)),
                )
                return {run_id: log_id for run_id, log_id in cur.fetchall()}
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
