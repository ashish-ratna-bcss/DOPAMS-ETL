"""
Phase 5: incremental sync, cursors, ordering, gaps, run log, reconciliation.

Run with: python etl3/tests/test_phase5_incremental.py

Sentinel rows are rolled back, or deleted in finally when a commit is
required to simulate a crash. Nothing here truncates a unified table.
"""
import sys
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.db import connections
from etl3.identity import person_matching as pm
from etl3.loaders.common import write_source_observation
from etl3.merger import current_state as cs
from etl3.run_phase4_consolidation import run_with_run_log
from etl3.sync.catchup import revision_run_id
from etl3.sync.cursor import advance_cursor, known_run_ids, select_high_water
from etl3.sync.reconcile import classify_module

T0 = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
T1 = datetime(2024, 6, 1, 0, 0, tzinfo=timezone.utc)
T2 = datetime(2024, 12, 1, 0, 0, tzinfo=timezone.utc)


def check(label, fn):
    try:
        fn()
        print(f"[PASS] {label}")
    except Exception as e:
        print(f"[FAIL] {label}: {type(e).__name__}: {e}")
        raise


def _add(writer, pk, facts, ts, obs, run="run"):
    writer.add(
        pk,
        source_system="V2",
        source_record_id=pk,
        mapped_fields={"brief_facts": facts, "fir_date": "2020-01-01T00:00:00"},
        extra_fields={},
        source_run_id=run,
        current_as_of=ts,
        observation_id=obs,
    )


def test_ordering_matrix_is_deterministic():
    """A→B, B→A, A→B→A, A→A→B, B→A→B all end on the newest observation."""
    conn = connections.get_unified_connection()
    try:
        writer = cs.UnifiedBatchWriter(conn, "crimes_unified", "crime_id", "crime")
        cases = {
            "__p5_ab": [(T1, 1, "A"), (T2, 2, "B")],
            "__p5_ba": [(T2, 2, "B"), (T1, 1, "A")],
            "__p5_aba": [(T1, 1, "A"), (T2, 2, "B"), (T1, 3, "A")],
            "__p5_aab": [(T1, 1, "A"), (T1, 2, "A2"), (T2, 3, "B")],
            "__p5_bab": [(T2, 2, "B"), (T1, 1, "A"), (T2, 3, "B2")],
        }
        for pk, steps in cases.items():
            for ts, obs, facts in steps:
                _add(writer, pk, facts, ts, obs)
            assert writer._existing[pk]["brief_facts"].startswith("B"), (pk, writer._existing[pk]["brief_facts"])
    finally:
        conn.rollback()
        conn.close()


def test_change_then_revert_and_multi_field_change():
    conn = connections.get_unified_connection()
    try:
        writer = cs.UnifiedBatchWriter(conn, "crimes_unified", "crime_id", "crime")
        t3 = datetime(2025, 1, 1, tzinfo=timezone.utc)
        _add(writer, "__p5_revert", "A", T1, 1)
        writer.add(
            "__p5_revert",
            source_system="V2",
            source_record_id="__p5_revert",
            mapped_fields={"brief_facts": "B", "case_status": "open"},
            extra_fields={},
            source_run_id="run",
            current_as_of=T2,
            observation_id=2,
        )
        _add(writer, "__p5_revert", "A", t3, 3)
        assert writer._existing["__p5_revert"]["brief_facts"] == "A"
        fields = [row[2] for row in writer._pending_change_log if row[1] == "__p5_revert"]
        assert fields.count("brief_facts") == 3
        assert "case_status" in fields
    finally:
        conn.rollback()
        conn.close()


def test_same_run_revision_id_is_stable():
    from etl3.sync.catchup import _choose_run_id

    spec = {"source_system": "V2"}
    row = {"etl_run_id": "11111111-1111-1111-1111-111111111111"}
    assert _choose_run_id(spec, row, T2, set(), "pk") == row["etl_run_id"]
    revised = _choose_run_id(spec, row, T2, {("pk", row["etl_run_id"])}, "pk")
    assert revised == _choose_run_id(spec, row, T2, {("pk", row["etl_run_id"])}, "pk")
    assert revised != row["etl_run_id"] and revised.startswith(row["etl_run_id"] + "#m:")
    null_row = {"etl_run_id": None}
    assert _choose_run_id({"source_system": "V2"}, null_row, None, set(), "pk") == "__initial_no_run_id__"
    assert _choose_run_id({"source_system": "V1"}, {}, T1, set(), "pk") == "__initial__"


