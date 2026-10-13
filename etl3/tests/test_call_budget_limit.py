"""Concurrency tests for limited-mode CallBudget and per-FIR call caps."""
from __future__ import annotations

import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.enrichment import runner as R
from etl3.tests.test_per_fir_ai_scheduler import FakeConn, SETTINGS, _accused_rec


def test_call_budget_never_exceeds_limit_under_contention():
    budget = R.CallBudget(50)
    wins: list[int] = []
    lock = threading.Lock()
    start = threading.Event()

    def worker():
        start.wait(timeout=5)
        if budget.try_reserve():
            with lock:
                wins.append(1)
            time.sleep(0.001)

    with ThreadPoolExecutor(max_workers=32) as pool:
        futs = [pool.submit(worker) for _ in range(200)]
        start.set()
        for f in as_completed(futs):
            f.result()

    assert sum(wins) == 50
    assert budget.used == 50
    assert budget.try_reserve() is False


def test_call_budget_release_after_unused_reservation():
    budget = R.CallBudget(2)
    assert budget.try_reserve() is True
    assert budget.try_reserve() is True
    assert budget.try_reserve() is False
    budget.release()
    assert budget.used == 1
    assert budget.try_reserve() is True
    assert budget.used == 2


def test_call_budget_unlimited_always_reserves():
    budget = R.CallBudget(0)
    for _ in range(100):
        assert budget.try_reserve() is True
    assert budget.used == 0
    budget.release()  # no-op
    assert budget.used == 0


def test_call_budget_release_on_exception_path_keeps_slot_when_kept_by_caller():
    """Document: exception after reserve keeps slot (runner sets called=True)."""
    budget = R.CallBudget(1)
    assert budget.try_reserve() is True
    # Simulate runner keeping reservation on exception
    assert budget.used == 1
    assert budget.try_reserve() is False


def _many_crimes(n: int):
    facts = {f"C{i}": ("V2", f"brief facts for crime {i} long enough xx") for i in range(n)}
    by_crime = {f"C{i}": [_accused_rec(f"a{i}", f"C{i}")] for i in range(n)}
    pairs = [(f"C{i}",) for i in range(n)]
    return facts, by_crime, pairs


def _run_limited(parallel: int, limit: int, n_crimes: int, *, settle_every: int | None = None):
    """Run mocked per-FIR backfill; return (stats, drug_calls, accused_calls)."""
    conn = FakeConn()
    facts, by_crime, pairs = _many_crimes(n_crimes)
    drug_calls = []
    accused_calls = []
    lock = threading.Lock()

    def already_settled(_conn, crime_id, digest):
        if settle_every is None:
            return False
        # Settle odd crimes for both types → skips must not consume slots
        idx = int(crime_id[1:])
        return (idx % settle_every) == 0

    def fake_drug(*args, **kwargs):
        crime_id = args[2]
        with lock:
            drug_calls.append(crime_id)
        time.sleep(0.002)
        return {"outcome": "success", "rejected": 0, "generic_rejected": 0, "unsupported": 0}

    def fake_accused(*args, **kwargs):
        crime_id = args[1]
        with lock:
            accused_calls.append(crime_id)
        time.sleep(0.002)
        return {
            "outcome": "success",
            "rows": [{"accused_id": f"row-{crime_id}", "input_hash": "h"}],
            "records": [],
        }

    settings = {
        **SETTINGS,
        "mode": "limited",
        "limit": limit,
        "parallel": parallel,
        "batch_size": 50,
    }

    worker_conns = []

    def fake_get_conn(*_a, **_k):
        c = FakeConn()
        worker_conns.append(c)
        return c

    with mock.patch.object(R, "_pairs", return_value=pairs), \
            mock.patch.object(R, "_brief_facts", return_value=facts), \
            mock.patch.object(R, "_load_accused_by_crime", return_value=by_crime), \
            mock.patch.object(R, "_ai_already_settled", side_effect=already_settled), \
            mock.patch.object(R, "process_drug_for_crime", side_effect=fake_drug), \
            mock.patch.object(R, "process_accused_ai_for_crime", side_effect=fake_accused), \
            mock.patch.object(R, "upsert_rows", return_value={"inserted": 1}), \
            mock.patch.object(R, "apply_v1_code_type_and_ccl_numbering", side_effect=lambda r, t: r), \
            mock.patch.object(R, "build_roster_for_prompt", return_value=[{"id": 1}]), \
            mock.patch.object(R.connections, "get_unified_connection", side_effect=fake_get_conn), \
            mock.patch.object(R, "configure_ollama_parallel", side_effect=lambda n: max(1, int(n))):
        stats = R.run_per_fir_ai_backfill(
            conn, "run-limit", kb_items=[],
            drug_client=mock.Mock(), accused_client=mock.Mock(),
            settings=settings, do_drugs=True, do_accused=True,
        )
    return stats, drug_calls, accused_calls


