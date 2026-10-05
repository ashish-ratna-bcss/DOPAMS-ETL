"""
Client for CCTNS Alfresco media download API.
Downloads FIR and Court document attachments and saves them hierarchically to disk.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import settings

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
    Downloads a single media attachment from Alfresco and saves to disk:
    <base_dir>/<attach_path>/<dms_file_name>

    Returns a dict with:
      - ok: bool
      - status: 'DOWNLOADED' | 'CACHED' | 'FAILED' | 'NOT_FOUND'
      - local_path: str (target path on disk)
      - file_size: int (bytes)
      - error_message: str | None
    """
    endpoint = settings.ALFRESCO_DOWNLOAD_API_URL
    if not endpoint:
        raise RuntimeError(
            "ALFRESCO_DOWNLOAD_API_URL is not configured in .env / settings."
        )

    base = Path(base_dir or settings.MEDIA_BASE_DIR)
    attach_clean = attach_path.strip().lstrip("/\\")
    file_clean = dms_file_name.strip().lstrip("/\\")

    target_file = base / attach_clean / file_clean
    target_str = str(target_file.resolve())

    # Check cache / skip if file exists and has content
    if not overwrite and target_file.exists():
        try:
            sz = target_file.stat().st_size
            if sz > 0:
                return {
                    "ok": True,
                    "status": "CACHED",
                    "local_path": target_str,
                    "file_size": sz,
                    "error_message": None,
                }
        except OSError:
            pass

    # Ensure parent directories exist
    target_file.parent.mkdir(parents=True, exist_ok=True)
    temp_file = target_file.with_suffix(target_file.suffix + ".tmp")

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
                "local_path": target_str,
                "file_size": 0,
                "error_message": f"HTTP 404: Document not found in Alfresco ({url})",
            }
        response.raise_for_status()

        total_bytes = 0
        with open(temp_file, "wb") as f:
            for chunk in response.iter_content(chunk_size=16384):
                if chunk:
                    f.write(chunk)
                    total_bytes += len(chunk)

        if total_bytes == 0:
            if temp_file.exists():
                temp_file.unlink(missing_ok=True)
            return {
                "ok": False,
                "status": "NOT_FOUND",
                "local_path": target_str,
                "file_size": 0,
                "error_message": "Document not available on Alfresco DMS (0 bytes)",
            }


        # Atomic rename from .tmp to final target file
        temp_file.replace(target_file)

        return {
            "ok": True,
            "status": "DOWNLOADED",
            "local_path": target_str,
            "file_size": total_bytes,
            "error_message": None,
        }

    except Exception as exc:
        if temp_file.exists():
            temp_file.unlink(missing_ok=True)
        return {
            "ok": False,
            "status": "FAILED",
            "local_path": target_str,
            "file_size": 0,
            "error_message": str(exc),
        }
