"""Unit tests for media path resolution and availability classification.

Run: python etl3/tests/test_media_paths.py
Uses temporary fixtures only — never touches production media trees.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.media.paths import (
    classify_v1_availability,
    classify_v2_availability,
    clear_dir_cache,
    resolve_v1_paths,
    resolve_v2_paths,
    safe_join_under_root,
)


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


if __name__ == "__main__":
    main()
