"""
V2 source adapter.

Unlike V1, every V2 business table carries its own per-row provenance
columns directly (etl_run_id, fetched_at, source_system, source_endpoint) --
confirmed live across all 12 business tables used here (see
ETL3_SOURCE_COMPATIBILITY_MATRIX.md and etl3/tests/test_v2_adapter.py,
which re-verifies this against the live database). There is no separate run-log
table the way V1 has cctns_v1_etl_run_log; a "run" is discovered directly
from the business table itself by grouping on etl_run_id.

Important asymmetry with V1 (see sources/base.py's module docstring):
etl_run_id is a UUID with no inherent ordering, and the SAME etl_run_id can
appear across multiple tables from one master_etl.py cycle. "Discover new
runs" therefore means "group by etl_run_id, exclude ones already known" --
never "id > last_known_id" the way V1's adapter can reason about its
integer run_log.id.

Media module `file_media_bookkeeping` is included for metadata consolidation.
Live schema carries etl_run_id / fetched_at / created_at / updated_at (verified
2026-10-09). It has no date_modified; list_record_stamps uses updated_at.
Binary downloads remain owned by the V2 media server; ETL-3 never writes V2.
"""
from typing import Optional

from etl3.db import connections
from etl3.sources.base import GapEntry, RecordRef, RunMetadata, SourceAdapter

# module -> primary key column, confirmed live (ETL3_SOURCE_COMPATIBILITY_MATRIX.md)
MODULE_PK = {
    "crimes": "crime_id",
    "accused": "accused_id",
    "persons": "person_id",
    "arrests": "id",
    "chargesheets": "id",
    "charge_sheet_updates": "id",
    "disposal": "id",
    "mo_seizures": "mo_seizure_id",
    "properties": "property_id",
    "fsl_case_property": "case_property_id",
    "interrogation_reports": "interrogation_report_id",
    "hierarchy": "ps_code",
    "file_media_bookkeeping": "id",
}


