"""
Client for CCTNS Alfresco media download API.
Downloads FIR and Court document attachments and uploads them directly to
the shared MinIO bucket under <attach_path>/<dms_file_name> -- no local
file is written.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import settings
from storage.object_store import IterStream, assert_v1_key, get_object_store

logger = logging.getLogger(__name__)


def _get_session() -> requests.Session:
    session = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=1.0,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retries, pool_connections=20, pool_maxsize=20)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


_GLOBAL_SESSION = _get_session()


def download_media_file(
    attach_path: str,
    dms_file_name: str,
    base_dir: Optional[str] = None,
    overwrite: bool = False,
    timeout_secs: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Downloads a single media attachment from Alfresco and uploads it
    directly to the shared MinIO bucket as:
    <attach_path>/<dms_file_name>

    `base_dir` is accepted for call-site compatibility but unused -- there
    is no local filesystem destination anymore.

    Returns a dict with:
      - ok: bool
      - status: 'DOWNLOADED' | 'CACHED' | 'FAILED' | 'NOT_FOUND'
      - local_path: str (the MinIO object key -- same field name callers/DB
        already expect, now holding a key instead of a filesystem path)
      - file_size: int (bytes)
      - error_message: str | None
    """
    endpoint = settings.ALFRESCO_DOWNLOAD_API_URL
    if not endpoint:
        raise RuntimeError(
            "ALFRESCO_DOWNLOAD_API_URL is not configured in .env / settings."
        )

    attach_clean = attach_path.strip().lstrip("/\\")
    file_clean = dms_file_name.strip().lstrip("/\\")
    key = f"{attach_clean}/{file_clean}"
    assert_v1_key(key)

    store = get_object_store()

    # Check cache / skip if the object already exists and has content
    if not overwrite:
        exists, size = store.object_exists(key)
        if exists and size > 0:
            return {
                "ok": True,
                "status": "CACHED",
                "local_path": key,
                "file_size": size,
                "error_message": None,
            }

    query_params = {
        "path": attach_clean,
        "name": file_clean,
    }
    url = f"{endpoint}?{urlencode(query_params)}"
    timeout = timeout_secs or settings.MEDIA_DOWNLOAD_TIMEOUT_SECS

    try:
        response = _GLOBAL_SESSION.get(url, stream=True, timeout=timeout)
        if response.status_code == 404:
            return {
                "ok": False,
                "status": "NOT_FOUND",
                "local_path": key,
                "file_size": 0,
                "error_message": f"HTTP 404: Document not found in Alfresco ({url})",
            }
        response.raise_for_status()

        content_length = response.headers.get("Content-Length")
        expected_size = int(content_length) if content_length and content_length.isdigit() else None
        content_type = response.headers.get("Content-Type")

        stream = IterStream(response.iter_content(chunk_size=65536))
        result = store.upload_and_confirm(
            key, stream, content_type=content_type, expected_size=expected_size
        )

        if not result.ok:
            store.delete_object(key)  # best-effort cleanup of any partial object
            if stream.total_bytes == 0:
                return {
                    "ok": False,
                    "status": "NOT_FOUND",
                    "local_path": key,
                    "file_size": 0,
                    "error_message": "Document not available on Alfresco DMS (0 bytes)",
                }
            return {
                "ok": False,
                "status": "FAILED",
                "local_path": key,
                "file_size": 0,
                "error_message": result.error,
            }

        return {
            "ok": True,
            "status": "DOWNLOADED",
            "local_path": key,
            "file_size": result.size,
            "error_message": None,
        }

    except Exception as exc:
        return {
            "ok": False,
            "status": "FAILED",
            "local_path": key,
            "file_size": 0,
            "error_message": str(exc),
        }
