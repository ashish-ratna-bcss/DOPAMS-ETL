"""Operator script: register a documented administrative/maintenance bulk
timestamp event into bulk_event_exclusions, so future observations landing
in that window get classified `administrative_bulk_excluded` instead of
`business_change` by etl3.merger.current_state.UnifiedBatchWriter (see
that module's _load_bulk_exclusions/_is_in_bulk_window).

This is NOT run automatically by anything -- not the daily pipeline, not
the regular enrichment runner, not this report's own implementation work.
It is a one-time, operator-triggered registration, with the exact same
--dry-run/--execute --confirm safety discipline as
etl3/run_name_enrichment_backfill.py. Only ONE row is ever inserted by
this script's current built-in event list; add a new entry to
KNOWN_EVENTS (with its own citation/evidence) before registering anything
else, never invent a window.

This script does NOT touch any existing change_log row -- it only adds a
new bulk_event_exclusions row (currently 0 rows live). Retroactively
reclassifying already-written change_log rows is a SEPARATE, also
not-automatically-run script:
etl3/diagnostics/_reclassify_bulk_excluded_change_log.py.

Usage (repo root):
    python -m etl3.diagnostics._register_known_bulk_events --dry-run
    python -m etl3.diagnostics._register_known_bulk_events --execute --confirm
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.db import connections

# Each entry here must already be independently documented/evidenced
# elsewhere before being added -- this script never invents a window.
# This one entry is the same event the schema's own comments
# (dopams_cctns/schema/ETL3_UNIFIED_SCHEMA.sql, migration
# 001_initial_schema.sql's bulk_event_exclusions seed-row comment) and
# CCTNS_V2_ACCUSED_API_CHANGE_AUDIT.md section 2 already describe:
# 86.9% of crimes and 86.6% of accused on CCTNS V2 touched in one UTC
# hour, confirmed not a genuine batch of business-state changes.
KNOWN_EVENTS = [
    {
        "source_system": "V2",
        "range_start": "2026-08-24 00:00:00+00",
        "range_end": "2026-08-24 23:59:59+00",
        "description": (
            "Administrative/maintenance bulk timestamp update -- 86.9% of "
            "crimes and 86.6% of accused touched in one day, 92% of that "
            "in a single UTC hour. Not a genuine batch of business-state "
            "changes. Documented in dopams_cctns/schema/"
            "ETL3_UNIFIED_SCHEMA.sql and CCTNS_V2_ACCUSED_API_CHANGE_AUDIT.md "
            "section 2; registered by _register_known_bulk_events.py."
        ),
    },
]


def dry_run_report(conn):
    print("== DRY RUN: nothing will be written ==\n")
    with conn.cursor() as cur:
        for event in KNOWN_EVENTS:
            cur.execute(
                "SELECT 1 FROM bulk_event_exclusions WHERE source_system=%s AND range_start=%s AND range_end=%s",
                (event["source_system"], event["range_start"], event["range_end"]),
            )
            already_registered = cur.fetchone() is not None
            print(f"  {event['source_system']} {event['range_start']} .. {event['range_end']}")
            print(f"    {'ALREADY REGISTERED (no-op)' if already_registered else 'WOULD INSERT'}")
            print(f"    description: {event['description'][:120]}...")

            # Impact preview: how many change_log rows currently classified
            # business_change fall in this window, for this source_system,
            # that a SEPARATE reclassification script could later correct.
            cur.execute(
                """
                SELECT count(*) FROM change_log
                WHERE source_system = %s AND change_classification = 'business_change'
                  AND observed_at BETWEEN %s AND %s
                """,
                (event["source_system"], event["range_start"], event["range_end"]),
            )
            affected = cur.fetchone()[0]
            print(f"    {affected} existing change_log rows in this window are still classified "
                  f"'business_change' -- NOT touched by this script; see "
                  f"_reclassify_bulk_excluded_change_log.py for that, separately.")


def execute(conn):
    inserted = 0
    with conn.cursor() as cur:
        for event in KNOWN_EVENTS:
            cur.execute(
                """
                INSERT INTO bulk_event_exclusions (source_system, range_start, range_end, description)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (event["source_system"], event["range_start"], event["range_end"], event["description"]),
            )
            inserted += cur.rowcount
    conn.commit()
    print(f"Inserted {inserted} new bulk_event_exclusions row(s) (of {len(KNOWN_EVENTS)} known events).")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Report what would change; writes nothing")
    mode.add_argument("--execute", action="store_true", help="Actually insert the known event(s) (requires --confirm)")
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
