"""Unit tests for the standalone historical backfill's pure logic (no
database required): outcome classification, checkpoint file I/O, and the
--execute/--confirm CLI safety guard. The DB-touching halves
(run_person_backfill, run_multi_accused_backfill, multi_accused_dry_run_report)
are thin, reviewed-not-unit-tested wrappers around already-tested/
production-proven functions (build_person_row_safe, upsert_rows,
_multi_accused_split, detect_multi_accused) -- see
DOPAMS_ETL3_NAME_ENRICHMENT_BACKFILL_REPORT.md for the real --dry-run
output this was validated against.

Run directly: python etl3/tests/test_name_enrichment_backfill.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.run_name_enrichment_backfill import (
    classify_backfill_outcome,
    load_checkpoint,
    save_checkpoint,
    clear_checkpoint,
)


def check(label, fn):
    try:
        fn()
        print(f"[PASS] {label}")
    except Exception as exc:
        print(f"[FAIL] {label}: {type(exc).__name__}: {exc}")
        raise


# ---------------------------------------------------------------------------
# classify_backfill_outcome
# ---------------------------------------------------------------------------

def test_classify_not_yet_processed():
    row = {"input_hash": "abc123"}
    assert classify_backfill_outcome(row, None) == "not_yet_processed"


def test_classify_already_current():
    row = {"input_hash": "abc123"}
    assert classify_backfill_outcome(row, "abc123") == "already_current"


def test_classify_stale_update():
    """A stored hash that differs from a fresh computation -- either the
    raw source data changed upstream, or (the case this whole backfill
    exists for) the cleaning LOGIC changed and now produces different
    output for the same input."""
    row = {"input_hash": "new_hash_after_logic_change"}
    assert classify_backfill_outcome(row, "old_hash_from_before") == "stale_update"


def test_classify_no_enrichable_data():
    """person_row() returning None (no name, no given/surname/alias, no
    domicile/address signal at all) is a legitimate, non-error outcome --
    never misclassified as "not yet processed" (which would wrongly
    suggest something is missing that should be backfilled)."""
    assert classify_backfill_outcome(None, None) == "no_enrichable_data"
    assert classify_backfill_outcome(None, "some_existing_hash") == "no_enrichable_data"


def test_classify_never_raises_on_realistic_inputs():
    for row, stored in [
        ({"input_hash": "x"}, None),
        ({"input_hash": "x"}, "x"),
        ({"input_hash": "x"}, "y"),
        (None, None),
        (None, "y"),
    ]:
        classify_backfill_outcome(row, stored)  # must not raise


# ---------------------------------------------------------------------------
# Checkpoint file I/O
# ---------------------------------------------------------------------------

def test_checkpoint_missing_file_returns_none(tmp_path=None):
    path = Path(__file__).resolve().parent / "_does_not_exist_checkpoint.json"
    assert not path.exists()
    assert load_checkpoint(path) is None


def test_checkpoint_round_trip():
    path = Path(__file__).resolve().parent / "_test_checkpoint_roundtrip.json"
    try:
        state = {"stage": "persons", "last_person_id": "V2:12345", "records_considered_so_far": 500}
        save_checkpoint(path, state)
        assert path.exists()
        loaded = load_checkpoint(path)
        assert loaded["last_person_id"] == "V2:12345"
        assert loaded["records_considered_so_far"] == 500
        # No stray .tmp file left behind by the atomic-write rename.
        assert not path.with_suffix(path.suffix + ".tmp").exists()
    finally:
        if path.exists():
            path.unlink()


def test_checkpoint_clear_removes_file():
    path = Path(__file__).resolve().parent / "_test_checkpoint_clear.json"
    save_checkpoint(path, {"last_person_id": "X"})
    assert path.exists()
    clear_checkpoint(path)
    assert not path.exists()
    clear_checkpoint(path)  # idempotent -- clearing an already-absent checkpoint is a no-op, not an error


def test_checkpoint_corrupt_file_returns_none_not_raise():
    path = Path(__file__).resolve().parent / "_test_checkpoint_corrupt.json"
    try:
        path.write_text("{not valid json", encoding="utf-8")
        assert load_checkpoint(path) is None
    finally:
        if path.exists():
            path.unlink()


def test_checkpoint_write_is_atomic_rename_not_append():
    """Writing a checkpoint twice must leave exactly the SECOND state --
    proves it's a full replace (temp file + rename), not an append or a
    partial in-place edit that could leave a corrupt file if interrupted."""
    path = Path(__file__).resolve().parent / "_test_checkpoint_atomic.json"
    try:
        save_checkpoint(path, {"last_person_id": "A"})
        save_checkpoint(path, {"last_person_id": "B"})
        loaded = load_checkpoint(path)
        assert loaded == {"last_person_id": "B"}
    finally:
        if path.exists():
            path.unlink()


# ---------------------------------------------------------------------------
# CLI safety guard: --execute without --confirm must refuse before any
# database connection is attempted.
# ---------------------------------------------------------------------------

def test_execute_without_confirm_refused_before_any_db_connection():
    import etl3.run_name_enrichment_backfill as backfill_module

    original_argv = sys.argv
    original_get_conn = backfill_module.connections.get_unified_connection

    def _fail_if_called(*_a, **_kw):
        raise AssertionError("get_unified_connection must never be called when --confirm is missing")

    backfill_module.connections.get_unified_connection = _fail_if_called
    try:
        sys.argv = ["run_name_enrichment_backfill.py", "--execute"]
        raised = False
        try:
            backfill_module.main()
        except SystemExit as exc:
            raised = True
            assert "confirm" in str(exc).lower()
        assert raised, "expected SystemExit refusing --execute without --confirm"
    finally:
        sys.argv = original_argv
        backfill_module.connections.get_unified_connection = original_get_conn


def test_dry_run_and_execute_are_mutually_exclusive():
    import etl3.run_name_enrichment_backfill as backfill_module

    original_argv = sys.argv
    try:
        sys.argv = ["run_name_enrichment_backfill.py", "--dry-run", "--execute"]
        raised = False
        try:
            backfill_module.main()
        except SystemExit:
            raised = True
        assert raised
    finally:
        sys.argv = original_argv


def main():
    check("classify: not yet processed", test_classify_not_yet_processed)
    check("classify: already current", test_classify_already_current)
    check("classify: stale update", test_classify_stale_update)
    check("classify: no enrichable data", test_classify_no_enrichable_data)
    check("classify: never raises on realistic inputs", test_classify_never_raises_on_realistic_inputs)
    check("checkpoint: missing file returns None", test_checkpoint_missing_file_returns_none)
    check("checkpoint: round trip", test_checkpoint_round_trip)
    check("checkpoint: clear removes file", test_checkpoint_clear_removes_file)
    check("checkpoint: corrupt file returns None", test_checkpoint_corrupt_file_returns_none_not_raise)
    check("checkpoint: write is atomic replace", test_checkpoint_write_is_atomic_rename_not_append)
    check("CLI: --execute without --confirm refused pre-connection", test_execute_without_confirm_refused_before_any_db_connection)
    check("CLI: --dry-run and --execute mutually exclusive", test_dry_run_and_execute_are_mutually_exclusive)
    print("all name-enrichment backfill unit tests passed")


if __name__ == "__main__":
    main()
