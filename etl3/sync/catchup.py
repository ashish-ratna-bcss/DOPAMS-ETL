"""Capture current source rows the run-id scan cannot see.

Two holes:
  * a primary key with no observation at all (late row, null etl_run_id)
  * a primary key whose source modified time moved forward while the
    etl_run_id stayed one we already stored (ON CONFLICT would drop it)

The second case is written as a new observation whose run id keeps the
source run id and appends the source's own modified instant. Nothing
here invents a timestamp. A second pass sees the same instant and
inserts nothing.
"""
from etl3.loaders import v1_observations as v1obs
from etl3.loaders import v2_observations as v2obs
from etl3.merger.current_state import as_aware
from etl3.sync.catalog import MODULES, adapter_for


def revision_run_id(base: str, modified) -> str:
    stamp = as_aware(modified)
    if stamp is None or not base:
        return None
    text = stamp.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    token = f"{base}#m:{text}"
    if len(token) <= 100:
        return token
    return token[-100:]


def _observed(conn, spec):
    """record_id -> latest modified, plus the set of (record_id, run_id)."""
    latest = {}
    pairs = set()
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT source_record_id, source_run_id, source_modified_at
            FROM {spec['obs_table']}
            WHERE source_system = %s AND source_table = %s
            """,
            (spec["source_system"], spec["source_table"]),
        )
        for record_id, run_id, modified in cur.fetchall():
            pairs.add((record_id, run_id))
            aware = as_aware(modified)
            prev = latest.get(record_id, None)
            if record_id not in latest or (aware is not None and (prev is None or aware > prev)):
                latest[record_id] = aware
    return latest, pairs


def _choose_run_id(spec, row, modified, pairs, record_id):
    if spec["source_system"] == "V1":
        real = v1obs.INITIAL_RUN_MARKER
    else:
        real = str(row.get("etl_run_id")) if row.get("etl_run_id") else "__initial_no_run_id__"
    if (record_id, real) not in pairs:
        return real
    return revision_run_id(real, modified)


def catch_up_module(
    conn,
    spec: dict,
    consolidation_run_id: str,
    batch_size: int = 500,
    commit_every_batches: int = 0,
) -> dict:
    """Capture missing/stale source rows into observation tables.

    ``commit_every_batches`` > 0 commits after that many batches (used for
    large media catch-up so a crash does not lose the whole module). Default
    0 preserves the original single-transaction behaviour.
    """
    adapter = adapter_for(spec["source_system"])
    stamps = adapter.list_record_stamps(spec["module"])
    latest, pairs = _observed(conn, spec)
    needed = []
    for pk, modified, _run in stamps:
        prev = latest.get(pk, None)
        modified_aware = as_aware(modified)
        missing = pk not in latest
        stale = (
            not missing
            and modified_aware is not None
            and (prev is None or modified_aware > prev)
        )
        if missing or stale:
            needed.append(pk)

    inserted = replayed = 0
    batches_since_commit = 0
    total_batches = (len(needed) + batch_size - 1) // batch_size if needed else 0
    for batch_idx, start in enumerate(range(0, len(needed), batch_size), 1):
        if total_batches and (batch_idx == 1 or batch_idx % 10 == 0 or batch_idx == total_batches):
            print(
                f"  catchup {spec['source_system']}/{spec['module']} "
                f"batch {batch_idx}/{total_batches} inserted={inserted}",
                flush=True,
            )
        rows = adapter.fetch_rows_by_pk(spec["module"], needed[start:start + batch_size])
        for row in rows:
            if spec["source_system"] == "V1":
                record_id = v1obs._record_id_for(spec["module"], row)
                modified = row.get("updated_at")
            else:
                from etl3.sources.v2.adapter import MODULE_PK
                record_id = str(row[MODULE_PK[spec["module"]]])
                # Media bookkeeping uses updated_at; business tables use date_modified.
                modified = row.get("date_modified") or row.get("updated_at")
            run_id = _choose_run_id(spec, row, modified, pairs, record_id)
            if not run_id:
                continue
            if spec["source_system"] == "V1":
                wrote = v1obs._write_row(conn, spec["module"], row, run_id, consolidation_run_id)
            else:
                wrote = v2obs._write_row(conn, spec["module"], row, run_id, consolidation_run_id)
            pairs.add((record_id, run_id))
            if wrote:
                inserted += 1
            else:
                replayed += 1
        batches_since_commit += 1
        if commit_every_batches and batches_since_commit >= commit_every_batches:
            conn.commit()
            batches_since_commit = 0
    if spec["module"] == "persons" and inserted:
        v2obs.record_placeholder_persons_gap(conn, inserted)
    return {
        "module": spec["module"],
        "source_system": spec["source_system"],
        "source_count": len(stamps),
        "missing": len(needed),
        "inserted": inserted,
        "already_present": replayed,
    }


def catch_up_all(conn, consolidation_run_id: str) -> list:
    return [catch_up_module(conn, spec, consolidation_run_id) for spec in MODULES]
