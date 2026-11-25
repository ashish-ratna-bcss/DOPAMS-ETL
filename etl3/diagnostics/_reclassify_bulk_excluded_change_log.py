"""Operator script: correct the `change_classification` of EXISTING
change_log rows that fall inside a REGISTERED bulk_event_exclusions
window but are still labeled `business_change`.

Never touches `old_value`/`new_value` -- the actual historical evidence
(what changed, from what, to what) is untouched forever. The only thing
this script ever writes is the single `change_classification` column,
correcting a label, not rewriting history -- consistent with why that
column exists at all (see dopams_cctns/schema/ETL3_UNIFIED_SCHEMA.sql's
change_log definition).

Requires bulk_event_exclusions to already have at least one row (see
etl3/diagnostics/_register_known_bulk_events.py --execute --confirm,
itself a separate, operator-triggered step) -- with zero rows there
(today's live state), --dry-run reports "0 rows would change" and
--execute is a safe no-op.

Same --dry-run/--execute --confirm safety discipline as
etl3/run_name_enrichment_backfill.py and
_register_known_bulk_events.py.

Usage (repo root):
    python -m etl3.diagnostics._reclassify_bulk_excluded_change_log --dry-run
    python -m etl3.diagnostics._reclassify_bulk_excluded_change_log --execute --confirm
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.db import connections

_AFFECTED_JOIN = """
    change_log cl
    JOIN bulk_event_exclusions be
      ON be.source_system = cl.source_system
     AND cl.observed_at BETWEEN be.range_start AND be.range_end
    WHERE cl.change_classification = 'business_change'
"""
_SELECT_AFFECTED = f"""
    SELECT cl.id, cl.entity, cl.unified_id, cl.field, cl.observed_at, cl.source_system
    FROM {_AFFECTED_JOIN}
    ORDER BY cl.observed_at
"""


def dry_run_report(conn):
    print("== DRY RUN: nothing will be written ==\n")
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM bulk_event_exclusions")
        n_windows = cur.fetchone()[0]
        print(f"Registered bulk_event_exclusions windows: {n_windows}")
        if n_windows == 0:
            print("No windows registered -- run _register_known_bulk_events.py first. Nothing to do.")
            return

        cur.execute(_SELECT_AFFECTED)
        rows = cur.fetchall()
        print(f"\n{len(rows)} change_log row(s) would be reclassified "
              f"business_change -> administrative_bulk_excluded:\n")
        by_entity = {}
        for _id, entity, _uid, field, _ts, _ss in rows:
            by_entity.setdefault(entity, {}).setdefault(field, 0)
            by_entity[entity][field] += 1
        for entity, fields in sorted(by_entity.items()):
            print(f"  {entity}:")
            for field, n in sorted(fields.items()):
                print(f"    {field}: {n}")
        print(f"\nNo old_value/new_value is ever touched. Only change_classification changes.")


def execute(conn):
    with conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE change_log SET change_classification = 'administrative_bulk_excluded'
            WHERE id IN (SELECT cl.id FROM {_AFFECTED_JOIN})
            """,
        )
        updated = cur.rowcount
    conn.commit()
    print(f"Reclassified {updated} change_log row(s) business_change -> administrative_bulk_excluded.")
    print("old_value/new_value were not touched on any row.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Report what would change; writes nothing")
    mode.add_argument("--execute", action="store_true", help="Actually reclassify (requires --confirm)")
    parser.add_argument("--confirm", action="store_true", help="Required alongside --execute")
    args = parser.parse_args()

    if args.execute and not args.confirm:
        raise SystemExit("--execute requires --confirm as well. Refusing to run.")

    if args.dry_run:
        conn = connections.get_unified_connection(readonly=True)
        try:
            dry_run_report(conn)
        finally:
            conn.rollback()
            conn.close()
    else:
        conn = connections.get_unified_connection(readonly=False)
        try:
            execute(conn)
        finally:
            conn.close()


if __name__ == "__main__":
    main()
