"""Verify-before-commit backfill for the accused_enrichment.address
duplicate-segment bug (see commit introducing
etl3.enrichment.accused_facts.normalize_address_segments).

Root cause: _compose_address() joined 8 structured person fields with no
dedup, and some CCTNS V1/V2 source records hold the same free-text street
address in more than one of those fields (e.g. house_no and ward_colony
both holding "14-1-61, Sitarampet, Gode ki KabarMangalhat, Hyderabad", one
tagged "R/o" and one bare). Some V1 dossier rows also hold a
self-duplicated block inside a single free-text field already, upstream of
anything this codebase composes. normalize_address_segments() now collapses
any repeated run of 2+ comma-separated segments (ignoring a leading R/o-
style tag), and is applied at every place accused_enrichment.address is
built (see etl3/enrichment/accused_facts.py and etl3/enrichment/runner.py).

This script fixes the rows already written before that fix landed. It
touches ONLY the `address` column of `accused_enrichment`:
  - No other column on an affected row is written.
  - No row outside the affected set is written.
  - No row is inserted or deleted.
  - No source table (persons_source, accused_source, persons_unified,
    accused_unified, ...) is read for writing, and none is written at all.

Safety design (same verify-before-commit pattern as
_reprocess_nationality_fix_verified.py):
  1. Snapshot every accused_enrichment row's FULL column set BEFORE, on a
     read-only connection (columns discovered from information_schema, so
     this never silently misses a column added by a later migration).
  2. Open a write connection. For every row with a non-null address, compute
     normalize_address_segments(old_address). Rows where the result differs
     from the stored value are "affected".
  3. Apply a targeted `UPDATE ... SET address = %s WHERE accused_id = %s
     AND address = %s` per affected row -- the old value in the WHERE
     clause is an optimistic-concurrency guard: if some other process (a
     live ETL-3 run) changed that row between our snapshot and our write,
     the guarded UPDATE touches 0 rows and we skip it rather than
     clobbering a newer value, and report it separately.
  4. Re-read the FULL table from the SAME (still open, uncommitted)
     transaction and verify:
       a. every affected row's address now equals the expected new value,
          and every other column on that row is byte-identical to before;
       b. every row NOT in the affected set is byte-identical to before,
          with no sampling -- the whole table is re-compared;
       c. re-running normalize_address_segments on every new address value
          is a no-op (idempotent) and finds no remaining 2+-segment repeat.
     Any surprise rolls back -- nothing is kept.
  5. Commit only with --commit; default is a dry run (report + rollback).

Run (dry run, always safe):
    python etl3/diagnostics/_backfill_address_dedup.py

Run (apply, after reviewing the dry-run report):
    python etl3/diagnostics/_backfill_address_dedup.py --commit
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.db import connections
from etl3.enrichment.accused_facts import normalize_address_segments

TABLE = "accused_enrichment"
PK = "accused_id"


def _columns(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position
            """,
            (TABLE,),
        )
        return [row[0] for row in cur.fetchall()]


def snapshot(conn, columns):
    with conn.cursor() as cur:
        cur.execute(f"SELECT {', '.join(columns)} FROM {TABLE}")
        return {row[0]: dict(zip(columns, row)) for row in cur.fetchall()}