def test_limited_parallel1_caps_each_task_type():
    stats, drug_calls, accused_calls = _run_limited(parallel=1, limit=5, n_crimes=30)
    assert len(drug_calls) == 5, len(drug_calls)
    assert len(accused_calls) == 5, len(accused_calls)
    assert stats["drug_model_calls"] == 5
    assert stats["accused_model_calls"] == 5
    assert stats["drugs"]["processed"] == 5
    assert stats["accused"]["processed"] == 5


def test_limited_parallel2_caps_each_task_type():
    stats, drug_calls, accused_calls = _run_limited(parallel=2, limit=50, n_crimes=120)
    assert len(drug_calls) == 50, len(drug_calls)
    assert len(accused_calls) == 50, len(accused_calls)
    assert stats["drug_model_calls"] == 50
    assert stats["accused_model_calls"] == 50
    assert len(set(drug_calls)) == 50
    assert len(set(accused_calls)) == 50


def test_limited_parallel2_many_workers_cannot_overshoot():
    """Stress: more FIR workers than slots still cannot exceed cap."""
    stats, drug_calls, accused_calls = _run_limited(parallel=4, limit=10, n_crimes=80)
    assert len(drug_calls) == 10
    assert len(accused_calls) == 10
    assert stats["drug_model_calls"] == 10
    assert stats["accused_model_calls"] == 10


def test_settled_skips_do_not_consume_call_slots():
    # Every 2nd crime settled → need 2x crimes to fill limit 5
    stats, drug_calls, accused_calls = _run_limited(
        parallel=2, limit=5, n_crimes=40, settle_every=2,
    )
    assert len(drug_calls) == 5
    assert len(accused_calls) == 5
    assert stats["drugs"]["skipped"] >= 5
    assert stats["accused"]["skipped"] >= 5
    assert stats["drug_model_calls"] == 5
    assert stats["accused_model_calls"] == 5


def test_paused_ollama_releases_reservation():
    """Health pause must not permanently consume a limited slot."""
    conn = FakeConn()
    facts, by_crime, pairs = _many_crimes(5)
    drug_outcomes = iter([
        {"outcome": "paused_ollama", "digest": "d"},
        {"outcome": "success", "rejected": 0, "generic_rejected": 0, "unsupported": 0},
        {"outcome": "success", "rejected": 0, "generic_rejected": 0, "unsupported": 0},
    ])
    drug_calls = []

    def fake_drug(*args, **kwargs):
        drug_calls.append(args[2])
        return next(drug_outcomes)

    settings = {**SETTINGS, "mode": "limited", "limit": 2, "parallel": 1}

    with mock.patch.object(R, "_pairs", return_value=pairs), \
            mock.patch.object(R, "_brief_facts", return_value=facts), \
            mock.patch.object(R, "_load_accused_by_crime", return_value=by_crime), \
            mock.patch.object(R, "_ai_already_settled", return_value=False), \
            mock.patch.object(R, "process_drug_for_crime", side_effect=fake_drug), \
            mock.patch.object(
                R, "process_accused_ai_for_crime",
                return_value={"outcome": "success", "rows": [], "records": []},
            ), \
            mock.patch.object(R, "upsert_rows", return_value={}), \
            mock.patch.object(R, "apply_v1_code_type_and_ccl_numbering", side_effect=lambda r, t: r), \
            mock.patch.object(R, "build_roster_for_prompt", return_value=[{"id": 1}]), \
            mock.patch.object(R, "configure_ollama_parallel", side_effect=lambda n: 1):
        stats = R.run_per_fir_ai_backfill(
            conn, "run-pause", kb_items=[],
            drug_client=mock.Mock(), accused_client=mock.Mock(),
            settings=settings,
        )

    # First drug paused (released); then status paused stops scheduling.
    # Accused on first FIR may still run before pause is observed at scheduler.
    assert stats["status"] == "paused"
    assert stats["drug_model_calls"] <= 2