def test_equal_timestamp_orders_by_observation_id():
    conn = connections.get_unified_connection()
    try:
        writer = cs.UnifiedBatchWriter(conn, "crimes_unified", "crime_id", "crime")
        _add(writer, "__p5_eq_lo", "low", T1, 1)
        _add(writer, "__p5_eq_lo", "high", T1, 5)
        assert writer._existing["__p5_eq_lo"]["brief_facts"] == "high"
        _add(writer, "__p5_eq_hi", "high", T1, 5)
        _add(writer, "__p5_eq_hi", "low", T1, 1)
        assert writer._existing["__p5_eq_hi"]["brief_facts"] == "high"
    finally:
        conn.rollback()
        conn.close()


def test_null_and_future_timestamps_do_not_clobber():
    conn = connections.get_unified_connection()
    try:
        writer = cs.UnifiedBatchWriter(conn, "crimes_unified", "crime_id", "crime")
        _add(writer, "__p5_null", "kept", T1, 1)
        before = len(writer._pending_change_log)
        _add(writer, "__p5_null", "gone", None, 2)
        assert writer._existing["__p5_null"]["brief_facts"] == "kept"
        assert len(writer._pending_change_log) == before

        future = datetime.now(timezone.utc) + timedelta(days=10)
        _add(writer, "__p5_future", "kept", T1, 1)
        _add(writer, "__p5_future", "future", future, 2)
        assert writer._existing["__p5_future"]["brief_facts"] == "kept"

        _add(writer, "__p5_future_first", "future", future, 1)
        _add(writer, "__p5_future_first", "sane", T1, 2)
        assert writer._existing["__p5_future_first"]["brief_facts"] == "sane"
    finally:
        conn.rollback()
        conn.close()


def test_explicit_null_clears_and_replay_does_not_restore():
    """A newer full snapshot may clear a field. An older replay must not put it back."""
    conn = connections.get_unified_connection()
    try:
        writer = cs.UnifiedBatchWriter(conn, "crimes_unified", "crime_id", "crime")
        _add(writer, "__p5_clear", "hello", T1, 1)
        _add(writer, "__p5_clear", None, T2, 2)
        assert writer._existing["__p5_clear"]["brief_facts"] is None
        _add(writer, "__p5_clear", "hello", T1, 3)
        assert writer._existing["__p5_clear"]["brief_facts"] is None
    finally:
        conn.rollback()
        conn.close()


def test_repeated_record_is_idempotent_and_representation_is_not_a_change():
    conn = connections.get_unified_connection()
    try:
        writer = cs.UnifiedBatchWriter(conn, "crimes_unified", "crime_id", "crime")
        naive = datetime(2020, 1, 1, 0, 0)
        aware = datetime(2020, 1, 1, 0, 0, tzinfo=timezone.utc)
        writer.add(
            "__p5_repr",
            source_system="V2",
            source_record_id="__p5_repr",
            mapped_fields={"brief_facts": "same", "fir_date": naive, "occurrence_year": Decimal("2020")},
            extra_fields={},
            source_run_id="run",
            current_as_of=T1,
            observation_id=1,
        )
        changes_after_first = len(writer._pending_change_log)
        writer.add(
            "__p5_repr",
            source_system="V2",
            source_record_id="__p5_repr",
            mapped_fields={"brief_facts": "same", "fir_date": aware, "occurrence_year": 2020},
            extra_fields={},
            source_run_id="run",
            current_as_of=T1,
            observation_id=2,
        )
        assert writer.counts["unchanged"] >= 1
        assert len(writer._pending_change_log) == changes_after_first
        assert cs._coerce_value("Y", "boolean") is True
        assert cs._coerce_value("N", "boolean") is False
        assert cs._values_differ(False, cs._coerce_value("N", "boolean")) is False
        assert cs._values_differ(Decimal("1.5"), 1.5) is False
        # whitespace is not normalized; a real difference stays a difference
        assert cs._values_differ("vikky ", " vikky") is True
    finally:
        conn.rollback()
        conn.close()


