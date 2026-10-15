"""Controlled, verify-before-commit reprocessing of person_enrichment against
the REAL dopams_cctns_v2 database, using the actual existing
etl3.enrichment.runner._person_enrichment function (not a hand-rolled
approximation) so every parameter (alias, given_name, surname,
relative_name, relation_type) is wired exactly as production does it.

Safety design:
  1. Snapshot every person_enrichment row's name-related columns BEFORE,
     on a read-only connection.
  2. Open a write connection, run the real _person_enrichment once --
     this executes inside an open, UNCOMMITTED transaction.
  3. Re-read the same columns from inside that same (still open, still
     uncommitted) transaction, and diff AFTER against the BEFORE
     snapshot in Python.
  4. Only commit if the diff matches expectations: changes confined to
     cleaned_full_name (the duplicate-name / hyphen-residue fix), with
     ZERO unexpected changes to cleaned_alias, relation_type or
     cleaned_relative_name. Any surprise rolls back -- nothing is kept.

Run: python etl3/diagnostics/_reprocess_person_names_verified.py
"""
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.db import connections
from etl3.enrichment.address import load_geo_kb
from etl3.enrichment.runner import _person_enrichment

NAME_COLUMNS = (
    "person_id, raw_full_name, cleaned_full_name, cleaned_given_name, "
    "cleaned_alias, relation_type, cleaned_relative_name, surname, "
    "domicile_classification, nationality"
)


def snapshot(conn):
    with conn.cursor() as cur:
        cur.execute(f"SELECT {NAME_COLUMNS} FROM person_enrichment")
        return {row[0]: row[1:] for row in cur.fetchall()}


def main():
    print("== Snapshotting BEFORE state (read-only connection) ==")
    ro_conn = connections.get_unified_connection(readonly=True)
    before = snapshot(ro_conn)
    ro_conn.close()
    print(f"  {len(before)} person_enrichment rows snapshotted")

    print("\n== Opening write connection, running the REAL _person_enrichment (uncommitted) ==")
    conn = connections.get_unified_connection()
    geo_kb = load_geo_kb(conn, schema="kb")
    run_id = str(uuid.uuid4())
    print(f"  run_id = {run_id}")
    stats = _person_enrichment(conn, run_id, geo_kb=geo_kb)
    print(f"  upsert stats: {stats}")

    print("\n== Reading AFTER state from the SAME (still uncommitted) transaction ==")
    after = snapshot(conn)
    print(f"  {len(after)} rows visible after the run (same table, same transaction)")

    changed_full_name_only = []
    unexpected = []
    new_rows = []
    for pid, after_vals in after.items():
        before_vals = before.get(pid)
        if before_vals is None:
            new_rows.append((pid, after_vals))
            continue
        (b_raw, b_cleaned, b_given, b_alias, b_reltype, b_relname, b_sur, b_dom, b_nat) = before_vals
        (a_raw, a_cleaned, a_given, a_alias, a_reltype, a_relname, a_sur, a_dom, a_nat) = after_vals
        if before_vals == after_vals:
            continue
        safe = (
            b_raw == a_raw
            and b_given == a_given
            and b_alias == a_alias
            and b_reltype == a_reltype
            and b_relname == a_relname
            and b_sur == a_sur
            and b_dom == a_dom
            and b_nat == a_nat
            and b_cleaned != a_cleaned
        )
        if safe:
            changed_full_name_only.append((pid, b_raw, b_cleaned, a_cleaned))
        else:
            unexpected.append((pid, before_vals, after_vals))

    print(f"\n  rows with ONLY cleaned_full_name changed (expected -- the fix): {len(changed_full_name_only)}")
    print(f"  rows with any OTHER/unexpected field change: {len(unexpected)}")
    print(f"  brand-new person_enrichment rows not present before: {len(new_rows)}")

    print("\n  sample of expected changes:")
    for pid, raw, b_cleaned, a_cleaned in changed_full_name_only[:10]:
        print(f"    {pid}: raw={raw!r} before={b_cleaned!r} after={a_cleaned!r}")

    if unexpected:
        print("\n  UNEXPECTED CHANGES (full detail):")
        for pid, b, a in unexpected[:20]:
            print(f"    {pid}: before={b} after={a}")

    if unexpected or new_rows:
        print("\n==> ROLLING BACK -- unexpected changes or new rows found, nothing committed.")
        conn.rollback()
        conn.close()
        raise SystemExit(1)

    if not changed_full_name_only:
        print("\n==> ROLLING BACK -- zero changes found, nothing to commit (unexpected on its own).")
        conn.rollback()
        conn.close()
        raise SystemExit(1)

    print(f"\n==> All changes verified safe and isolated to cleaned_full_name on {len(changed_full_name_only)} rows. Committing.")
    conn.commit()
    conn.close()
    print("COMMITTED.")

    # Final read-only confirmation, independent connection.
    ro_conn2 = connections.get_unified_connection(readonly=True)
    with ro_conn2.cursor() as cur:
        cur.execute(
            "SELECT person_id, full_name FROM be_read.person WHERE person_id = ANY(%s)",
            ([pid for pid, *_ in changed_full_name_only[:3]],),
        )
        print("\nbe_read.person for 3 of the fixed rows (still shows RAW name -- expected, that gap is separate):")
        for pid, name in cur.fetchall():
            print(f"  {pid}: {name!r}")
        cur.execute(
            "SELECT person_id, cleaned_full_name FROM person_enrichment WHERE person_id = ANY(%s)",
            ([pid for pid, *_ in changed_full_name_only[:3]],),
        )
        print("\nperson_enrichment.cleaned_full_name for the same 3 rows (now fixed, persisted):")
        for pid, cleaned in cur.fetchall():
            print(f"  {pid}: {cleaned!r}")
    ro_conn2.close()


if __name__ == "__main__":
    main()