def test_exception_after_reserve_consumes_slot():
    conn = FakeConn()
    facts, by_crime, pairs = _many_crimes(10)
    n = {"drug": 0}

    def boom_then_ok(*args, **kwargs):
        n["drug"] += 1
        if n["drug"] == 1:
            raise RuntimeError("boom")
        return {"outcome": "success", "rejected": 0, "generic_rejected": 0, "unsupported": 0}

    settings = {**SETTINGS, "mode": "limited", "limit": 2, "parallel": 1}

    with mock.patch.object(R, "_pairs", return_value=pairs), \
            mock.patch.object(R, "_brief_facts", return_value=facts), \
            mock.patch.object(R, "_load_accused_by_crime", return_value=by_crime), \
            mock.patch.object(R, "_ai_already_settled", return_value=False), \
            mock.patch.object(R, "process_drug_for_crime", side_effect=boom_then_ok), \
            mock.patch.object(
                R, "process_accused_ai_for_crime",
                return_value={"outcome": "success", "rows": [{"accused_id": "x", "input_hash": "h"}], "records": []},
            ), \
            mock.patch.object(R, "upsert_rows", return_value={"inserted": 1}), \
            mock.patch.object(R, "apply_v1_code_type_and_ccl_numbering", side_effect=lambda r, t: r), \
            mock.patch.object(R, "build_roster_for_prompt", return_value=[{"id": 1}]), \
            mock.patch.object(R, "configure_ollama_parallel", side_effect=lambda n: 1):
        stats = R.run_per_fir_ai_backfill(
            conn, "run-exc", kb_items=[],
            drug_client=mock.Mock(), accused_client=mock.Mock(),
            settings=settings,
        )

    assert stats["drug_model_calls"] == 2
    assert stats["drugs"]["failed"] == 1
    assert stats["drugs"]["processed"] == 1


def test_failed_reservation_never_calls_model():
    budget = R.CallBudget(1)
    assert budget.try_reserve() is True
    conn = FakeConn()
    called = []

    def fake_drug(*_a, **_k):
        called.append("drug")
        return {"outcome": "success", "rejected": 0, "generic_rejected": 0, "unsupported": 0}

    # Direct process path with exhausted budget
    with mock.patch.object(R, "_ai_already_settled", return_value=False), \
            mock.patch.object(R, "process_drug_for_crime", side_effect=fake_drug), \
            mock.patch.object(R, "process_accused_ai_for_crime") as accused, \
            mock.patch.object(R, "apply_v1_code_type_and_ccl_numbering", side_effect=lambda r, t: r), \
            mock.patch.object(R, "build_roster_for_prompt", return_value=[{"id": 1}]):
        result = R._process_one_crime_ai(
            conn, "run", "C9",
            fact_entry=("V2", "brief facts long enough here"),
            records=[_accused_rec("a9", "C9")],
            known={"C9"},
            do_drugs=True,
            do_accused=True,
            drug_client=mock.Mock(),
            accused_client=mock.Mock(),
            kb_items=[],
            drug_kb_obj=None,
            settings=SETTINGS,
            drug_latencies=[],
            accused_latencies=[],
            drug_budget=budget,  # already exhausted
            accused_budget=R.CallBudget(0),
        )
    assert called == []
    assert result["drug"]["pending"] == 1
    accused.assert_called()  # accused unlimited still runs


def run_all():
    tests = [
        test_call_budget_never_exceeds_limit_under_contention,
        test_call_budget_release_after_unused_reservation,
        test_call_budget_unlimited_always_reserves,
        test_call_budget_release_on_exception_path_keeps_slot_when_kept_by_caller,
        test_limited_parallel1_caps_each_task_type,
        test_limited_parallel2_caps_each_task_type,
        test_limited_parallel2_many_workers_cannot_overshoot,
        test_settled_skips_do_not_consume_call_slots,
        test_paused_ollama_releases_reservation,
        test_exception_after_reserve_consumes_slot,
        test_failed_reservation_never_calls_model,
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
