"""Standalone historical backfill: re-run the latest name-cleaning and
multi-accused enrichment (etl3/enrichment/names.py, multi_accused.py)
against EXISTING persons_unified / accused_unified rows that have not yet
received it.

This is a completely independent execution path from the regular ETL3
pipeline (etl3/enrichment/runner.py:run_enrichment()). It is never
imported by, called from, or scheduled alongside that pipeline -- it has
its own CLI entrypoint and its own execution lifecycle, invoked only by
an operator, explicitly, by name. (Why a separate script exists at all,
given the regular pipeline already re-scans every row on each run: this
gives an operator a way to roll out a name-cleaning-only improvement
without triggering the full, AI-involving enrichment pipeline, with its
own dry-run/batch/resume/report controls and reporting, independently of
when the next full ETL3 run happens to occur.)

Reuses the existing, unmodified enrichment functions throughout -- no
cleaning, extraction, or splitting rule is reimplemented here:
  - etl3.enrichment.project.person_row() / runner.build_person_row_safe()
    for per-person name cleaning (the SAME function the regular pipeline
    calls).
  - etl3.enrichment.persist.upsert_rows() for the actual write (the SAME
    idempotent, input_hash-gated upsert the regular pipeline uses).
  - etl3.enrichment.runner._multi_accused_split() for the multi-accused
    split step in --execute mode (the SAME function, same guards,
    already proven in production: 19 real split children exist today).
  - etl3.enrichment.multi_accused.detect_multi_accused() /
    runner._load_parent_accused_for_split() for the READ-ONLY dry-run
    analysis of what the split step would do, without writing anything.

Usage (repo root):
    python -m etl3.run_name_enrichment_backfill --dry-run
    python -m etl3.run_name_enrichment_backfill --dry-run --limit 100
    python -m etl3.run_name_enrichment_backfill --execute --confirm --batch-size 200
    python -m etl3.run_name_enrichment_backfill --execute --confirm --resume
    python -m etl3.run_name_enrichment_backfill --dry-run --skip-multi-accused
    python -m etl3.run_name_enrichment_backfill --dry-run --skip-persons

Safety:
  - --dry-run opens a READ-ONLY database session (Postgres-enforced via
    connections.get_unified_connection(readonly=True), same as every
    other read-only tool in this codebase) and never calls upsert_rows,
    _multi_accused_split, or the checkpoint writer. Nothing it does can
    write to the database even if a bug tried to.
  - --execute requires --confirm as well; --execute alone refuses to run.
  - Every write is batched and committed per batch (never one giant
    transaction over the whole table), with a checkpoint saved only
    after a batch's commit succeeds -- a crash or Ctrl-C mid-batch loses
    at most that one uncommitted batch, never corrupts a partially
    written one, and --resume picks back up exactly where the last
    successful batch left off.
  - Never deletes or overwrites a raw source value. Never changes a
    canonical person/accused identity. Never touches deduplication
    (etl3/identity/person_matching.py) or face-matching thresholds.
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from etl3.config import settings as cfg
from etl3.db import connections
from etl3.enrichment.address import load_geo_kb
from etl3.enrichment.multi_accused import detect_multi_accused
from etl3.enrichment.persist import upsert_rows
from etl3.enrichment.runner import (
    _latest,
    _load_parent_accused_for_split,
    _multi_accused_settings,
    _multi_accused_split,
    _name_identity_settings,
    _v2_person_payload,
    build_person_row_safe,
)

_PERSON_ENRICHMENT_COLUMNS = [
    "person_id", "source_system", "domicile_classification", "domicile_method",
    "nationality", "surname", "relation_type", "gender_source", "address_resolution",
    "raw_full_name", "cleaned_full_name", "cleaned_given_name",
    "cleaned_alias", "cleaned_relative_name", "input_hash",
]
_PERSON_ENRICHMENT_IDENTITY_COLUMNS = [
    "name_identified", "unidentified_reason", "extracted_role_hint",
    "extracted_nationality_hint", "review_flags",
]

_DEFAULT_CHECKPOINT_PATH = Path(__file__).resolve().parent / ".backfill_name_enrichment_checkpoint.json"
_BATCH_RETRY_ATTEMPTS = 3
_BATCH_RETRY_BACKOFF_SEC = 2.0

_STOP = False


def _handle_signal(signum, _frame):
    global _STOP
    print(f"[name-backfill] received signal {signum}; will stop after current batch", flush=True)
    _STOP = True


def _stop_requested() -> bool:
    return _STOP


# ---------------------------------------------------------------------------
# Checkpoint: pure file I/O, no database. Deliberately NOT a database table
# -- this state is operator-local, run-scoped, and never read by anything
# else (not the regular pipeline, not any report), so a plain JSON file
# next to this script is the simplest thing that is still safe, inspectable,
# and git-ignored (see .gitignore) rather than ever accidentally committed.
# ---------------------------------------------------------------------------

def load_checkpoint(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save_checkpoint(path: Path, state: dict) -> None:
    """Atomic write (temp file + rename) so a crash mid-write never leaves
    a half-written, unparseable checkpoint behind."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def clear_checkpoint(path: Path) -> None:
    if path.exists():
        path.unlink()


