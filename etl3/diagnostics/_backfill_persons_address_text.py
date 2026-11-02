"""Verify-before-commit backfill for the persons_unified V2 address-text
state-dropping bug (see the fix to etl3.merger.field_maps.compose_v2_address_text).

Root cause: persons_unified.present_address_text / permanent_address_text
for V2 persons were built as a single-field OR-chain:
    p.get("present_house_no") or p.get("present_locality_village") or p.get("present_district")
which returns the FIRST non-null field and never falls through to
present_state_ut / present_area_mandal / present_country. Any person whose
source only captured district+state_ut (no house_no/locality) ends up with
present_address_text == just the district, silently dropping the state --
and persons_unified.present_address_text is exposed verbatim through
be_read.persons (etl3/migrations/006_backend_read_contract.sql), so this
reaches whatever reads that view.

compose_v2_address_text() now composes every available structured level
(locality, mandal, district, state_ut, country) when house_no is absent,
instead of stopping at the first one. This script fixes the rows already
written before that fix landed. It touches ONLY present_address_text and
permanent_address_text on persons_unified:
  - No other column on an affected row is written.
  - No row outside the affected set (source_system='V2' only) is written.
  - No row is inserted or deleted.
  - persons_source is read-only, never written.

Safety design (same verify-before-commit pattern as
_backfill_address_dedup.py / _reprocess_nationality_fix_verified.py):
  1. Snapshot the FULL persons_unified table BEFORE, on a read-only
     connection (columns discovered from information_schema).
  2. Fetch the latest persons_source payload per source_record_id for V2,
     using the exact same "latest observation" query as the live merger
     (current_state.fetch_latest_by_record_id), so this never drifts from
     what a live consolidation run would compute.
  3. For every V2 persons_unified row, recompute both address-text columns
     from that payload via field_maps.compose_v2_address_text (the SAME
     function wired into field_maps.PERSONS["V2"]["map"], not a
     reimplementation) and compare to the stored values. Rows where either
     column differs are "affected".
  4. Pre-write validation: every computed replacement must be produced by
     calling the live field_maps.PERSONS["V2"]["map"] lambdas directly
     (cross-checked against the bare function) and must be idempotent
     (recomposing from the same payload twice gives the same string).
  5. Apply a targeted guarded UPDATE per affected row -- WHERE matches BOTH
     old column values, so a row changed by anything else since our
     snapshot (e.g. a live consolidation run) is skipped, not clobbered.
  6. Re-read the FULL table from the SAME (still open, uncommitted)
     transaction and verify every affected row matches expected, every
     other column on it is unchanged, every row NOT in the affected set
     (including all V1 rows) is byte-identical to before. Any surprise
     rolls back -- nothing is kept.
  7. Commit only with --commit; default is a dry run (report + rollback).

Run (dry run, always safe):
    python etl3/diagnostics/_backfill_persons_address_text.py

Run (apply, after reviewing the dry-run report):
    python etl3/diagnostics/_backfill_persons_address_text.py --commit
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.db import connections
from etl3.merger import field_maps
from etl3.merger.field_maps import compose_v2_address_text

TABLE = "persons_unified"
PK = "person_id"
SOURCE_TABLE = "persons_source"

# The exact person cited in the original report, for an explicit spot-check.
SPOTLIGHT_PERSON_ID = "678b8bfcaf14634f5773e97a"


def _columns(conn, table):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position
            """,
            (table,),
        )
        return [row[0] for row in cur.fetchall()]


def snapshot(conn, columns):
    with conn.cursor() as cur:
        cur.execute(f"SELECT {', '.join(columns)} FROM {TABLE}")
        return {row[0]: dict(zip(columns, row)) for row in cur.fetchall()}