def test_distinct_sources_stay_distinct():
    conn = connections.get_unified_connection()
    try:
        writer = cs.UnifiedBatchWriter(conn, "crimes_unified", "crime_id", "crime")
        _add(writer, "__p5_v1ish", "one", T1, 1, run="v1")
        writer.add(
            "__p5_v2ish",
            source_system="V1",
            source_record_id="__p5_v2ish",
            mapped_fields={"brief_facts": "two"},
            extra_fields={},
            source_run_id="v1-run",
            current_as_of=T2,
            observation_id=2,
        )
        assert writer._existing["__p5_v1ish"]["brief_facts"] == "one"
        assert writer._existing["__p5_v2ish"]["brief_facts"] == "two"
        assert writer._existing["__p5_v1ish"]["source_system"] == "V2"
        assert writer._existing["__p5_v2ish"]["source_system"] == "V1"
    finally:
        conn.rollback()
        conn.close()


def test_fetch_latest_keeps_equal_watermark_and_ignores_far_future():
    conn = connections.get_unified_connection()
    run = str(uuid.uuid4())
    record = "__p5_fetch__"
    try:
        write_source_observation(
            conn, "crimes_source", source_system="V2", source_table="crimes",
            source_record_id=record, source_run_id="eq-low", source_created_at=T1,
            source_modified_at=T1, source_fetched_at=None, payload={"crime_id": record, "brief_facts": "low"},
            consolidation_run_id=run,
        )
        write_source_observation(
            conn, "crimes_source", source_system="V2", source_table="crimes",
            source_record_id=record, source_run_id="eq-high", source_created_at=T1,
            source_modified_at=T1, source_fetched_at=None, payload={"crime_id": record, "brief_facts": "high"},
            consolidation_run_id=run,
        )
        rows = {
            row[0]: row for row in cs.fetch_latest_by_record_id(conn, "crimes_source", "V2")
            if row[0] == record
        }
        assert rows[record][1] == "eq-high"

        future = datetime.now(timezone.utc) + timedelta(days=30)
        write_source_observation(
            conn, "crimes_source", source_system="V2", source_table="crimes",
            source_record_id=record + "f", source_run_id="future", source_created_at=future,
            source_modified_at=future, source_fetched_at=None,
            payload={"crime_id": record + "f", "brief_facts": "future"},
            consolidation_run_id=run,
        )
        write_source_observation(
            conn, "crimes_source", source_system="V2", source_table="crimes",
            source_record_id=record + "f", source_run_id="sane", source_created_at=T1,
            source_modified_at=T1, source_fetched_at=None,
            payload={"crime_id": record + "f", "brief_facts": "sane"},
            consolidation_run_id=run,
        )
        rows = {
            row[0]: row for row in cs.fetch_latest_by_record_id(conn, "crimes_source", "V2")
            if row[0] == record + "f"
        }
        assert rows[record + "f"][1] == "sane"
    finally:
        conn.rollback()
        conn.close()


def test_cursor_does_not_regress_reset_or_jump():
    assert select_high_water({"b", "a"}, {"a": 5, "b": 5}) == ("b", 5)
    assert select_high_water({"late", "old"}, {"old": 1}) == ("old", 1)
    assert select_high_water({"__initial__", "run-9"}, {"run-9": 9}) == ("run-9", 9)
    token = revision_run_id("abc", T1)
    assert token and len(token) <= 100 and "#m:" in token
    assert select_high_water({token, "run-1"}, {"run-1": 1, token: 99}) == ("run-1", 1)
    assert revision_run_id("abc", T1) == token

    conn = connections.get_unified_connection()
    try:
        assert advance_cursor(conn, "V1", "__p5_probe__", None, None, None) == "skipped_empty"
        assert advance_cursor(conn, "V1", "__p5_probe__", "run-2", 2, None) == "advanced"
        with conn.cursor() as cur:
            cur.execute(
                "SELECT last_processed_source_run_id FROM consolidation_cursor WHERE source_module='__p5_probe__'"
            )
            assert cur.fetchone()[0] == "run-2"
        assert advance_cursor(conn, "V1", "__p5_probe__", None, None, 2) == "skipped_empty"
        assert advance_cursor(conn, "V1", "__p5_probe__", "run-1", 1, 2) == "refused_regression"
        with conn.cursor() as cur:
            cur.execute(
                "SELECT last_processed_source_run_id, status FROM consolidation_cursor WHERE source_module='__p5_probe__'"
            )
            run_id, status = cur.fetchone()
        assert run_id == "run-2"
        assert status == "idle"
        assert advance_cursor(conn, "V1", "__p5_probe__", "run-2", 2, 2) == "unchanged"
        # A stored run the source can no longer place must not freeze the cursor.
        assert advance_cursor(conn, "V1", "__p5_probe__", "run-9", 9, None) == "advanced"
        with conn.cursor() as cur:
            cur.execute(
                "SELECT last_processed_source_run_id FROM consolidation_cursor WHERE source_module='__p5_probe__'"
            )
            assert cur.fetchone()[0] == "run-9"
    finally:
        conn.rollback()
        conn.close()


