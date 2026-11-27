#!/usr/bin/env python3
"""
One-time backfill: CCTNS V2 existing local media (FILES_MEDIA_BASE_PATH,
default /mnt/shared-etl-files) -> MinIO bucket `files`.

Must be run on a host that has BOTH:
  - filesystem access to FILES_MEDIA_BASE_PATH (the V2 ETL / NFS host), and
  - write-capable MinIO credentials (MINIO_ACCESS_KEY / MINIO_SECRET_KEY).

Does NOT touch any database table or row. The existing object-key convention
(<subdir>/<file_id>.<ext>, the same shape generate_file_path() already
produces) is preserved exactly, so file_media_bookkeeping's existing
file_url values keep resolving once the bytes exist in MinIO -- no DB
migration is needed or performed here.

Never deletes, moves or modifies a source file. Never overwrites an existing
MinIO object whose confirmed size differs from the source file; such cases
are reported for manual review instead.

Usage:
    python backfill_v2_media_to_minio.py                  # dry run (default)
    python backfill_v2_media_to_minio.py --apply           # upload for real
    python backfill_v2_media_to_minio.py --apply --sample 20   # first 20 eligible files only
    python backfill_v2_media_to_minio.py --apply --subdir crimes
    python backfill_v2_media_to_minio.py --apply --resume  # skip files already
                                                            # recorded as migrated
                                                            # in the progress file

Content-integrity verification:
    Source files here are expected to be well under MAX_SINGLE_PUT_BYTES
    (200 MB). Within that limit the script reads the whole file and uploads
    it with a single put_object call, which MinIO/S3 guarantee returns an
    ETag equal to the plain hex MD5 of the body -- so the local MD5 can be
    compared directly against the returned ETag. A file at or above the cap
    is NOT uploaded automatically: boto3 would silently switch to a
    multipart upload, whose ETag is not an MD5 and cannot be checked this
    way, so such files are written to the "needs_manual_review" bucket
    instead of being verified unsafely.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterator, List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[1]  # etl-files/ -> cctns-v2/
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_PACKAGE_DIR = Path(__file__).resolve().parent / "etl_files_media_server"
if str(_PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_DIR))

from env_utils import first_env, load_repo_environment  # noqa: E402
from etl_files_media_server.object_store import get_object_store, valid_key  # noqa: E402

SCRIPT_DIR = Path(__file__).resolve().parent
LOADED_ENV_FILE = load_repo_environment(extra_candidates=[SCRIPT_DIR / ".env.server", SCRIPT_DIR / ".env"])

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - backfill - %(levelname)s - %(message)s",
)
logger = logging.getLogger("backfill_v2_media_to_minio")

# Same 10 destination subdirs map_destination_subdir() in main.py /
# sync_files_state.py / update_file_urls_with_extensions.py produces.
SUBDIRS = [
    "crimes",
    os.path.join("person", "identitydetails"),
    os.path.join("person", "media"),
    "property",
    os.path.join("interrogations", "media"),
    os.path.join("interrogations", "interrogationreport"),
    os.path.join("interrogations", "dopamsdata"),
    "mo_seizures",
    "chargesheets",
    "fsl_case_property",
]

MAX_SINGLE_PUT_BYTES = 200 * 1024 * 1024  # 200 MB
PROGRESS_FILE_DEFAULT = str(SCRIPT_DIR / "backfill_v2_progress.json")
MAX_UPLOAD_RETRIES = 3


@dataclass
class FileRecord:
    subdir: str
    filename: str
    local_path: str
    object_key: str
    size: int
    file_id: str
    ext: str


@dataclass
class Reconciliation:
    source_files_discovered: int = 0
    zero_byte: int = 0
    no_extension: int = 0
    duplicate_file_ids: int = 0
    eligible: int = 0
    skipped_already_valid: int = 0
    uploaded: int = 0
    failed: int = 0
    size_mismatch_existing: int = 0
    checksum_mismatch_after_upload: int = 0
    needs_manual_review: int = 0
    total_source_bytes: int = 0
    total_verified_minio_bytes: int = 0
    manual_review_items: List[dict] = field(default_factory=list)
    failed_items: List[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "source_files_discovered": self.source_files_discovered,
            "zero_byte": self.zero_byte,
            "no_extension": self.no_extension,
            "duplicate_file_ids": self.duplicate_file_ids,
            "eligible": self.eligible,
            "skipped_already_valid": self.skipped_already_valid,
            "uploaded": self.uploaded,
            "failed": self.failed,
            "size_mismatch_existing": self.size_mismatch_existing,
            "checksum_mismatch_after_upload": self.checksum_mismatch_after_upload,
            "needs_manual_review": self.needs_manual_review,
            "total_source_bytes": self.total_source_bytes,
            "total_verified_minio_bytes": self.total_verified_minio_bytes,
            "manual_review_items": self.manual_review_items[:200],
            "failed_items": self.failed_items[:200],
        }


def load_progress(path: str) -> Dict[str, dict]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.warning("Could not read progress file %s (%s); starting fresh.", path, exc)
        return {}


def save_progress(path: str, progress: Dict[str, dict]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(progress, f, indent=2)
    os.replace(tmp, path)


def discover_files(base_path: str, subdir_filter: Optional[str]) -> Iterator[FileRecord]:
    """Walks only the known destination subdirs; never recurses into
    anything else under base_path."""
    subdirs = [subdir_filter] if subdir_filter else SUBDIRS
    for subdir in subdirs:
        full_dir = os.path.join(base_path, subdir)
        if not os.path.isdir(full_dir):
            logger.warning("Subdir does not exist, skipping: %s", full_dir)
            continue
        for entry in sorted(os.listdir(full_dir)):
            local_path = os.path.join(full_dir, entry)
            if not os.path.isfile(local_path):
                continue
            file_id, ext = os.path.splitext(entry)
            size = os.path.getsize(local_path)
            key = f"{subdir.replace(os.sep, '/')}/{entry}"
            yield FileRecord(
                subdir=subdir, filename=entry, local_path=local_path,
                object_key=key, size=size, file_id=file_id, ext=ext,
            )


def guess_content_type(ext: str) -> Optional[str]:
    return {
        ".pdf": "application/pdf",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".doc": "application/msword",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }.get(ext.lower())


def md5_of_file(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def upload_with_retry(store, rec: FileRecord) -> "tuple[bool, Optional[str], Optional[str]]":
    """Returns (ok, etag_hex_or_None, error_or_None). Never raises."""
    last_error = None
    for attempt in range(1, MAX_UPLOAD_RETRIES + 1):
        try:
            with open(rec.local_path, "rb") as f:
                body = f.read()
            extra = {}
            ctype = guess_content_type(rec.ext)
            if ctype:
                extra["ContentType"] = ctype
            resp = store._client.put_object(  # noqa: SLF001 - single-part PUT is deliberate; see module docstring
                Bucket=store.bucket, Key=rec.object_key, Body=body, **extra
            )
            etag = (resp.get("ETag") or "").strip('"')
            return True, etag, None
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            if attempt < MAX_UPLOAD_RETRIES:
                time.sleep(min(2 ** attempt, 10))
    return False, None, last_error


def run(base_path: str, apply: bool, subdir_filter: Optional[str], sample: Optional[int],
        resume: bool, progress_path: str) -> Reconciliation:
    recon = Reconciliation()
    progress = load_progress(progress_path) if resume else {}

    store = None
    if apply:
        try:
            store = get_object_store()  # fail fast if MinIO credentials aren't configured
        except Exception as exc:
            logger.error("Cannot start apply-mode backfill: %s", exc)
            raise

    seen_file_ids: Dict[str, set] = {}
    eligible_records: List[FileRecord] = []

    for rec in discover_files(base_path, subdir_filter):
        recon.source_files_discovered += 1
        recon.total_source_bytes += rec.size

        seen_file_ids.setdefault(rec.subdir, set())
        if rec.file_id in seen_file_ids[rec.subdir]:
            recon.duplicate_file_ids += 1
            recon.manual_review_items.append({"reason": "duplicate_file_id", "key": rec.object_key})
            continue
        seen_file_ids[rec.subdir].add(rec.file_id)

        if rec.size == 0:
            recon.zero_byte += 1
            recon.manual_review_items.append({"reason": "zero_byte", "key": rec.object_key})
            continue
        if not rec.ext:
            recon.no_extension += 1
            recon.manual_review_items.append({"reason": "no_extension", "key": rec.object_key})
            continue
        if not valid_key(rec.object_key):
            recon.needs_manual_review += 1
            recon.manual_review_items.append({"reason": "invalid_key", "key": rec.object_key})
            continue

        recon.eligible += 1
        eligible_records.append(rec)

    logger.info(
        "Discovered %d files (%d eligible, %d zero-byte, %d missing-extension, %d duplicate-id)",
        recon.source_files_discovered, recon.eligible, recon.zero_byte,
        recon.no_extension, recon.duplicate_file_ids,
    )

    if not apply:
        logger.info("DRY RUN ONLY -- no MinIO calls made, no files uploaded.")
        return recon

    processed = 0
    for rec in eligible_records:
        if sample is not None and processed >= sample:
            break

        if resume and progress.get(rec.object_key, {}).get("status") == "uploaded":
            recon.skipped_already_valid += 1
            recon.total_verified_minio_bytes += progress[rec.object_key].get("size", 0)
            continue

        exists, remote_size = store.object_exists(rec.object_key)
        if exists:
            if remote_size == rec.size:
                recon.skipped_already_valid += 1
                recon.total_verified_minio_bytes += remote_size
                progress[rec.object_key] = {"status": "uploaded", "size": remote_size}
                continue
            else:
                recon.size_mismatch_existing += 1
                recon.manual_review_items.append({
                    "reason": "size_mismatch_existing_object",
                    "key": rec.object_key, "local_size": rec.size, "minio_size": remote_size,
                })
                progress[rec.object_key] = {"status": "size_mismatch", "local_size": rec.size, "minio_size": remote_size}
                continue

        if rec.size >= MAX_SINGLE_PUT_BYTES:
            recon.needs_manual_review += 1
            recon.manual_review_items.append({
                "reason": "exceeds_single_put_cap_unsafe_to_autoverify",
                "key": rec.object_key, "size": rec.size,
            })
            progress[rec.object_key] = {"status": "needs_manual_review", "size": rec.size}
            continue

        local_md5 = md5_of_file(rec.local_path)
        ok, etag, error = upload_with_retry(store, rec)
        processed += 1

        if not ok:
            recon.failed += 1
            recon.failed_items.append({"key": rec.object_key, "error": error})
            progress[rec.object_key] = {"status": "failed", "error": error}
            continue

        if etag and etag != local_md5:
            recon.checksum_mismatch_after_upload += 1
            recon.manual_review_items.append({
                "reason": "checksum_mismatch_after_upload", "key": rec.object_key,
                "local_md5": local_md5, "returned_etag": etag,
            })
            progress[rec.object_key] = {"status": "checksum_mismatch"}
            continue

        confirmed, confirmed_size = store.object_exists(rec.object_key)
        if not confirmed or confirmed_size != rec.size:
            recon.failed += 1
            recon.failed_items.append({"key": rec.object_key, "error": "post-upload HEAD did not confirm size"})
            progress[rec.object_key] = {"status": "failed", "error": "head_confirm_failed"}
            continue

        recon.uploaded += 1
        recon.total_verified_minio_bytes += confirmed_size
        progress[rec.object_key] = {"status": "uploaded", "size": confirmed_size}

        if recon.uploaded % 50 == 0:
            save_progress(progress_path, progress)
            logger.info("Progress checkpoint: %d uploaded so far", recon.uploaded)

    save_progress(progress_path, progress)
    return recon


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill CCTNS V2 local media into MinIO (dry-run by default).")
    parser.add_argument("--apply", action="store_true", help="Actually upload. Without this flag, only inventories and reports.")
    parser.add_argument("--subdir", default=None, help="Limit to one destination subdir, e.g. crimes")
    parser.add_argument("--sample", type=int, default=None, help="In apply mode, upload at most N eligible files.")
    parser.add_argument("--resume", action="store_true", help="Skip files already recorded as uploaded in the progress file.")
    parser.add_argument("--progress-file", default=PROGRESS_FILE_DEFAULT)
    parser.add_argument("--base-path", default=None, help="Override FILES_MEDIA_BASE_PATH for this run.")
    parser.add_argument("--report-file", default=str(SCRIPT_DIR / "backfill_v2_reconciliation.json"))
    args = parser.parse_args()

    base_path = args.base_path or first_env("FILES_MEDIA_BASE_PATH", default="/mnt/shared-etl-files")
    if not os.path.isdir(base_path):
        logger.error("Base media path does not exist or is not accessible from this host: %s", base_path)
        logger.error("This script must run on the host where FILES_MEDIA_BASE_PATH is actually mounted.")
        sys.exit(2)

    logger.info("Base media path: %s", base_path)
    logger.info("Mode: %s", "APPLY" if args.apply else "DRY RUN")

    try:
        recon = run(
            base_path=base_path, apply=args.apply, subdir_filter=args.subdir,
            sample=args.sample, resume=args.resume, progress_path=args.progress_file,
        )
    except Exception as exc:
        logger.error("Backfill aborted: %s", exc)
        sys.exit(1)

    with open(args.report_file, "w", encoding="utf-8") as f:
        json.dump(recon.to_dict(), f, indent=2)

    logger.info("")
    logger.info("=" * 80)
    logger.info("BACKFILL RECONCILIATION")
    logger.info("=" * 80)
    for k, v in recon.to_dict().items():
        if k not in ("manual_review_items", "failed_items"):
            logger.info("%-38s: %s", k, v)
    logger.info("Full report written to: %s", args.report_file)
    logger.info("=" * 80)

    if recon.failed or recon.checksum_mismatch_after_upload or recon.size_mismatch_existing:
        sys.exit(1)


if __name__ == "__main__":
    main()
