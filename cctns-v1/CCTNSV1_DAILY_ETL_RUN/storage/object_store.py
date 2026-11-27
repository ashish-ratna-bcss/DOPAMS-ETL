"""MinIO/S3-compatible object storage client for the CCTNS V1 media ETL.

Upload-and-confirm semantics: an upload is never treated as successful from
the PUT call alone. A follow-up HEAD against the bucket confirms the object
is really retrievable, with the expected size, before the caller may mark
anything DOWNLOADED. Writing straight to local disk never needed this extra
step because the local write itself *was* the confirmation; a remote PUT
is not.

V1 object keys are `<attach_path>/<dms_file_name>` -- the same relative path
this ETL has always recorded, now used directly as an S3 key instead of
being joined under a local filesystem root. See assert_v1_key() for the
guard against colliding with V2's reserved key prefixes in the shared
bucket.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Iterable, Optional, Tuple

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

from config import settings

logger = logging.getLogger(__name__)


# V2's reserved top-level key prefixes
# (cctns-v2/etl-files/etl_files_media_server/etl_files_media_server/main.py:
# map_destination_subdir()). V1 must never write under one of these.
_V2_RESERVED_PREFIXES = (
    "crimes/",
    "person/identitydetails/",
    "person/media/",
    "property/",
    "interrogations/media/",
    "interrogations/interrogationreport/",
    "interrogations/dopamsdata/",
    "mo_seizures/",
    "chargesheets/",
    "fsl_case_property/",
)


def assert_v1_key(key: str) -> None:
    """Refuse a V1 key that would collide with a V2-reserved prefix."""
    if any(key.startswith(prefix) for prefix in _V2_RESERVED_PREFIXES):
        raise ValueError(f"refusing V1 key {key!r}: collides with a V2-reserved prefix")


def valid_key(key: str) -> bool:
    return bool(key) and not key.startswith("/") and ".." not in key.split("/")


@dataclass
class UploadResult:
    ok: bool
    key: str
    size: int = 0
    etag: Optional[str] = None
    error: Optional[str] = None


class IterStream:
    """Minimal file-like adapter over a byte-chunk iterator (e.g. requests'
    iter_content), since boto3's upload_fileobj only ever calls .read(size).
    Tracks total bytes read for completeness checks."""

    def __init__(self, chunks: Iterable[bytes]) -> None:
        self._chunks = iter(chunks)
        self._buffer = b""
        self.total_bytes = 0

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            parts = [self._buffer]
            self._buffer = b""
            parts.extend(self._chunks)
            data = b"".join(parts)
        else:
            while len(self._buffer) < size:
                try:
                    self._buffer += next(self._chunks)
                except StopIteration:
                    break
            data, self._buffer = self._buffer[:size], self._buffer[size:]
        self.total_bytes += len(data)
        return data


class ObjectStore:
    """Thin boto3 S3 client wrapper configured for the shared MinIO bucket."""

    def __init__(self) -> None:
        if not settings.MINIO_ACCESS_KEY or not settings.MINIO_SECRET_KEY:
            raise RuntimeError(
                "MINIO_ACCESS_KEY / MINIO_SECRET_KEY are required to upload media "
                "(write-capable MinIO credentials -- do not reuse the backend's "
                "read-only FILE_SERVER_* key)."
            )
        self.bucket = settings.MINIO_BUCKET
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.MINIO_ENDPOINT_URL,
            aws_access_key_id=settings.MINIO_ACCESS_KEY,
            aws_secret_access_key=settings.MINIO_SECRET_KEY,
            region_name=settings.MINIO_REGION,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    def object_exists(self, key: str) -> Tuple[bool, int]:
        """HEAD `key`. Never raises; returns (False, 0) for anything short of
        a confirmed object."""
        try:
            resp = self._client.head_object(Bucket=self.bucket, Key=key)
            return True, int(resp.get("ContentLength", 0))
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code not in ("404", "NoSuchKey", "NotFound"):
                logger.warning("HEAD %s returned unexpected error: %s", key, exc)
            return False, 0
        except BotoCoreError as exc:
            logger.warning("HEAD %s failed: %s", key, exc)
            return False, 0

    def upload_and_confirm(
        self,
        key: str,
        stream: IterStream,
        content_type: Optional[str] = None,
        expected_size: Optional[int] = None,
    ) -> UploadResult:
        """Upload `stream` to `key`, then HEAD it back before declaring
        success. Callers must only mark a row DOWNLOADED when `.ok` is True
        -- the PUT succeeding is not, by itself, proof the object is
        retrievable."""
        if not valid_key(key):
            return UploadResult(ok=False, key=key, error=f"rejected key: {key!r}")

        extra_args = {"ContentType": content_type} if content_type else None
        try:
            self._client.upload_fileobj(stream, self.bucket, key, ExtraArgs=extra_args)
        except (BotoCoreError, ClientError) as exc:
            return UploadResult(ok=False, key=key, error=f"upload failed: {exc}")

        exists, size = self.object_exists(key)
        if not exists:
            return UploadResult(ok=False, key=key, error="upload did not confirm on HEAD after PUT")
        if expected_size and size != expected_size:
            return UploadResult(
                ok=False,
                key=key,
                size=size,
                error=f"size mismatch after upload: wrote {size}, expected {expected_size}",
            )

        etag = None
        try:
            etag = self._client.head_object(Bucket=self.bucket, Key=key).get("ETag", "").strip('"')
        except Exception:  # noqa: BLE001 - best-effort only, never fails the upload
            pass
        return UploadResult(ok=True, key=key, size=size, etag=etag)

    def delete_object(self, key: str) -> None:
        """Best-effort cleanup of a failed/partial upload. Never raises."""
        try:
            self._client.delete_object(Bucket=self.bucket, Key=key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("cleanup delete of %s failed (non-fatal): %s", key, exc)


_store: Optional[ObjectStore] = None


def get_object_store() -> ObjectStore:
    """Lazy singleton so importing this module doesn't itself require MinIO
    credentials to be configured."""
    global _store
    if _store is None:
        _store = ObjectStore()
    return _store