def test_known_runs_keep_late_records_and_replays_insert_nothing():
    conn = connections.get_unified_connection()
    run = str(uuid.uuid4())
    try:
        first = write_source_observation(
            conn, "hierarchy_source", source_system="V2", source_table="hierarchy",
            source_record_id="__p5_late__", source_run_id="old-run",
            source_created_at=T0, source_modified_at=T0, source_fetched_at=None,
            payload={"ps_code": "__p5_late__"}, consolidation_run_id=run,
        )
        second = write_source_observation(
            conn, "hierarchy_source", source_system="V2", source_table="hierarchy",
            source_record_id="__p5_late__", source_run_id="old-run",
            source_created_at=T0, source_modified_at=T0, source_fetched_at=None,
            payload={"ps_code": "__p5_late__"}, consolidation_run_id=run,
        )
        assert first is True and second is False
        known = known_run_ids(conn, "V2", "hierarchy", "hierarchy_source")
        assert "old-run" in known
        assert "__initial_no_run_id__" not in known
    finally:
        conn.rollback()
        conn.close()


def test_gap_insert_is_idempotent_and_does_not_reopen():
    conn = connections.get_unified_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO source_gap_ledger
                    (source_system, gap_type, gap_key, first_seen_at, status, source_evidence_table)
                VALUES ('V1', 'ora_06502_window', '__p5_gap__', now(), 'RESOLVED', 'cctns_v1_failed_fetch_window')
                """
            )
            cur.execute(
                """
                INSERT INTO source_gap_ledger
                    (source_system, gap_type, gap_key, first_seen_at, status, source_evidence_table)
                VALUES ('V1', 'ora_06502_window', '__p5_gap__', now(), 'OPEN', 'cctns_v1_failed_fetch_window')
                ON CONFLICT (source_system, gap_type, gap_key) DO NOTHING
                """
            )
            cur.execute(
                """
                SELECT count(*), max(status) FROM source_gap_ledger
                WHERE gap_key = '__p5_gap__'
                """
            )
            count, status = cur.fetchone()
        assert count == 1 and status == "RESOLVED"
    finally:
        conn.rollback()
        conn.close()


def test_linked_arrest_closes_its_gap_and_unlinked_stays_open():
    from etl3.merger.v2_arrest_gaps import resolve_arrest_gaps_now_linked

    conn = connections.get_unified_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO crimes_unified
                    (crime_id, source_system, source_record_id, current_source_run_id, current_as_of)
                VALUES ('__p5gapcrime', 'V2', '__p5gapcrime', 'phase5', now())
                """
            )
            cur.execute(
                """
                INSERT INTO accused_unified
                    (accused_id, source_system, source_record_id, crime_id, current_source_run_id, current_as_of)
                VALUES ('__p5gapaccused', 'V2', '__p5gapaccused', '__p5gapcrime', 'phase5', now())
                """
            )
            cur.execute(
                """
                INSERT INTO arrests_unified
                    (arrest_id, source_system, source_record_id, accused_id, crime_id,
                     current_source_run_id, current_as_of)
                VALUES
                    ('__p5gaplinked', 'V2', '__p5gaplinked', '__p5gapaccused', '__p5gapcrime', 'phase5', now()),
                    ('__p5gapopen', 'V2', '__p5gapopen', NULL, '__p5gapcrime', 'phase5', now())
                """
            )
            cur.execute(
                """
                INSERT INTO source_gap_ledger
                    (source_system, gap_type, gap_key, first_seen_at, status, source_evidence_table)
                VALUES
                    ('V2', 'unresolved_arrest_accused_link', 'arrest_id=__p5gaplinked|reason=source_person_id_null', now(), 'OPEN', 'arrests'),
                    ('V2', 'unresolved_arrest_accused_link', 'arrest_id=__p5gapopen|reason=source_person_id_null', now(), 'OPEN', 'arrests')
                """
            )
        resolved = resolve_arrest_gaps_now_linked(conn)
        assert resolved >= 1
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT gap_key, status FROM source_gap_ledger
                WHERE gap_key LIKE 'arrest_id=__p5gap%'
                ORDER BY gap_key
                """
            )
            rows = dict(cur.fetchall())
        assert rows["arrest_id=__p5gaplinked|reason=source_person_id_null"] == "RESOLVED"
        assert rows["arrest_id=__p5gapopen|reason=source_person_id_null"] == "OPEN"
    finally:
        conn.rollback()
        conn.close()


def test_reconciliation_classifies_instead_of_hiding():
    assert classify_module(source_count=10, observed_count=10, collapse=False) == "EXPECTED"
    assert classify_module(source_count=10, observed_count=4, collapse=True) == "UNRESOLVED"
    assert classify_module(source_count=10, observed_count=12, collapse=False) == "EXPECTED"
    assert classify_module(source_count=3, observed_count=3, collapse=True) == "EXPECTED"
    assert classify_module(source_count=1, observed_count=0, collapse=False, defect=True) == "UNRESOLVED"
    assert classify_module(source_count=10, observed_count=10, collapse=False, defect=True) == "MISMATCH"


def test_run_log_success_failure_restart_and_interrupted():
    conn = connections.get_unified_connection()
    created = []
    try:
        def succeed(conn, run_id, progress):
            progress["empty"] = {"ok": True}
            return {"empty": "phase5"}, 0

        def fail(conn, run_id, progress):
            progress["step"] = {"ok": True}
            raise RuntimeError("phase5 controlled failure")

        ok = run_with_run_log(conn, succeed)
        created.append(ok)
        with conn.cursor() as cur:
            cur.execute("SELECT status, error_message FROM consolidation_run_log WHERE run_id=%s", (ok,))
            status, err = cur.fetchone()
        assert status == "success" and err is None

        for _ in range(2):
            try:
                run_with_run_log(conn, fail)
                raise AssertionError("failure was swallowed")
            except RuntimeError as exc:
                assert "controlled failure" in str(exc)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT run_id, error_message FROM consolidation_run_log
                WHERE status='failed' AND error_message LIKE '%%phase5 controlled failure%%'
                ORDER BY started_at DESC LIMIT 2
                """
            )
            failed = cur.fetchall()
        assert len(failed) == 2
        for run_id, message in failed:
            created.append(run_id)
            assert "Traceback" in message
            assert "RuntimeError" in message

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO consolidation_run_log (run_id, status)
                VALUES (%s, 'running')
                """,
                (str(uuid.uuid4()),),
            )
            cur.execute("SELECT run_id FROM consolidation_run_log WHERE status='running' ORDER BY started_at DESC LIMIT 1")
            stale = cur.fetchone()[0]
        conn.commit()
        created.append(stale)
        restarted = run_with_run_log(conn, succeed)
        created.append(restarted)
        with conn.cursor() as cur:
            cur.execute("SELECT status, error_message FROM consolidation_run_log WHERE run_id=%s", (stale,))
            status, err = cur.fetchone()
            assert status == "failed" and "interrupted" in err
            cur.execute("SELECT count(*) FROM consolidation_run_log WHERE status='running'")
            assert cur.fetchone()[0] == 0
    finally:
        with conn.cursor() as cur:
            for run_id in created:
                cur.execute("DELETE FROM consolidation_run_log WHERE run_id = %s", (run_id,))
        conn.commit()
        conn.close()


def test_crash_before_cursor_then_restart_is_idempotent():
    conn = connections.get_unified_connection()
    run_ids = []
    record = "__p5_crash__"
    try:
        advance_cursor(conn, "V2", "__p5_crash__", "run-high", 10, None)
        conn.commit()

        def crash(conn, run_id, progress):
            wrote = write_source_observation(
                conn, "hierarchy_source", source_system="V2", source_table="hierarchy",
                source_record_id=record, source_run_id="run-low",
                source_created_at=T0, source_modified_at=T0, source_fetched_at=None,
                payload={"ps_code": record}, consolidation_run_id=run_id,
            )
            assert wrote is True
            conn.commit()
            progress["observed"] = {"ok": True}
            raise RuntimeError("crash before cursor advance")

        try:
            run_with_run_log(conn, crash)
            raise AssertionError("crash was swallowed")
        except RuntimeError:
            pass
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT run_id FROM consolidation_run_log
                WHERE status='failed' AND error_message LIKE '%%crash before cursor advance%%'
                ORDER BY started_at DESC LIMIT 1
                """
            )
            run_ids.append(cur.fetchone()[0])
            cur.execute(
                "SELECT last_processed_source_run_id FROM consolidation_cursor WHERE source_module='__p5_crash__'"
            )
            assert cur.fetchone()[0] == "run-high"
            cur.execute(
                "SELECT count(*) FROM hierarchy_source WHERE source_record_id=%s",
                (record,),
            )
            assert cur.fetchone()[0] == 1

        def recover(conn, run_id, progress):
            wrote = write_source_observation(
                conn, "hierarchy_source", source_system="V2", source_table="hierarchy",
                source_record_id=record, source_run_id="run-low",
                source_created_at=T0, source_modified_at=T0, source_fetched_at=None,
                payload={"ps_code": record}, consolidation_run_id=run_id,
            )
            assert wrote is False
            result = advance_cursor(conn, "V2", "__p5_crash__", "run-high", 10, 10)
            assert result == "unchanged"
            assert advance_cursor(conn, "V2", "__p5_crash__", "run-low", 1, 10) == "refused_regression"
            return {"crash": "phase5"}, 1

        run_ids.append(run_with_run_log(conn, recover))
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM hierarchy_source WHERE source_record_id=%s",
                (record,),
            )
            assert cur.fetchone()[0] == 1
            cur.execute(
                "SELECT last_processed_source_run_id FROM consolidation_cursor WHERE source_module='__p5_crash__'"
            )
            assert cur.fetchone()[0] == "run-high"
    finally:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM hierarchy_source WHERE source_record_id=%s", (record,))
            cur.execute("DELETE FROM consolidation_cursor WHERE source_module='__p5_crash__'")
            for run_id in run_ids:
                cur.execute("DELETE FROM consolidation_run_log WHERE run_id=%s", (run_id,))
        conn.commit()
        conn.close()


