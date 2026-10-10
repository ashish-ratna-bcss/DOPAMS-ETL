"""Unit tests for per-FIR drug + accused AI scheduling.

No database, no Ollama, no production process. Mocks settle checks, extractors,
and DB writes to verify ordering, commits, failure isolation, and recovery.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.enrichment.ai import AIExtractionError
from etl3.enrichment import runner as R


SETTINGS = {
    "enabled": True,
    "host": "http://127.0.0.1:11434",
    "model": "test-model",
    "timeout": 30,
    "mode": "backfill",
    "limit": 0,
    "batch_size": 2,
    "max_retries": 1,
    "request_delay_sec": 0,
    "health_cooldown_sec": 0.01,
}


class FakeConn:
    def __init__(self, usable: bool = True):
        self.commits = 0
        self.rollbacks = 0
        self.closed = 0
        self._usable = usable
        self._cursors = []

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def cursor(self):
        cur = mock.MagicMock()
        cur.__enter__ = mock.MagicMock(return_value=cur)
        cur.__exit__ = mock.MagicMock(return_value=False)
        if not self._usable:
            cur.execute.side_effect = Exception("connection closed")
        else:
            cur.fetchone.return_value = (1,)
            cur.fetchall.return_value = []
        self._cursors.append(cur)
        return cur


def _accused_rec(accused_id, crime_id="C1", code="A1"):
    return {
        "accused_id": accused_id,
        "source_system": "V2",
        "accused_code": code,
        "crime_id": crime_id,
        "person_id": f"p-{accused_id}",
    }


def test_drug_digest_stable():
    assert R._drug_input_digest("facts") == R._drug_input_digest("facts")
    assert R._drug_input_digest("a") != R._drug_input_digest("b")


def test_accused_digest_includes_roster():
    d1 = R._accused_input_digest("facts", [{"accused_id": "a1"}])
    d2 = R._accused_input_digest("facts", [{"accused_id": "a1"}, {"accused_id": "a2"}])
    assert d1 != d2


def test_mixed_settled_pending_runs_only_pending():
    """Drug settled + accused pending (and vice versa) on different FIRs."""
    conn = FakeConn()
    facts = {
        "C1": ("V2", "brief facts for crime one long enough"),
        "C2": ("V2", "brief facts for crime two long enough"),
    }
    by_crime = {
        "C1": [_accused_rec("a1", "C1")],
        "C2": [_accused_rec("a2", "C2")],
    }
    settled = {
        # C1 drug settled; C1 accused pending
        # C2 drug pending; C2 accused settled
    }

    def already_settled(_conn, crime_id, digest):
        # Distinguish digests by kind via digest content is opaque; use call order
        # through a side channel: drug digest has no "accused" kind in hash input.
        # Track via which digest was computed last by patching digests.
        key = getattr(already_settled, "_last_kind", None)
        if crime_id == "C1" and key == "drug":
            return True
        if crime_id == "C2" and key == "accused":
            return True
        return False

    drug_calls = []
    accused_calls = []

    def fake_drug(*args, **kwargs):
        crime_id = args[2]
        drug_calls.append(crime_id)
        return {"outcome": "success", "rejected": 0, "generic_rejected": 0, "unsupported": 0}

    def fake_accused(*args, **kwargs):
        crime_id = args[1]
        accused_calls.append(crime_id)
        return {
            "outcome": "success",
            "rows": [{"accused_id": f"row-{crime_id}", "input_hash": "h"}],
            "records": [],
        }

    with mock.patch.object(R, "_pairs", return_value=[("C1",), ("C2",)]), \
            mock.patch.object(R, "_brief_facts", return_value=facts), \
            mock.patch.object(R, "_load_accused_by_crime", return_value=by_crime), \
            mock.patch.object(R, "_drug_input_digest", side_effect=lambda t: (_set_kind("drug"), f"d:{t}")[1]), \
            mock.patch.object(R, "_accused_input_digest", side_effect=lambda t, r: (_set_kind("accused"), f"a:{t}")[1]), \
            mock.patch.object(R, "_ai_already_settled", side_effect=already_settled), \
            mock.patch.object(R, "process_drug_for_crime", side_effect=fake_drug), \
            mock.patch.object(R, "process_accused_ai_for_crime", side_effect=fake_accused), \
            mock.patch.object(R, "upsert_rows", return_value={"inserted": 1}), \
            mock.patch.object(R, "apply_v1_code_type_and_ccl_numbering", side_effect=lambda r, t: r), \
            mock.patch.object(R, "build_roster_for_prompt", return_value=[{"id": 1}]):

        def _set_kind(kind):
            already_settled._last_kind = kind
            return None

        # rebind for closure used in side_effect above
        globals_patch = None  # placate linters

        stats = R.run_per_fir_ai_backfill(
            conn, "run-1", kb_items=[],
            drug_client=mock.Mock(), accused_client=mock.Mock(),
            settings=SETTINGS, do_drugs=True, do_accused=True,
        )

    assert drug_calls == ["C2"], drug_calls
    assert accused_calls == ["C1"], accused_calls
    assert stats["drugs"]["skipped"] == 1
    assert stats["drugs"]["processed"] == 1
    assert stats["accused"]["skipped"] == 1
    assert stats["accused"]["processed"] == 1
    # One commit per successful enrichment type
    assert conn.commits >= 2


def test_drug_failure_still_attempts_accused():
    conn = FakeConn()
    facts = {"C1": ("V2", "brief facts long enough here")}
    by_crime = {"C1": [_accused_rec("a1"), _accused_rec("a2", code="A2")]}
    accused_calls = []

    def boom_drug(*_a, **_k):
        raise RuntimeError("simulated drug write failure")

    def ok_accused(*args, **kwargs):
        accused_calls.append(args[1])
        # Multiple accused on one FIR → one call
        assert len(args[2]) == 2
        return {
            "outcome": "success",
            "rows": [
                {"accused_id": "a1", "input_hash": "h1"},
                {"accused_id": "a2", "input_hash": "h2"},
            ],
            "records": args[2],
        }

    with mock.patch.object(R, "_pairs", return_value=[("C1",)]), \
            mock.patch.object(R, "_brief_facts", return_value=facts), \
            mock.patch.object(R, "_load_accused_by_crime", return_value=by_crime), \
            mock.patch.object(R, "_ai_already_settled", return_value=False), \
            mock.patch.object(R, "process_drug_for_crime", side_effect=boom_drug), \
            mock.patch.object(R, "process_accused_ai_for_crime", side_effect=ok_accused), \
            mock.patch.object(R, "upsert_rows", return_value={"inserted": 2}), \
            mock.patch.object(R, "apply_v1_code_type_and_ccl_numbering", side_effect=lambda r, t: r), \
            mock.patch.object(R, "build_roster_for_prompt", return_value=[{"id": 1}]):
        stats = R.run_per_fir_ai_backfill(
            conn, "run-2", kb_items=[],
            drug_client=mock.Mock(), accused_client=mock.Mock(),
            settings=SETTINGS,
        )

    assert accused_calls == ["C1"]
    assert stats["drugs"]["failed"] == 1
    assert stats["accused"]["success"] == 1
    assert conn.rollbacks >= 1
    assert conn.commits >= 1


def test_db_unusable_after_drug_failure_aborts():
    conn = FakeConn(usable=False)
    facts = {"C1": ("V2", "brief facts long enough here")}
    by_crime = {"C1": [_accused_rec("a1")]}

    with mock.patch.object(R, "_pairs", return_value=[("C1",)]), \
            mock.patch.object(R, "_brief_facts", return_value=facts), \
            mock.patch.object(R, "_load_accused_by_crime", return_value=by_crime), \
            mock.patch.object(R, "_ai_already_settled", return_value=False), \
            mock.patch.object(
                R, "process_drug_for_crime",
                side_effect=RuntimeError("ssl connection closed"),
            ), \
            mock.patch.object(R, "process_accused_ai_for_crime") as accused_proc, \
            mock.patch.object(R, "apply_v1_code_type_and_ccl_numbering", side_effect=lambda r, t: r), \
            mock.patch.object(R, "build_roster_for_prompt", return_value=[]):
        try:
            R.run_per_fir_ai_backfill(
                conn, "run-3", kb_items=[],
                drug_client=mock.Mock(), accused_client=mock.Mock(),
                settings=SETTINGS,
            )
            raised = False
        except RuntimeError:
            raised = True

    assert raised
    accused_proc.assert_not_called()


def test_restart_skips_settled_both_types():
    conn = FakeConn()
    facts = {"C1": ("V2", "brief facts long enough here")}
    by_crime = {"C1": [_accused_rec("a1")]}

    with mock.patch.object(R, "_pairs", return_value=[("C1",)]), \
            mock.patch.object(R, "_brief_facts", return_value=facts), \
            mock.patch.object(R, "_load_accused_by_crime", return_value=by_crime), \
            mock.patch.object(R, "_ai_already_settled", return_value=True), \
            mock.patch.object(R, "process_drug_for_crime") as drug_proc, \
            mock.patch.object(R, "process_accused_ai_for_crime") as accused_proc, \
            mock.patch.object(R, "apply_v1_code_type_and_ccl_numbering", side_effect=lambda r, t: r), \
            mock.patch.object(R, "build_roster_for_prompt", return_value=[{"id": 1}]):
        stats = R.run_per_fir_ai_backfill(
            conn, "run-4", kb_items=[],
            drug_client=mock.Mock(), accused_client=mock.Mock(),
            settings=SETTINGS,
        )

    drug_proc.assert_not_called()
    accused_proc.assert_not_called()
    assert stats["drugs"]["skipped"] == 1
    assert stats["accused"]["skipped"] == 1
    assert stats["drugs"]["processed"] == 0
    assert stats["accused"]["processed"] == 0


def test_commit_per_enrichment_type():
    conn = FakeConn()
    facts = {
        "C1": ("V2", "brief facts one long enough xx"),
        "C2": ("V2", "brief facts two long enough xx"),
    }
    by_crime = {
        "C1": [_accused_rec("a1", "C1")],
        "C2": [_accused_rec("a2", "C2")],
    }

    with mock.patch.object(R, "_pairs", return_value=[("C1",), ("C2",)]), \
            mock.patch.object(R, "_brief_facts", return_value=facts), \
            mock.patch.object(R, "_load_accused_by_crime", return_value=by_crime), \
            mock.patch.object(R, "_ai_already_settled", return_value=False), \
            mock.patch.object(
                R, "process_drug_for_crime",
                return_value={"outcome": "success", "rejected": 0, "generic_rejected": 0, "unsupported": 0},
            ), \
            mock.patch.object(
                R, "process_accused_ai_for_crime",
                return_value={"outcome": "empty", "rows": [{"accused_id": "x", "input_hash": "h"}], "records": []},
            ), \
            mock.patch.object(R, "upsert_rows", return_value={"inserted": 1}), \
            mock.patch.object(R, "apply_v1_code_type_and_ccl_numbering", side_effect=lambda r, t: r), \
            mock.patch.object(R, "build_roster_for_prompt", return_value=[{"id": 1}]):
        R.run_per_fir_ai_backfill(
            conn, "run-5", kb_items=[],
            drug_client=mock.Mock(), accused_client=mock.Mock(),
            settings=SETTINGS,
        )

    # 2 FIRs × (drug commit + accused commit) = 4
    assert conn.commits == 4, conn.commits


def test_multiple_accused_one_model_call():
    conn = FakeConn()
    records = [_accused_rec("a1"), _accused_rec("a2", code="A2"), _accused_rec("a3", code="A3")]
    facts = {"C1": ("V2", "brief facts long enough here")}
    call_count = {"n": 0}

    def fake_extract(client, text, roster, allowed_ids, allowed_codes, max_retries=1):
        call_count["n"] += 1
        assert len(allowed_ids) == 3
        assert len(roster) == 3
        return (
            {"accused": [
                {"accused_id": "a1", "role_in_crime": "supplier"},
                {"accused_id": "a2", "role_in_crime": "carrier"},
            ]},
            1,
            "{}",
        )

    with mock.patch.object(R, "_ai_already_settled", return_value=False), \
            mock.patch.object(R, "_wait_for_ollama", return_value=True), \
            mock.patch.object(R, "extract_accused_with_retry", side_effect=fake_extract), \
            mock.patch.object(R, "record_ai_attempt"), \
            mock.patch.object(R, "build_roster_for_prompt", return_value=[{"id": i} for i in range(3)]), \
            mock.patch.object(R, "enrich_existing_accused", return_value={"rows": [
                {"accused_id": "a1"}, {"accused_id": "a2"}, {"accused_id": "a3"},
            ]}), \
            mock.patch.object(R, "_pace_after_request"):
        result = R.process_accused_ai_for_crime(
            conn, "C1", records, facts["C1"], mock.Mock(), SETTINGS, [],
        )

    assert call_count["n"] == 1
    assert result["outcome"] == "success"
    assert len(result["rows"]) == 3


def test_process_drug_records_failure_and_retries_policy():
    conn = FakeConn()
    client = mock.Mock()

    with mock.patch.object(R, "_ai_already_settled", return_value=False), \
            mock.patch.object(R, "_wait_for_ollama", return_value=True), \
            mock.patch.object(
                R, "extract_with_retry",
                side_effect=AIExtractionError("timeout", "timed out"),
            ), \
            mock.patch.object(R, "record_ai_attempt") as record, \
            mock.patch.object(R, "_pace_after_request"):
        result = R.process_drug_for_crime(
            conn, "run", "C1", "V2", "brief facts text here",
            client, [], SETTINGS, [],
        )

    assert result["outcome"] == "failed"
    record.assert_called_once()
    assert record.call_args[0][4] == "timeout"


def test_process_drug_success_writes_and_settles():
    conn = FakeConn()
    parsed = {"drugs": [{"raw_drug_name": "Ganja", "quantity": 1, "unit": "kg"}]}
    rows = [{"extraction_id": "e1", "crime_id": "C1", "input_hash": "h"}]
    # ai_drug_rows may return a list-like with .rejections
    row_list = mock.MagicMock()
    row_list.__iter__ = mock.Mock(return_value=iter(rows))
    row_list.__bool__ = mock.Mock(return_value=True)
    row_list.rejections = []

    with mock.patch.object(R, "_ai_already_settled", return_value=False), \
            mock.patch.object(R, "_wait_for_ollama", return_value=True), \
            mock.patch.object(
                R, "extract_with_retry",
                return_value=(parsed, 1, "{}"),
            ), \
            mock.patch.object(R, "ai_drug_rows", return_value=row_list), \
            mock.patch.object(R, "record_ai_attempt") as record, \
            mock.patch.object(R, "upsert_rows") as upsert, \
            mock.patch.object(R, "_pace_after_request"):
        result = R.process_drug_for_crime(
            conn, "run", "C1", "V2", "brief facts text here",
            mock.Mock(), [("ganja", "Ganja")], SETTINGS, [],
        )

    assert result["outcome"] == "success"
    assert record.call_args[0][4] == "success"
    upsert.assert_called_once()


def test_stop_check_halts_between_firs():
    conn = FakeConn()
    facts = {
        "C1": ("V2", "brief facts one long enough xx"),
        "C2": ("V2", "brief facts two long enough xx"),
    }
    calls = []

    def drug(*args, **kwargs):
        calls.append(args[2])
        return {"outcome": "success", "rejected": 0, "generic_rejected": 0, "unsupported": 0}

    stop_after_first = {"n": 0}

    def stop_check():
        # Stop before second FIR (after first FIR's drug+accused done)
        return stop_after_first["n"] >= 1

    def accused(*args, **kwargs):
        stop_after_first["n"] += 1
        return {"outcome": "empty", "rows": [], "records": []}

    with mock.patch.object(R, "_pairs", return_value=[("C1",), ("C2",)]), \
            mock.patch.object(R, "_brief_facts", return_value=facts), \
            mock.patch.object(R, "_load_accused_by_crime", return_value={
                "C1": [_accused_rec("a1", "C1")],
                "C2": [_accused_rec("a2", "C2")],
            }), \
            mock.patch.object(R, "_ai_already_settled", return_value=False), \
            mock.patch.object(R, "process_drug_for_crime", side_effect=drug), \
            mock.patch.object(R, "process_accused_ai_for_crime", side_effect=accused), \
            mock.patch.object(R, "upsert_rows", return_value={}), \
            mock.patch.object(R, "apply_v1_code_type_and_ccl_numbering", side_effect=lambda r, t: r), \
            mock.patch.object(R, "build_roster_for_prompt", return_value=[{"id": 1}]):
        stats = R.run_per_fir_ai_backfill(
            conn, "run-6", kb_items=[],
            drug_client=mock.Mock(), accused_client=mock.Mock(),
            settings=SETTINGS, stop_check=stop_check,
        )

    assert calls == ["C1"]
    assert stats["status"] == "stopped"


def test_retry_exhausted_settlement_helper():
    """timeout/error count >= 3 is settled (no re-call)."""
    conn = mock.MagicMock()
    cur = mock.MagicMock()
    cur.__enter__ = mock.MagicMock(return_value=cur)
    cur.__exit__ = mock.MagicMock(return_value=False)
    cur.fetchall.return_value = [("timeout", 2), ("error", 1)]
    conn.cursor.return_value = cur
    assert R._ai_already_settled(conn, "C1", "digest") is True

    cur.fetchall.return_value = [("timeout", 1), ("error", 1)]
    assert R._ai_already_settled(conn, "C1", "digest") is False

    cur.fetchall.return_value = [("success", 1)]
    assert R._ai_already_settled(conn, "C1", "digest") is True


def test_sequential_order_drug_then_accused_per_fir():
    conn = FakeConn()
    facts = {
        "C1": ("V2", "brief facts one long enough xx"),
        "C2": ("V2", "brief facts two long enough xx"),
    }
    order = []

    def drug(*args, **kwargs):
        order.append(("drug", args[2]))
        return {"outcome": "success", "rejected": 0, "generic_rejected": 0, "unsupported": 0}

    def accused(*args, **kwargs):
        order.append(("accused", args[1]))
        return {"outcome": "success", "rows": [{"accused_id": "x", "input_hash": "h"}], "records": []}

    with mock.patch.object(R, "_pairs", return_value=[("C1",), ("C2",)]), \
            mock.patch.object(R, "_brief_facts", return_value=facts), \
            mock.patch.object(R, "_load_accused_by_crime", return_value={
                "C1": [_accused_rec("a1", "C1")],
                "C2": [_accused_rec("a2", "C2")],
            }), \
            mock.patch.object(R, "_ai_already_settled", return_value=False), \
            mock.patch.object(R, "process_drug_for_crime", side_effect=drug), \
            mock.patch.object(R, "process_accused_ai_for_crime", side_effect=accused), \
            mock.patch.object(R, "upsert_rows", return_value={"inserted": 1}), \
            mock.patch.object(R, "apply_v1_code_type_and_ccl_numbering", side_effect=lambda r, t: r), \
            mock.patch.object(R, "build_roster_for_prompt", return_value=[{"id": 1}]):
        R.run_per_fir_ai_backfill(
            conn, "run-7", kb_items=[],
            drug_client=mock.Mock(), accused_client=mock.Mock(),
            settings=SETTINGS,
        )

    assert order == [
        ("drug", "C1"),
        ("accused", "C1"),
        ("drug", "C2"),
        ("accused", "C2"),
    ]


def run_all():
    tests = [
        test_drug_digest_stable,
        test_accused_digest_includes_roster,
        test_mixed_settled_pending_runs_only_pending,
        test_drug_failure_still_attempts_accused,
        test_db_unusable_after_drug_failure_aborts,
        test_restart_skips_settled_both_types,
        test_commit_per_enrichment_type,
        test_multiple_accused_one_model_call,
        test_process_drug_records_failure_and_retries_policy,
        test_process_drug_success_writes_and_settles,
        test_stop_check_halts_between_firs,
        test_retry_exhausted_settlement_helper,
        test_sequential_order_drug_then_accused_per_fir,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"[PASS] {fn.__name__}")
        except Exception as exc:
            failed += 1
            print(f"[FAIL] {fn.__name__}: {type(exc).__name__}: {exc}")
            import traceback
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return failed


if __name__ == "__main__":
    raise SystemExit(run_all())