# ---------------------------------------------------------------------------
# Person-level name enrichment backfill
# ---------------------------------------------------------------------------

def classify_backfill_outcome(row: dict | None, stored_hash: str | None) -> str:
    """Pure classification, no I/O -- what WOULD happen to this person_id,
    given the freshly computed row (or None, if person_row() found
    nothing enrichable at all) and the currently stored input_hash (or
    None, if no person_enrichment row exists yet).

    Distinguishes exactly the states the backfill must tell apart (not
    every NULL means "incomplete" -- see the module docstring and the
    backfill report): not_yet_processed, stale_update, already_current,
    no_enrichable_data. "failed" is assigned by the caller instead, from
    build_person_row_safe()'s own failure signal, never from this
    function.
    """
    if row is None:
        return "no_enrichable_data"
    if stored_hash is None:
        return "not_yet_processed"
    if row["input_hash"] == stored_hash:
        return "already_current"
    return "stale_update"


def _fetch_person_batch(conn, after_person_id: str | None, batch_size: int):
    with conn.cursor() as cur:
        if after_person_id is None:
            cur.execute(
                """
                SELECT person_id, source_system, source_record_id,
                       full_name, alias, relative_name, nationality,
                       present_address_text, permanent_address_text
                FROM persons_unified
                ORDER BY person_id
                LIMIT %s
                """,
                (batch_size,),
            )
        else:
            cur.execute(
                """
                SELECT person_id, source_system, source_record_id,
                       full_name, alias, relative_name, nationality,
                       present_address_text, permanent_address_text
                FROM persons_unified
                WHERE person_id > %s
                ORDER BY person_id
                LIMIT %s
                """,
                (after_person_id, batch_size),
            )
        return cur.fetchall()


def _fetch_stored_hashes(conn, person_ids: list[str]) -> dict:
    if not person_ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT person_id, input_hash FROM person_enrichment WHERE person_id = ANY(%s)",
            (person_ids,),
        )
        return dict(cur.fetchall())