def test_identity_rules_unchanged_and_not_auto_confirmed():
    """Uses the existing matcher. Rolls back every inserted person."""
    conn = connections.get_unified_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM identity_links WHERE status <> 'candidate'")
            assert cur.fetchone()[0] == 0
        rows = [
            ("__p5id_v1a", "V1", "Probe Person", "Father One", "9999999999", None),
            ("__p5id_v1b", "V1", "Probe Person", "Father One", "9999999999", None),
            ("__p5id_v2", "V2", "Probe Person", "Father One", "9999999999", "1990-01-01"),
            ("__p5id_dob_a", "V1", "Dob Only A", None, None, "1980-05-05"),
            ("__p5id_dob_b", "V2", "Dob Only B", None, None, "1980-05-05"),
            ("__p5id_null_a", "V1", None, None, None, None),
            ("__p5id_null_b", "V2", None, "Father One", None, None),
            ("__p5id_name_a", "V1", "Same Name", "Other Father", "9000011111", None),
            ("__p5id_name_b", "V2", "Same Name", "Different Father", "9000022222", None),
        ]
        with conn.cursor() as cur:
            for pid, system, name, father, phone, dob in rows:
                cur.execute(
                    """
                    INSERT INTO persons_unified
                        (person_id, source_system, source_record_id, full_name, relative_name,
                         phone_number, date_of_birth, current_source_run_id, current_as_of)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 'phase5-test', now())
                    """,
                    (pid, system, pid, name, father, phone, dob),
                )
        found = {
            (a, b): (basis, conf)
            for a, b, basis, conf in pm.generate_candidates(conn)
            if a.startswith("__p5id_") or b.startswith("__p5id_")
        }
        phone_pair = found.get(("__p5id_v1a", "__p5id_v2"))
        assert phone_pair is not None
        assert phone_pair[1] == 0.30
        assert ("__p5id_dob_a", "__p5id_dob_b") not in found
        assert ("__p5id_null_a", "__p5id_null_b") not in found
        assert ("__p5id_name_a", "__p5id_name_b") not in found

        probe = [item for item in pm.generate_candidates(conn) if item[0].startswith("__p5id_") or item[1].startswith("__p5id_")]
        first = pm.write_candidates(conn, probe, str(uuid.uuid4()))
        second = pm.write_candidates(conn, probe, str(uuid.uuid4()))
        assert first["newly_inserted"] == len(probe)
        assert second["newly_inserted"] == 0
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE identity_links SET status='confirmed', reviewed_by='phase5-test'
                WHERE person_a_id='__p5id_v1a' AND person_b_id='__p5id_v2'
                """
            )
        pm.write_candidates(conn, probe, str(uuid.uuid4()))
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT status FROM identity_links
                WHERE person_a_id='__p5id_v1a' AND person_b_id='__p5id_v2'
                """
            )
            assert cur.fetchone()[0] == "confirmed"
            cur.execute(
                """
                SELECT count(*) FROM (
                    SELECT person_a_id, person_b_id
                    FROM identity_links
                    WHERE person_a_id = ANY(%s) OR person_b_id = ANY(%s)
                    GROUP BY 1, 2
                    HAVING count(*) > 1
                ) d
                """,
                ([row[0] for row in rows], [row[0] for row in rows]),
            )
            assert cur.fetchone()[0] == 0
    finally:
        conn.rollback()
        conn.close()