def _has_repeated_segment(text, min_run=2):
    """Independent re-check: does this address still contain a repeated
    run of >=min_run segments? Mirrors normalize_address_segments' own
    duplicate-finding loop so the verification is not just "trust the
    function that wrote it"."""
    if not text:
        return False
    from etl3.enrichment.accused_facts import _normalize_address_segment_key

    segments = [s.strip() for s in str(text).split(",") if s.strip()]
    keys = [_normalize_address_segment_key(s) for s in segments]
    n = len(keys)
    for size in range(min_run, n // 2 + 1):
        for start in range(0, n - size):
            window = keys[start:start + size]
            for start2 in range(start + size, n - size + 1):
                if keys[start2:start2 + size] == window:
                    return True
    return False


def main():
    commit = "--commit" in sys.argv[1:]

    print("== Snapshotting BEFORE state (read-only connection) ==")
    ro_conn = connections.get_unified_connection(readonly=True)
    columns = _columns(ro_conn)
    assert PK in columns and "address" in columns
    before = snapshot(ro_conn, columns)
    ro_conn.close()
    print(f"  {len(before)} {TABLE} rows snapshotted, columns = {columns}")

    affected = []  # (accused_id, old_address, new_address)
    cosmetic_only_skipped = 0
    for accused_id, row in before.items():
        old = row.get("address")
        if not old:
            continue
        if not _has_repeated_segment(old):
            continue  # no actual duplicate segment -- never touched, even if
            # normalize_address_segments would also reformat comma spacing.
        new = normalize_address_segments(old)
        if new != old:
            affected.append((accused_id, old, new))
        else:
            cosmetic_only_skipped += 1

    print(f"\n  rows with non-null address : {sum(1 for r in before.values() if r.get('address'))}")
    print(f"  affected rows (genuine repeated segment found): {len(affected)}")
    print(f"  cosmetic-only skipped (should be 0): {cosmetic_only_skipped}")
    for accused_id, old, new in affected[:10]:
        print(f"    {accused_id}:")
        print(f"      before: {old}")
        print(f"      after : {new}")

    # Sanity: the computed replacement must never itself contain a
    # repeated segment, and must be a no-op under a second pass.
    bad_candidates = []
    for accused_id, old, new in affected:
        if _has_repeated_segment(new):
            bad_candidates.append((accused_id, "new value still has a repeated segment", new))
        elif normalize_address_segments(new) != new:
            bad_candidates.append((accused_id, "not idempotent", new))
    if bad_candidates:
        print(f"\n==> ABORTING -- {len(bad_candidates)} computed replacement(s) failed pre-write validation:")
        for accused_id, reason, new in bad_candidates[:10]:
            print(f"    {accused_id}: {reason}: {new!r}")
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
        for accused_id, old, new in affected:
            cur.execute(
                f"UPDATE {TABLE} SET address = %s WHERE {PK} = %s AND address = %s",
                (new, accused_id, old),
            )
            if cur.rowcount == 1:
                updated.append((accused_id, old, new))
            else:
                skipped_concurrent.append(accused_id)

    print(f"  updated: {len(updated)}")
    if skipped_concurrent:
        print(f"  skipped (address changed since snapshot -- not overwritten): {len(skipped_concurrent)}")
        for accused_id in skipped_concurrent[:10]:
            print(f"    {accused_id}")

    print("\n== Re-reading AFTER state from the SAME (still uncommitted) transaction ==")
    after = snapshot(conn, columns)

    other_columns = [c for c in columns if c != "address"]
    violations = []

    updated_ids = {accused_id for accused_id, _old, _new in updated}
    for accused_id, old, new in updated:
        actual = after.get(accused_id)
        if actual is None:
            violations.append((accused_id, "row disappeared"))
            continue
        if actual.get("address") != new:
            violations.append((accused_id, f"address = {actual.get('address')!r}, expected {new!r}"))
        for col in other_columns:
            if actual.get(col) != before[accused_id].get(col):
                violations.append((accused_id, f"unrelated column {col!r} changed: "
                                                  f"{before[accused_id].get(col)!r} -> {actual.get(col)!r}"))
        if _has_repeated_segment(actual.get("address")):
            violations.append((accused_id, "post-update address still has a repeated segment"))

    checked = 0
    for accused_id, row in before.items():
        if accused_id in updated_ids:
            continue
        checked += 1
        actual = after.get(accused_id)
        if actual != row:
            violations.append((accused_id, "untouched row changed", actual, row))

    if violations:
        print(f"\n==> ROLLING BACK -- verification failed: {len(violations)} violation(s).")
        for v in violations[:20]:
            print(f"    {v}")
        conn.rollback()
        conn.close()
        raise SystemExit(1)

    print(f"\n==> Verified: {len(updated)} rows updated (address only), "
          f"{checked} other rows byte-identical to before, "
          f"{len(skipped_concurrent)} skipped due to a concurrent change. Committing.")
    conn.commit()
    conn.close()
    print("COMMITTED.")


if __name__ == "__main__":
    main()