def _retry_commit(conn, attempt_label: str):
    """Commit with a small number of retries for a TRANSIENT failure only
    (a dropped connection, a serialization conflict) -- never retries a
    real data/programming error, which is re-raised immediately on its
    first occurrence so it is never silently swallowed as "transient"."""
    import psycopg2

    last_exc = None
    for attempt in range(1, _BATCH_RETRY_ATTEMPTS + 1):
        try:
            conn.commit()
            return
        except (psycopg2.OperationalError, psycopg2.errors.SerializationFailure) as exc:
            last_exc = exc
            print(
                f"[name-backfill] {attempt_label}: transient commit failure "
                f"(attempt {attempt}/{_BATCH_RETRY_ATTEMPTS}): {exc}",
                flush=True,
            )
            if attempt < _BATCH_RETRY_ATTEMPTS:
                time.sleep(_BATCH_RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError(f"{attempt_label}: commit failed after {_BATCH_RETRY_ATTEMPTS} attempts") from last_exc


def run_person_backfill(
    conn, run_id: str, *, dry_run: bool, batch_size: int, limit: int | None,
    start_after: str | None, checkpoint_path: Path, stop_check=_stop_requested,
) -> dict:
    """Process persons_unified in batches, keyset-paginated on person_id
    (never OFFSET -- stable under concurrent inserts, and never requires
    loading the whole table). Bounded memory: only one batch_size's worth
    of rows is ever in memory at a time; the V2 persons_source merge
    table is loaded once (the SAME cost the regular pipeline already
    pays on every run, not a new one introduced here)."""
    geo_kb = load_geo_kb(conn, schema="kb")
    persist_identity_metadata = _name_identity_settings()["enabled"]
    v2_payloads = {
        record_id: payload for record_id, payload in _latest(conn, "persons_source", "V2")
    }

    stats = {
        "batches": 0, "records_considered": 0,
        "not_yet_processed": 0, "stale_update": 0, "already_current": 0,
        "no_enrichable_data": 0, "failed": 0,
        "name_identified_with_changes": 0, "name_identified_unchanged": 0,
        "name_unidentified": 0, "failed_records": [],
    }
    cursor = start_after
    stopped_early = False
    while True:
        if stop_check():
            stopped_early = True
            break
        batch = _fetch_person_batch(conn, cursor, batch_size)
        if not batch:
            break
        person_ids = [r[0] for r in batch]
        stored_hashes = _fetch_stored_hashes(conn, person_ids)

        to_write = []
        for (
            person_id, source_system, record_id, full_name, alias, relative_name,
            nationality, present_address_text, permanent_address_text,
        ) in batch:
            if limit is not None and stats["records_considered"] >= limit:
                stopped_early = True
                break
            stats["records_considered"] += 1
            if source_system == "V2":
                payload = _v2_person_payload(
                    v2_payloads, record_id, full_name, alias, relative_name,
                    nationality, present_address_text, permanent_address_text,
                )
            else:
                payload = {
                    "full_name": full_name, "alias": alias, "relative_name": relative_name,
                    "nationality": nationality, "present_address_text": present_address_text,
                    "permanent_address_text": permanent_address_text,
                }
            row, failure = build_person_row_safe(
                source_system, person_id, record_id, payload, geo_kb=geo_kb,
                persist_identity_metadata=persist_identity_metadata,
            )
            if failure:
                stats["failed"] += 1
                stats["failed_records"].append(failure)
                continue
            outcome = classify_backfill_outcome(row, stored_hashes.get(person_id))
            stats[outcome] += 1
            if outcome in ("not_yet_processed", "stale_update"):
                to_write.append(row)
                if row["name_identified"] is False:
                    stats["name_unidentified"] += 1
                elif row["cleaned_full_name"] == row["raw_full_name"]:
                    stats["name_identified_unchanged"] += 1
                else:
                    stats["name_identified_with_changes"] += 1

        stats["batches"] += 1
        last_person_id_in_batch = person_ids[-1]

        if not dry_run and to_write:
            columns = _PERSON_ENRICHMENT_COLUMNS + (
                _PERSON_ENRICHMENT_IDENTITY_COLUMNS if persist_identity_metadata else []
            )
            upsert_rows(conn, "person_enrichment", "person_id", to_write, columns, run_id, "person_enrichment")
            _retry_commit(conn, f"person batch ending {last_person_id_in_batch}")
        elif not dry_run:
            # Nothing to write this batch, but still commit to release the
            # transaction/snapshot before moving on.
            _retry_commit(conn, f"person batch ending {last_person_id_in_batch} (no-op)")

        cursor = last_person_id_in_batch
        if not dry_run:
            save_checkpoint(checkpoint_path, {
                "stage": "persons", "last_person_id": cursor,
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "records_considered_so_far": stats["records_considered"],
            })
        print(
            f"[name-backfill] person batch #{stats['batches']} done, "
            f"cursor={cursor!r}, considered={stats['records_considered']}, "
            f"written={len(to_write)}, dry_run={dry_run}",
            flush=True,
        )
        if stopped_early:
            break
        if len(batch) < batch_size:
            break  # last page

    stats["stopped_early"] = stopped_early
    stats["last_cursor"] = cursor
    return stats


# ---------------------------------------------------------------------------
# Multi-accused split backfill
# ---------------------------------------------------------------------------

def multi_accused_dry_run_report(conn, limit: int | None = None) -> dict:
    """Read-only: what WOULD the multi-accused split step do, without
    calling build_split_rows/write_split_rows or touching the database at
    all beyond the one SELECT _load_parent_accused_for_split already
    issues. Reuses detect_multi_accused() directly -- the exact function
    the real split step is built on -- so this prediction can never drift
    from what --execute would actually do."""
    parents = _load_parent_accused_for_split(conn)
    if limit is not None:
        parents = parents[:limit]
    report = {
        "parents_considered": 0, "would_split_high_confidence": 0,
        "flagged_low_confidence_for_review": 0, "no_multi_person_signal": 0,
        "high_confidence_examples": [], "low_confidence_examples": [],
    }
    for parent in parents:
        report["parents_considered"] += 1
        result = detect_multi_accused(parent["full_name"])
        if result["confidence"] == "high":
            report["would_split_high_confidence"] += 1
            if len(report["high_confidence_examples"]) < 10:
                report["high_confidence_examples"].append({
                    "accused_id": parent["accused_id"], "crime_id": parent["crime_id"],
                    "raw_full_name": parent["full_name"],
                    "segments": [s["raw_text"] for s in result["segments"]],
                })
        elif result["confidence"] == "low":
            report["flagged_low_confidence_for_review"] += 1
            if len(report["low_confidence_examples"]) < 10:
                report["low_confidence_examples"].append({
                    "accused_id": parent["accused_id"], "crime_id": parent["crime_id"],
                    "raw_full_name": parent["full_name"], "warnings": result["warnings"],
                })
        else:
            report["no_multi_person_signal"] += 1
    return report


def run_multi_accused_backfill(conn, run_id: str) -> dict:
    """--execute only: calls the EXISTING, already-production-proven
    _multi_accused_split() directly -- no parallel implementation. That
    function already guarantees no-duplicate-split (existing_active_children
    / diff_superseded_children, keyed on deterministic split_child_id, never
    on name matching alone) and same-crime linkage (crime_id carried
    straight from the parent in build_split_rows). Gated the same way the
    regular pipeline gates it: a no-op unless ETL3_MULTI_ACCUSED_SPLIT_ENABLED=1."""
    settings = _multi_accused_settings()
    if not settings["enabled"]:
        print(
            "[name-backfill] ETL3_MULTI_ACCUSED_SPLIT_ENABLED is not set to '1' -- "
            "multi-accused split step is a no-op (same gate the regular "
            "pipeline uses). Set it to actually create split occurrences.",
            flush=True,
        )
        return {"status": "disabled"}
    stats = _multi_accused_split(conn, run_id)
    conn.commit()
    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="ETL3 standalone historical name-enrichment backfill "
                     "(never invoked by the regular ETL3 pipeline)",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Report what would change; writes nothing")
    mode.add_argument("--execute", action="store_true", help="Actually write changes (requires --confirm)")
    parser.add_argument(
        "--confirm", action="store_true",
        help="Required alongside --execute as an explicit second confirmation",
    )
    parser.add_argument("--batch-size", type=int, default=500, metavar="N")
    parser.add_argument("--limit", type=int, default=None, metavar="N", help="Cap total person records considered")
    parser.add_argument("--resume", action="store_true", help="Resume from the saved checkpoint")
    parser.add_argument("--start-id", default=None, metavar="PERSON_ID", help="Start after this person_id (overrides --resume)")
    parser.add_argument("--checkpoint-file", default=str(_DEFAULT_CHECKPOINT_PATH), metavar="PATH")
    parser.add_argument("--skip-persons", action="store_true", help="Skip the name-enrichment backfill")
    parser.add_argument("--skip-multi-accused", action="store_true", help="Skip the multi-accused split step")
    parser.add_argument("--report-file", default=None, metavar="PATH", help="Write the full JSON stats here too")
    args = parser.parse_args()

    if args.execute and not args.confirm:
        raise SystemExit(
            "--execute requires --confirm as an explicit second confirmation "
            "(this writes to the database). Refusing to run.",
        )
    if args.batch_size < 1:
        raise SystemExit("--batch-size must be >= 1")

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    dry_run = bool(args.dry_run)
    checkpoint_path = Path(args.checkpoint_file)

    start_after = args.start_id
    if start_after is None and args.resume and not dry_run:
        checkpoint = load_checkpoint(checkpoint_path)
        if checkpoint:
            start_after = checkpoint.get("last_person_id")
            print(f"[name-backfill] resuming after person_id={start_after!r} "
                  f"(checkpoint from {checkpoint.get('updated_at')})", flush=True)
    elif args.resume and dry_run:
        print("[name-backfill] --resume has no effect in --dry-run mode "
              "(dry-run never writes a checkpoint to resume from)", flush=True)

    conn = connections.get_unified_connection(readonly=dry_run)
    print(
        f"[name-backfill] target={cfg.EXPECTED_UNIFIED_DBNAME} dry_run={dry_run} "
        f"batch_size={args.batch_size} limit={args.limit} "
        f"start_after={start_after!r}",
        flush=True,
    )
    run_id = str(uuid.uuid4())
    summary = {"run_id": run_id, "dry_run": dry_run, "persons": None, "multi_accused": None}
    try:
        if not args.skip_persons:
            print("\n===== person name-enrichment backfill =====\n", flush=True)
            summary["persons"] = run_person_backfill(
                conn, run_id, dry_run=dry_run, batch_size=args.batch_size, limit=args.limit,
                start_after=start_after, checkpoint_path=checkpoint_path,
            )
            print("[name-backfill] persons:", json.dumps(
                {k: v for k, v in summary["persons"].items() if k != "failed_records"},
            ), flush=True)
            if summary["persons"].get("failed_records"):
                print("[name-backfill] sample failures:",
                      json.dumps(summary["persons"]["failed_records"][:5]), flush=True)

        if not args.skip_multi_accused and not _STOP:
            print("\n===== multi-accused split backfill =====\n", flush=True)
            if dry_run:
                summary["multi_accused"] = multi_accused_dry_run_report(conn, limit=args.limit)
            else:
                summary["multi_accused"] = run_multi_accused_backfill(conn, run_id)
            print("[name-backfill] multi_accused:", json.dumps(
                summary["multi_accused"], default=str,
            ), flush=True)

        if not dry_run and not _STOP and summary.get("persons") and not summary["persons"].get("stopped_early"):
            # Full, uninterrupted completion -- clear the checkpoint so a
            # future --resume starts a fresh pass rather than skipping
            # everything (the table keeps changing as new source data
            # arrives, so "done" is never truly final).
            clear_checkpoint(checkpoint_path)
            print("[name-backfill] full pass completed; checkpoint cleared", flush=True)

        summary["stopped"] = _STOP
    finally:
        conn.close()

    if args.report_file:
        Path(args.report_file).write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        print(f"[name-backfill] wrote {args.report_file}", flush=True)

    print("\n[name-backfill] DONE." + (" (dry run -- nothing was written)" if dry_run else ""), flush=True)
    return summary


if __name__ == "__main__":
    main()
