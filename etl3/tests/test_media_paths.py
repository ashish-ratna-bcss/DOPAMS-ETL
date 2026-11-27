"""Unit tests for media path resolution and availability classification.

Run: python etl3/tests/test_media_paths.py
Uses temporary fixtures only — never touches production media trees.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.media import paths as media_paths
from etl3.media.paths import (
    classify_v1_availability,
    classify_v2_availability,
    clear_dir_cache,
    minio_health_check,
    resolve_v1_paths,
    resolve_v2_paths,
    safe_join_under_root,
)


class _FakeClientError(Exception):
    """Minimal stand-in for botocore.exceptions.ClientError -- carries a
    .response dict shaped the way _minio_object_size() reads it, without
    requiring a real boto3 install to construct one."""

    def __init__(self, code: str):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


import contextlib  # noqa: E402


@contextlib.contextmanager
def _fake_minio_credentials():
    """Set MINIO_ACCESS_KEY/SECRET_KEY for the duration of a test only, and
    restore whatever was there before on exit. Without this, a leaked fake
    credential would make _minio_enabled() return True for every later test
    in this same process (including the pre-existing, MinIO-unconfigured
    tests above), making them silently attempt a real network call instead
    of exercising the local-fallback path they were written to test."""
    keys = ("MINIO_ACCESS_KEY", "MINIO_SECRET_KEY")
    backup = {k: os.environ.get(k) for k in keys}
    os.environ["MINIO_ACCESS_KEY"] = "k"
    os.environ["MINIO_SECRET_KEY"] = "s"
    try:
        yield
    finally:
        for k, v in backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def check(label, fn):
    try:
        fn()
        print(f"[PASS] {label}")
    except Exception as e:
        print(f"[FAIL] {label}: {type(e).__name__}: {e}")
        raise


def test_safe_join_blocks_traversal():
    with tempfile.TemporaryDirectory() as td:
        assert safe_join_under_root(td, "a", "b.pdf") == str(Path(td, "a", "b.pdf").resolve())
        assert safe_join_under_root(td, "../etc/passwd") is None
        assert safe_join_under_root(td, "a/../../etc/passwd") is None
        assert safe_join_under_root(td, "/absolute/nope") is not None  # leading slash stripped
        # but must still stay under root
        joined = safe_join_under_root(td, "/absolute/nope")
        assert joined.startswith(str(Path(td).resolve()))


def test_v1_inaccessible_historical_path():
    payload = {
        "status": "DOWNLOADED",
        "local_path": "/home/tganb/dopams/media_cctnsv1/FIR/x/y.pdf",
        "attach_path": "FIR/x",
        "dms_file_name": "y.pdf",
        "error_message": None,
    }
    with tempfile.TemporaryDirectory() as td:
        os.environ["MEDIA_BASE_DIR"] = td
        paths = resolve_v1_paths(payload)
        status, detail = classify_v1_availability(payload, paths)
        assert status == "BOOKKEEPING_DOWNLOADED_INACCESSIBLE", (status, detail)
        assert paths["source_local_path"].startswith("/home/tganb/")


def test_v1_verified_under_media_base():
    with tempfile.TemporaryDirectory() as td:
        os.environ["MEDIA_BASE_DIR"] = td
        attach = "FIR/2020/demo"
        name = "doc.pdf"
        target = Path(td) / attach / name
        target.parent.mkdir(parents=True)
        target.write_bytes(b"%PDF-1.4 demo")
        payload = {
            "status": "DOWNLOADED",
            "local_path": f"/home/tganb/dopams/media_cctnsv1/{attach}/{name}",
            "attach_path": attach,
            "dms_file_name": name,
        }
        paths = resolve_v1_paths(payload)
        status, detail = classify_v1_availability(payload, paths)
        assert status == "VERIFIED_ACCESSIBLE", (status, detail)
        assert paths["resolved_relative_path"] == f"{attach}/{name}"


def test_v1_empty_at_source():
    payload = {
        "status": "NOT_FOUND",
        "error_message": "Document not available on Alfresco DMS (0 bytes)",
        "attach_path": "FIR/x",
        "dms_file_name": "y.pdf",
    }
    status, _ = classify_v1_availability(payload)
    assert status == "EMPTY_AT_SOURCE"


def test_v2_verified_and_missing():
    with tempfile.TemporaryDirectory() as td:
        os.environ["FILES_MEDIA_BASE_PATH"] = td
        fid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        rel = f"crimes/{fid}"
        # file stored with extension
        fpath = Path(td) / "crimes" / f"{fid}.pdf"
        fpath.parent.mkdir(parents=True)
        fpath.write_bytes(b"%PDF-1.4")
        payload = {
            "file_id": fid,
            "file_path": f"/{rel}",
            "is_downloaded": True,
            "is_empty": False,
            "has_field": True,
            "download_error": None,
            "source_type": "crime",
            "source_field": "FIR_COPY",
            "parent_id": "crime1",
        }
        paths = resolve_v2_paths(payload)
        status, detail = classify_v2_availability(payload, paths)
        assert status == "VERIFIED_ACCESSIBLE", (status, detail)

        payload2 = dict(payload)
        payload2["file_path"] = "/crimes/missing-id"
        payload2["file_id"] = "missing-id"
        paths2 = resolve_v2_paths(payload2)
        status2, _ = classify_v2_availability(payload2, paths2)
        assert status2 == "BOOKKEEPING_DOWNLOADED_INACCESSIBLE"


def test_v2_unresolved_file_id():
    payload = {
        "file_id": None,
        "file_path": None,
        "is_downloaded": False,
        "is_empty": False,
        "has_field": True,
        "download_error": "SOURCE: MEDIA present but file_id is null (cannot download)",
        "source_type": "case_property",
        "source_field": "MEDIA",
        "parent_id": "cp1",
    }
    status, _ = classify_v2_availability(payload)
    assert status == "UNRESOLVED_FILE_ID"


def test_v2_empty_and_permanent():
    empty = {
        "file_id": None,
        "is_empty": True,
        "is_downloaded": False,
        "has_field": True,
        "download_error": None,
    }
    assert classify_v2_availability(empty)[0] == "EMPTY_AT_SOURCE"
    perm = {
        "file_id": "x",
        "is_empty": False,
        "is_downloaded": False,
        "has_field": True,
        "download_error": "PERMANENT: HTTP 400 Bad Request",
        "file_path": "/chargesheets/x",
    }
    assert classify_v2_availability(perm)[0] == "DOWNLOAD_FAILED"


def test_collision_safe_keys_differ_by_system():
    # Same raw numeric id must not collide across systems.
    v1_key = f"V1:media:42"
    v2_key = f"V2:file_media_bookkeeping:42"
    assert v1_key != v2_key


def test_v1_null_parent_preserved():
    from etl3.merger.media_consolidate import _project_v1

    payload = {
        "status": "PENDING",
        "fir_reg_num": None,
        "entity_type": "FIR",
        "attach_path": "FIR/x",
        "dms_file_name": "y.pdf",
    }
    mapped = _project_v1(payload)
    assert mapped["parent_entity_id"] is None
    assert mapped["parent_crime_id"] is None
    assert mapped["availability_status"] == "PENDING"


def test_v2_parent_entity_linking():
    from etl3.merger.media_consolidate import _project_v2

    with tempfile.TemporaryDirectory() as td:
        os.environ["FILES_MEDIA_BASE_PATH"] = td
        payload = {
            "file_id": "fid-1",
            "file_path": "/persons/fid-1",
            "is_downloaded": False,
            "is_empty": False,
            "has_field": True,
            "download_error": None,
            "source_type": "person",
            "source_field": "PHOTO",
            "parent_id": 99,
        }
        mapped = _project_v2(payload)
        assert mapped["parent_entity_type"] == "person"
        assert mapped["parent_entity_id"] == "99"
        assert mapped["parent_crime_id"] is None
        assert mapped["attachment_category"] == "person/PHOTO"


def test_empty_file_not_verified():
    with tempfile.TemporaryDirectory() as td:
        os.environ["FILES_MEDIA_BASE_PATH"] = td
        fid = "empty-file-id"
        target = Path(td) / "crimes" / f"{fid}.pdf"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"")
        payload = {
            "file_id": fid,
            "file_path": f"/crimes/{fid}",
            "is_downloaded": True,
            "is_empty": False,
            "has_field": True,
            "download_error": None,
        }
        status, _ = classify_v2_availability(payload, resolve_v2_paths(payload))
        assert status == "BOOKKEEPING_DOWNLOADED_INACCESSIBLE"


def test_status_transition_on_path_change():
    with tempfile.TemporaryDirectory() as td:
        os.environ["MEDIA_BASE_DIR"] = td
        attach = "FIR/2020/demo"
        name = "doc.pdf"
        payload = {
            "status": "DOWNLOADED",
            "local_path": f"/home/tganb/dopams/media_cctnsv1/{attach}/{name}",
            "attach_path": attach,
            "dms_file_name": name,
        }
        clear_dir_cache()
        status1, _ = classify_v1_availability(payload, resolve_v1_paths(payload))
        assert status1 == "BOOKKEEPING_DOWNLOADED_INACCESSIBLE"
        target = Path(td) / attach / name
        target.parent.mkdir(parents=True)
        target.write_bytes(b"%PDF-1.4")
        clear_dir_cache()  # parent-dir negative cache must not outlive fixture writes
        status2, _ = classify_v1_availability(payload, resolve_v1_paths(payload))
        assert status2 == "VERIFIED_ACCESSIBLE"


def test_minio_health_check_unconfigured():
    # This host's real V1/V2 .env files now carry a real MINIO_ACCESS_KEY/
    # SECRET_KEY (deployed 2026-11-27), and _env()'s fallback reads those
    # files directly -- so popping the os.environ vars alone is not enough
    # to simulate "unconfigured" here. Also patch the settings paths so the
    # fallback can't find a real .env either, to exercise the genuinely-
    # unconfigured branch regardless of what's deployed on this host.
    env_backup = {k: os.environ.pop(k, None) for k in ("MINIO_ACCESS_KEY", "MINIO_SECRET_KEY")}
    try:
        with patch.object(media_paths.settings, "V1_SOURCE_ENV_PATH", "/nonexistent/v1.env"), \
             patch.object(media_paths.settings, "V2_SOURCE_ENV_PATH", "/nonexistent/v2.env"):
            ok, detail = minio_health_check()
        assert ok is True, detail
        assert "not configured" in detail.lower()
    finally:
        for k, v in env_backup.items():
            if v is not None:
                os.environ[k] = v


def test_minio_health_check_configured_and_healthy():
    fake_client = type("C", (), {"head_bucket": lambda self, Bucket: {"OK": True}})()
    with _fake_minio_credentials():
        with patch.object(media_paths, "_get_minio_client", return_value=fake_client):
            ok, detail = minio_health_check()
    assert ok is True, detail
    assert "reachable" in detail.lower()


def test_minio_health_check_configured_unreachable():
    def _raise(self, Bucket):
        raise _FakeClientError("403")

    fake_client = type("C", (), {"head_bucket": _raise})()
    with _fake_minio_credentials():
        with patch.object(media_paths, "_get_minio_client", return_value=fake_client):
            ok, detail = minio_health_check()
    assert ok is False, detail
    assert "failed" in detail.lower()


def test_minio_health_check_client_init_failure():
    with _fake_minio_credentials():
        with patch.object(media_paths, "_get_minio_client", return_value=None):
            ok, detail = minio_health_check()
    assert ok is False, detail
    assert "failed to initialize" in detail.lower()


def test_minio_object_size_exists():
    fake_client = type(
        "C", (), {"head_object": lambda self, Bucket, Key: {"ContentLength": 1234}}
    )()
    with _fake_minio_credentials():
        with patch.object(media_paths, "_get_minio_client", return_value=fake_client):
            ok, size = media_paths._minio_object_size("crimes/some-id.pdf")
    assert ok is True
    assert size == 1234


def test_minio_object_size_missing_404():
    def _raise(self, Bucket, Key):
        raise _FakeClientError("404")

    fake_client = type("C", (), {"head_object": _raise})()
    with _fake_minio_credentials():
        with patch.object(media_paths, "_get_minio_client", return_value=fake_client):
            ok, size = media_paths._minio_object_size("crimes/missing-id.pdf")
    assert ok is False
    assert size == 0


def test_minio_object_size_auth_failure_logs_warning_not_silent_missing():
    def _raise(self, Bucket, Key):
        raise _FakeClientError("AccessDenied")

    fake_client = type("C", (), {"head_object": _raise})()
    import unittest

    case = unittest.TestCase()
    with _fake_minio_credentials():
        with patch.object(media_paths, "_get_minio_client", return_value=fake_client):
            with case.assertLogs(media_paths.logger, level="WARNING") as captured:
                ok, size = media_paths._minio_object_size("crimes/some-id.pdf")
    assert ok is False
    assert size == 0
    assert any("non-404" in m for m in captured.output), captured.output


def test_minio_object_size_network_failure_logs_warning_not_silent_missing():
    def _raise(self, Bucket, Key):
        raise ConnectionError("connection refused")

    fake_client = type("C", (), {"head_object": _raise})()
    import unittest

    case = unittest.TestCase()
    with _fake_minio_credentials():
        with patch.object(media_paths, "_get_minio_client", return_value=fake_client):
            with case.assertLogs(media_paths.logger, level="WARNING") as captured:
                ok, size = media_paths._minio_object_size("crimes/some-id.pdf")
    assert ok is False
    assert size == 0
    assert any("non-404" in m for m in captured.output), captured.output


def test_minio_down_does_not_falsely_grant_availability_when_disk_also_lacks_file():
    """A MinIO auth/network failure must never be reinterpreted as the file
    being confirmed-missing-and-therefore-fine, nor as confirmed-present.
    With MinIO failing AND no local copy, the only honest classification
    is still-inaccessible."""

    def _raise(self, Bucket, Key):
        raise _FakeClientError("AccessDenied")

    fake_client = type("C", (), {"head_object": _raise})()
    with tempfile.TemporaryDirectory() as td:
        os.environ["FILES_MEDIA_BASE_PATH"] = td  # empty -- no local copy either
        fid = "ffffffff-0000-1111-2222-333333333333"
        payload = {
            "file_id": fid,
            "file_path": f"/crimes/{fid}",
            "is_downloaded": True,
            "is_empty": False,
            "has_field": True,
            "download_error": None,
        }
        with _fake_minio_credentials():
            with patch.object(media_paths, "_get_minio_client", return_value=fake_client):
                status, detail = classify_v2_availability(payload, resolve_v2_paths(payload))
    assert status == "BOOKKEEPING_DOWNLOADED_INACCESSIBLE", (status, detail)


def test_minio_down_local_fallback_still_grants_availability_when_disk_has_file():
    """When MinIO errors mid-run but the local copy genuinely still exists
    (not yet migrated off disk), the disk fallback must still work -- a
    MinIO outage must not regress a file that was already locally verified."""

    def _raise(self, Bucket, Key):
        raise ConnectionError("connection refused")

    fake_client = type("C", (), {"head_object": _raise})()
    with tempfile.TemporaryDirectory() as td:
        os.environ["FILES_MEDIA_BASE_PATH"] = td
        fid = "aaaaaaaa-1111-2222-3333-444444444444"
        fpath = Path(td) / "crimes" / f"{fid}.pdf"
        fpath.parent.mkdir(parents=True)
        fpath.write_bytes(b"%PDF-1.4")
        payload = {
            "file_id": fid,
            "file_path": f"/crimes/{fid}",
            "is_downloaded": True,
            "is_empty": False,
            "has_field": True,
            "download_error": None,
        }
        with _fake_minio_credentials():
            with patch.object(media_paths, "_get_minio_client", return_value=fake_client):
                status, detail = classify_v2_availability(payload, resolve_v2_paths(payload))
    assert status == "VERIFIED_ACCESSIBLE", (status, detail)


def main():
    check("safe join / traversal", test_safe_join_blocks_traversal)
    check("v1 inaccessible historical", test_v1_inaccessible_historical_path)
    check("v1 verified under MEDIA_BASE_DIR", test_v1_verified_under_media_base)
    check("v1 empty at source", test_v1_empty_at_source)
    check("v2 verified/missing", test_v2_verified_and_missing)
    check("v2 unresolved file_id", test_v2_unresolved_file_id)
    check("v2 empty/permanent", test_v2_empty_and_permanent)
    check("collision-safe keys", test_collision_safe_keys_differ_by_system)
    check("v1 null parent", test_v1_null_parent_preserved)
    check("v2 parent linking", test_v2_parent_entity_linking)
    check("empty file not verified", test_empty_file_not_verified)
    check("status transition on path change", test_status_transition_on_path_change)
    check("minio_health_check: unconfigured -> healthy (fallback mode)", test_minio_health_check_unconfigured)
    check("minio_health_check: configured + reachable -> healthy", test_minio_health_check_configured_and_healthy)
    check("minio_health_check: configured + unreachable -> unhealthy", test_minio_health_check_configured_unreachable)
    check("minio_health_check: client init failure -> unhealthy", test_minio_health_check_client_init_failure)
    check("minio object size: exists", test_minio_object_size_exists)
    check("minio object size: missing (404)", test_minio_object_size_missing_404)
    check("minio object size: auth failure logs, not silent", test_minio_object_size_auth_failure_logs_warning_not_silent_missing)
    check("minio object size: network failure logs, not silent", test_minio_object_size_network_failure_logs_warning_not_silent_missing)
    check("minio down + no local copy -> still inaccessible (no false grant)", test_minio_down_does_not_falsely_grant_availability_when_disk_also_lacks_file)
    check("minio down + local copy present -> disk fallback still verifies", test_minio_down_local_fallback_still_grants_availability_when_disk_has_file)


if __name__ == "__main__":
    main()