def fetch_latest_v2_payloads(conn):
    """Mirrors current_state.fetch_latest_by_record_id exactly, so the
    backfill never computes a different 'current' payload than a live
    consolidation run would."""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT DISTINCT ON (source_record_id)
                source_record_id, payload
            FROM {SOURCE_TABLE}
            WHERE source_system = 'V2'
            ORDER BY source_record_id,
                     CASE
                         WHEN COALESCE(source_modified_at, source_created_at) > now() + interval '1 day'
                         THEN NULL
                         ELSE COALESCE(source_modified_at, source_created_at)
                     END DESC NULLS LAST,
                     id DESC
            """
        )
        return {row[0]: row[1] for row in cur.fetchall()}


def main():
    commit = "--commit" in sys.argv[1:]

    print("== Snapshotting BEFORE state (read-only connection) ==")
    ro_conn = connections.get_unified_connection(readonly=True)
    columns = _columns(ro_conn, TABLE)
    assert PK in columns and "present_address_text" in columns and "permanent_address_text" in columns
    before = snapshot(ro_conn, columns)
    payloads = fetch_latest_v2_payloads(ro_conn)
    ro_conn.close()
    print(f"  {len(before)} {TABLE} rows snapshotted, columns = {columns}")
    print(f"  {len(payloads)} latest V2 {SOURCE_TABLE} payloads fetched")

    v2_map = field_maps.PERSONS["V2"]["map"]
    affected = []  # (person_id, old_present, new_present, old_permanent, new_permanent)
    missing_payload = 0
    for person_id, row in before.items():
        if row.get("source_system") != "V2":
            continue
        payload = payloads.get(row.get("source_record_id"))
        if payload is None:
            missing_payload += 1
            continue
        old_present = row.get("present_address_text")
        old_permanent = row.get("permanent_address_text")
        new_present = v2_map["present_address_text"](payload)
        new_permanent = v2_map["permanent_address_text"](payload)
        if new_present != old_present or new_permanent != old_permanent:
            affected.append((person_id, old_present, new_present, old_permanent, new_permanent))

    total_v2 = sum(1 for r in before.values() if r.get("source_system") == "V2")
    print(f"\n  total V2 {TABLE} rows: {total_v2}")
    print(f"  rows with no matching source payload (should be 0): {missing_payload}")
    print(f"  affected rows (present and/or permanent address text changes): {len(affected)}")

    spotlight = next((a for a in affected if a[0] == SPOTLIGHT_PERSON_ID), None)
    if spotlight:
        print(f"\n  spot-check {SPOTLIGHT_PERSON_ID} (the originally reported case):")
        print(f"    present   before={spotlight[1]!r} after={spotlight[2]!r}")
        print(f"    permanent before={spotlight[3]!r} after={spotlight[4]!r}")

    for person_id, old_p, new_p, old_m, new_m in affected[:10]:
        print(f"    {person_id}:")
        if old_p != new_p:
            print(f"      present  : {old_p!r} -> {new_p!r}")
        if old_m != new_m:
            print(f"      permanent: {old_m!r} -> {new_m!r}")

    # Pre-write validation: every computed value must come from the live
    # map (not drift from compose_v2_address_text directly) and must be
    # idempotent -- recomposing from the same payload must reproduce it.
    bad = []
    for person_id, old_p, new_p, old_m, new_m in affected:
        payload = payloads[before[person_id]["source_record_id"]]
        if compose_v2_address_text(payload, "present") != new_p:
            bad.append((person_id, "present value disagrees with bare compose function"))
        if compose_v2_address_text(payload, "permanent") != new_m:
            bad.append((person_id, "permanent value disagrees with bare compose function"))
        if v2_map["present_address_text"](payload) != new_p:
            bad.append((person_id, "present value not idempotent on re-apply"))
        if v2_map["permanent_address_text"](payload) != new_m:
            bad.append((person_id, "permanent value not idempotent on re-apply"))
    if bad:
        print(f"\n==> ABORTING -- {len(bad)} computed replacement(s) failed pre-write validation:")
        for person_id, reason in bad[:10]:
            print(f"    {person_id}: {reason}")
        raise SystemExit(1)

    if not commit:
        print("\n==> DRY RUN only (no --commit). No connection to the write DB was opened.")
        return

    if not affected:
        print("\n==> Nothing to commit (zero affected rows).")
        return

    print(f"\n== Opening write connection, applying {len(affected)} guarded UPDATEs ==")
    conn = connections.get_unified_connection()
    with conn.cursor() as cur:
        updated = []
        skipped_concurrent = []
        for person_id, old_p, new_p, old_m, new_m in affected:
            cur.execute(
                f"""
                UPDATE {TABLE}
                SET present_address_text = %s, permanent_address_text = %s
                WHERE {PK} = %s
                  AND present_address_text IS NOT DISTINCT FROM %s
                  AND permanent_address_text IS NOT DISTINCT FROM %s
                """,
                (new_p, new_m, person_id, old_p, old_m),
            )
            if cur.rowcount == 1:
                updated.append((person_id, old_p, new_p, old_m, new_m))
            else:
                skipped_concurrent.append(person_id)

    print(f"  updated: {len(updated)}")
    if skipped_concurrent:
        print(f"  skipped (row changed since snapshot -- not overwritten): {len(skipped_concurrent)}")
        for person_id in skipped_concurrent[:10]:
            print(f"    {person_id}")

    print("\n== Re-reading AFTER state from the SAME (still uncommitted) transaction ==")
    after = snapshot(conn, columns)

    other_columns = [c for c in columns if c not in ("present_address_text", "permanent_address_text")]
    violations = []

    updated_ids = {person_id for person_id, *_ in updated}
    for person_id, old_p, new_p, old_m, new_m in updated:
        actual = after.get(person_id)
        if actual is None:
            violations.append((person_id, "row disappeared"))
            continue
        if actual.get("present_address_text") != new_p:
            violations.append((person_id, f"present_address_text = {actual.get('present_address_text')!r}, expected {new_p!r}"))
        if actual.get("permanent_address_text") != new_m:
            violations.append((person_id, f"permanent_address_text = {actual.get('permanent_address_text')!r}, expected {new_m!r}"))
        for col in other_columns:
            if actual.get(col) != before[person_id].get(col):
                violations.append((person_id, f"unrelated column {col!r} changed: "
                                               f"{before[person_id].get(col)!r} -> {actual.get(col)!r}"))

    checked = 0
    for person_id, row in before.items():
        if person_id in updated_ids:
            continue
        checked += 1
        actual = after.get(person_id)
        if actual != row:
            violations.append((person_id, "untouched row changed", actual, row))

    if violations:
        print(f"\n==> ROLLING BACK -- verification failed: {len(violations)} violation(s).")
        for v in violations[:20]:
            print(f"    {v}")
        conn.rollback()
        conn.close()
        raise SystemExit(1)

    print(f"\n==> Verified: {len(updated)} rows updated (address text only), "
          f"{checked} other rows byte-identical to before, "
          f"{len(skipped_concurrent)} skipped due to a concurrent change. Committing.")
    conn.commit()
    conn.close()
    print("COMMITTED.")


if __name__ == "__main__":
    main()