class V2Adapter(SourceAdapter):
    source_system = "V2"

    def supported_modules(self):
        return list(MODULE_PK.keys())

    def discover_new_runs(self, module: str, known_run_ids: Optional[set] = None) -> list:
        if module not in MODULE_PK:
            raise ValueError(f"Unsupported V2 module: {module!r}")
        known_run_ids = known_run_ids or set()
        conn = connections.get_v2_source_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT etl_run_id, min(fetched_at), max(fetched_at), count(*)
                    FROM {module}
                    WHERE etl_run_id IS NOT NULL
                    GROUP BY etl_run_id
                    ORDER BY max(fetched_at)
                    """
                )
                rows = cur.fetchall()
        finally:
            conn.rollback()
            conn.close()

        runs = []
        for run_id, min_fetched, max_fetched, n in rows:
            run_id = str(run_id)  # psycopg2 returns a uuid.UUID for this column; normalize to str
            if run_id in known_run_ids:
                continue
            runs.append(
                RunMetadata(
                    source_system=self.source_system,
                    source_module=module,
                    source_run_id=run_id,
                    started_at=min_fetched,
                    finished_at=max_fetched,
                    status=None,  # V2 has no run-level status; success is implied by the row existing
                    row_count=n,
                )
            )
        return runs

    def get_changed_records(self, module: str, run_id: str) -> list:
        if module not in MODULE_PK:
            raise ValueError(f"Unsupported V2 module: {module!r}")
        pk_col = MODULE_PK[module]
        conn = connections.get_v2_source_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(f"SELECT {pk_col} FROM {module} WHERE etl_run_id = %s", (run_id,))
                rows = cur.fetchall()
        finally:
            conn.rollback()
            conn.close()
        return [
            RecordRef(
                source_system=self.source_system,
                source_module=module,
                source_run_id=run_id,
                source_record_id=str(r[0]),
                action=None,  # V2 doesn't track insert-vs-update at this grain
            )
            for r in rows
        ]

    def get_source_record(self, module: str, record_id: str) -> Optional[dict]:
        if module not in MODULE_PK:
            raise ValueError(f"Unsupported V2 module: {module!r}")
        pk_col = MODULE_PK[module]
        conn = connections.get_v2_source_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(f"SELECT * FROM {module} WHERE {pk_col} = %s", (record_id,))
                row = cur.fetchone()
                if row is None:
                    return None
                cols = [d[0] for d in cur.description]
                return dict(zip(cols, row))
        finally:
            conn.rollback()
            conn.close()

    def get_all_current_records(self, module: str, batch_size: int = 1000):
        """
        Yields every row currently in this module's table, as dicts, batched
        (not loaded entirely into memory). The baseline/initial-observation
        path -- reads the table directly, not through etl_run_id grouping,
        so it also captures rows with a NULL etl_run_id (e.g. the 24
        placeholder persons rows) that discover_new_runs()/
        get_changed_records() cannot see by construction.
        """
        if module not in MODULE_PK:
            raise ValueError(f"Unsupported V2 module: {module!r}")
        conn = connections.get_v2_source_connection()
        try:
            with conn.cursor(name=f"etl3_v2_scan_{module}") as cur:
                cur.itersize = batch_size
                cur.execute(f"SELECT * FROM {module}")
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
        if module not in MODULE_PK:
            raise ValueError(f"Unsupported V2 module: {module!r}")
        pk_col = MODULE_PK[module]
        conn = connections.get_v2_source_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(f"SELECT {pk_col}::text FROM {module}")
                return [row[0] for row in cur.fetchall()]
        finally:
            conn.rollback()
            conn.close()

    def list_record_stamps(self, module: str) -> list:
        """(pk, modified_at, etl_run_id or None).

        Business tables use date_modified. file_media_bookkeeping uses
        updated_at (no date_modified column — verified live). A missing
        modified column degrades to pk-only rather than dropping the module.
        """
        if module not in MODULE_PK:
            raise ValueError(f"Unsupported V2 module: {module!r}")
        pk_col = MODULE_PK[module]
        modified_col = "updated_at" if module == "file_media_bookkeeping" else "date_modified"
        conn = connections.get_v2_source_connection()
        try:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        f"SELECT {pk_col}::text, {modified_col}, etl_run_id::text FROM {module}"
                    )
                    return [(pk, modified, run_id) for pk, modified, run_id in cur.fetchall()]
            except Exception:
                conn.rollback()
                with conn.cursor() as cur:
                    cur.execute(f"SELECT {pk_col}::text FROM {module}")
                    return [(pk, None, None) for (pk,) in cur.fetchall()]
        finally:
            conn.rollback()
            conn.close()

    def fetch_rows_by_pk(self, module: str, ids: list) -> list:
        if module not in MODULE_PK:
            raise ValueError(f"Unsupported V2 module: {module!r}")
        if not ids:
            return []
        pk_col = MODULE_PK[module]
        conn = connections.get_v2_source_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT * FROM {module} WHERE {pk_col}::text = ANY(%s)",
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
        """{etl_run_id: max(fetched_at)}. UUIDs are not ordered; fetched_at is
        only a display order for the cursor, never the exclusion set."""
        if module not in MODULE_PK or not run_ids:
            return {}
        conn = connections.get_v2_source_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT etl_run_id::text, max(fetched_at)
                    FROM {module}
                    WHERE etl_run_id::text = ANY(%s)
                    GROUP BY etl_run_id
                    """,
                    (list(run_ids),),
                )
                return {run_id: fetched for run_id, fetched in cur.fetchall()}
        finally:
            conn.rollback()
            conn.close()

    def get_source_gap_state(self) -> list:
        conn = connections.get_v2_source_connection()
        gaps = []
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT kind, module_name, count(*), count(*) FILTER (WHERE resolved),
                           max(coalesce(last_attempted_at, updated_at))
                    FROM etl_bookkeeping
                    WHERE kind IN ('fk_retry', 'failure')
                    GROUP BY kind, module_name
                    """
                )
                for kind, module_name, total, resolved, last_at in cur.fetchall():
                    unresolved = total - (resolved or 0)
                    gaps.append(
                        GapEntry(
                            source_system=self.source_system,
                            gap_type="fk_retry_capped" if kind == "fk_retry" else "address_unresolved",
                            gap_key=f"{kind}:{module_name}",
                            first_seen_at=last_at,
                            status="OPEN" if unresolved > 0 else "RESOLVED",
                            source_evidence_table="etl_bookkeeping",
                            detail={"total": total, "resolved": resolved or 0, "unresolved": unresolved},
                        )
                    )

                cur.execute("SELECT count(*) FROM accused WHERE person_id IS NULL")
                unlinked = cur.fetchone()[0]
                gaps.append(
                    GapEntry(
                        source_system=self.source_system,
                        gap_type="unlinked_accused",
                        gap_key="accused.person_id IS NULL",
                        first_seen_at=None,
                        status="OPEN" if unlinked > 0 else "RESOLVED",
                        source_evidence_table="accused",
                        detail={"count": unlinked},
                    )
                )
        finally:
            conn.rollback()
            conn.close()
        return gaps