def test_source_writes_rejected_and_source_trees_untouched():
    import psycopg2

    for getter, label in (
        (connections.get_v1_source_connection, "V1"),
        (connections.get_v2_source_connection, "V2"),
    ):
        conn = getter()
        try:
            with conn.cursor() as cur:
                cur.execute("CREATE TABLE etl3_phase5_write_probe (x int)")
            raise AssertionError(f"{label} write was accepted")
        except psycopg2.errors.ReadOnlySqlTransaction:
            conn.rollback()
        finally:
            conn.close()

    import subprocess
    diff = subprocess.check_output(
        ["git", "status", "--porcelain"],
        cwd=str(Path(__file__).resolve().parents[2]),
        text=True,
    )
    touched = []
    for line in diff.splitlines():
        path = line[3:].strip().strip('"')
        if path.startswith("cctns-v1/") or path.startswith("cctns-v2/"):
            touched.append(path)
    assert touched == []


def main():
    check("ordering matrix is deterministic", test_ordering_matrix_is_deterministic)
    check("change then revert records both field changes", test_change_then_revert_and_multi_field_change)
    check("same-run revision id is stable", test_same_run_revision_id_is_stable)
    check("equal timestamps order by observation id", test_equal_timestamp_orders_by_observation_id)
    check("null and future timestamps do not clobber", test_null_and_future_timestamps_do_not_clobber)
    check("explicit null clears and older replay does not restore", test_explicit_null_clears_and_replay_does_not_restore)
    check("replay and representation are not false changes", test_repeated_record_is_idempotent_and_representation_is_not_a_change)
    check("distinct source rows stay distinct", test_distinct_sources_stay_distinct)
    check("fetch_latest keeps both equal watermarks' winner and ignores far future", test_fetch_latest_keeps_equal_watermark_and_ignores_far_future)
    check("cursor does not regress, reset, or jump", test_cursor_does_not_regress_reset_or_jump)
    check("late run ids stay visible and replays insert nothing", test_known_runs_keep_late_records_and_replays_insert_nothing)
    check("gap insert is idempotent and does not reopen", test_gap_insert_is_idempotent_and_does_not_reopen)
    check("linked arrest closes its gap", test_linked_arrest_closes_its_gap_and_unlinked_stays_open)
    check("reconciliation classifies deltas", test_reconciliation_classifies_instead_of_hiding)
    check("run log success, failure, restart, interrupted", test_run_log_success_failure_restart_and_interrupted)
    check("crash before cursor then restart is idempotent", test_crash_before_cursor_then_restart_is_idempotent)
    check("identity rules unchanged", test_identity_rules_unchanged_and_not_auto_confirmed)
    check("source writes rejected", test_source_writes_rejected_and_source_trees_untouched)


if __name__ == "__main__":
    main()
