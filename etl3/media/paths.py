"""Resolve and classify media paths without inventing files or IDs.

V1 stores absolute host paths (historically under /home/tganb/...).
V2 stores shared-storage-relative paths under FILES_MEDIA_BASE_PATH
(/mnt/shared-etl-files) as recorded in file_media_bookkeeping.file_path.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from etl3.config import settings


AVAILABILITY = (
    "VERIFIED_ACCESSIBLE",
    "BOOKKEEPING_DOWNLOADED_INACCESSIBLE",
    "MISSING_AT_SOURCE",
    "EMPTY_AT_SOURCE",
    "PENDING",
    "DOWNLOAD_FAILED",
    "INVALID_PATH",
    "UNRESOLVED_FILE_ID",
)


def _env(name: str, default: str = "") -> str:
    raw = os.environ.get(name)
    if raw is not None and str(raw).strip():
        return str(raw).strip()
    # Prefer source env files when present (V1 MEDIA_BASE_DIR / V2 FILES path)
    try:
        from dotenv import dotenv_values

        for path in (settings.V1_SOURCE_ENV_PATH, settings.V2_SOURCE_ENV_PATH):
            if path and Path(path).is_file():
                vals = dotenv_values(path)
                if vals.get(name) and str(vals.get(name)).strip():
                    return str(vals.get(name)).strip()
    except Exception:
        pass
    return default


def media_roots() -> dict:
    """Authorized media roots on this host. Never invent roots."""
    v1 = _env("MEDIA_BASE_DIR", "/home/eagle/media_cctnsv1")
    v2 = _env("FILES_MEDIA_BASE_PATH", "/mnt/shared-etl-files")
    return {
        "V1_MEDIA": os.path.abspath(v1),
        "V2_SHARED": os.path.abspath(v2),
        # Historical V1 root referenced by bookkeeping; may be absent.
        "V1_HISTORICAL": "/home/tganb/dopams/media_cctnsv1",
    }


def safe_join_under_root(root: str, *parts: str) -> Optional[str]:
    """Join path parts under root; return None on traversal or escape."""
    if not root:
        return None
    root_resolved = Path(root).resolve()
    cleaned = []
    for part in parts:
        if part is None:
            continue
        text = str(part).replace("\\", "/").strip()
        if not text:
            continue
        # Reject absolute fragments and parent references before join.
        if text.startswith("/") or text.startswith("~"):
            text = text.lstrip("/").lstrip("~")
        for segment in text.split("/"):
            if segment in ("", "."):
                continue
            if segment == ".." or ":" in segment:
                return None
            cleaned.append(segment)
    candidate = root_resolved.joinpath(*cleaned) if cleaned else root_resolved
    try:
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError):
        return None
    try:
        resolved.relative_to(root_resolved)
    except ValueError:
        return None
    return str(resolved)


# Common extensions used by the V2 media server (Content-Type → ext).
# Prefer probing these over scandir() of huge NFS directories.
_V2_EXTS = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".bin", ".json")
_KNOWN_EXT_SET = {e.lower() for e in _V2_EXTS}

# Cache parent-dir existence so missing NFS trees fail once, not per file×ext.
_parent_exists_cache: dict[str, bool] = {}
_root_exists_cache: dict[str, bool] = {}


def clear_dir_cache() -> None:
    """Reset parent/root existence caches between consolidation runs."""
    _parent_exists_cache.clear()
    _root_exists_cache.clear()


def _path_exists_dir(path: str) -> bool:
    cached = _root_exists_cache.get(path)
    if cached is not None:
        return cached
    try:
        ok = os.path.isdir(path)
    except OSError:
        ok = False
    _root_exists_cache[path] = ok
    return ok


def _parent_exists(path: str) -> bool:
    parent = str(Path(path).parent)
    cached = _parent_exists_cache.get(parent)
    if cached is not None:
        return cached
    try:
        ok = os.path.isdir(parent)
    except OSError:
        ok = False
    _parent_exists_cache[parent] = ok
    return ok


def _file_accessible(path: Optional[str]) -> tuple[bool, int]:
    if not path:
        return False, 0
    try:
        # Fail fast when the parent tree is absent (common for historical V1
        # roots and missing V2 shared subtrees). Avoids N×ext NFS stats.
        if not _parent_exists(path):
            return False, 0
        p = Path(path)
        if p.is_file():
            sz = p.stat().st_size
            return sz > 0, sz
        # Path already has a known extension — do not probe other suffixes.
        suffix = p.suffix.lower()
        if suffix in _KNOWN_EXT_SET:
            return False, 0
        # V2 bookkeeping stores paths without extension. Probe known suffixes
        # instead of listing the parent directory (NFS dirs can be huge).
        for ext in _V2_EXTS:
            candidate = Path(str(path) + ext)
            if not _parent_exists(str(candidate)):
                return False, 0
            if candidate.is_file():
                sz = candidate.stat().st_size
                if sz > 0:
                    return True, sz
        return False, 0
    except OSError:
        return False, 0


def resolve_v1_paths(payload: dict) -> dict:
    """Preserve absolute source path; optionally remap under MEDIA_BASE_DIR."""
    roots = media_roots()
    source_local = payload.get("local_path")
    attach = (payload.get("attach_path") or "").strip().lstrip("/\\")
    name = (payload.get("dms_file_name") or "").strip().lstrip("/\\")
    relative = None
    if attach and name and ".." not in attach and ".." not in name:
        relative = f"{attach}/{name}".replace("\\", "/")
    resolved_abs = None
    root_key = None
    # Prefer configured MEDIA_BASE_DIR when the relative layout is known.
    if relative and _path_exists_dir(roots["V1_MEDIA"]):
        joined = safe_join_under_root(roots["V1_MEDIA"], relative)
        if joined:
            ok, _ = _file_accessible(joined)
            if ok:
                resolved_abs = joined
                root_key = "V1_MEDIA"
    # Fall back to recorded absolute path only if it resolves under an authorized root.
    if resolved_abs is None and source_local:
        try:
            abs_path = str(Path(source_local).resolve(strict=False))
        except (OSError, RuntimeError):
            abs_path = None
        if abs_path:
            for key in ("V1_MEDIA", "V1_HISTORICAL"):
                root = roots[key]
                # Do not treat an absent historical root as a resolvable location.
                if not _path_exists_dir(root):
                    continue
                try:
                    if abs_path == root or abs_path.startswith(root.rstrip("/") + "/"):
                        # Still require no traversal out of that root.
                        if safe_join_under_root(root, abs_path[len(root):].lstrip("/")):
                            resolved_abs = abs_path
                            root_key = key
                            break
                except Exception:
                    continue
    return {
        "source_local_path": source_local,
        "resolved_relative_path": relative,
        "resolved_absolute_path": resolved_abs,
        "media_root_key": root_key if resolved_abs else ("V1_MEDIA" if relative else None),
    }


def classify_v1_availability(payload: dict, paths: Optional[dict] = None) -> tuple[str, str]:
    status = (payload.get("status") or "").upper()
    err = payload.get("error_message") or ""
    paths = paths or resolve_v1_paths(payload)

    if status == "NOT_FOUND" or "0 bytes" in err.lower():
        return "EMPTY_AT_SOURCE", err or "Document not available on Alfresco DMS (0 bytes)"
    if status in ("PENDING",):
        return "PENDING", err or "pending download"
    if status == "FAILED":
        return "DOWNLOAD_FAILED", err or "download failed"
    if status in ("DOWNLOADED", "CACHED"):
        check_path = paths.get("resolved_absolute_path") or paths.get("source_local_path")
        ok, size = _file_accessible(check_path)
        if ok:
            return "VERIFIED_ACCESSIBLE", f"readable file size={size}"
        # Remap attempt already embedded in resolve_v1_paths.
        detail = (
            f"source status={status} but file inaccessible on this host; "
            f"source_local_path={paths.get('source_local_path')!r}"
        )
        return "BOOKKEEPING_DOWNLOADED_INACCESSIBLE", detail
    if paths.get("source_local_path") and ".." in str(paths.get("source_local_path")):
        return "INVALID_PATH", "path contains parent reference"
    return "DOWNLOAD_FAILED", err or f"unrecognized status={status!r}"


def resolve_v2_paths(payload: dict) -> dict:
    roots = media_roots()
    file_path = payload.get("file_path")
    relative = None
    if file_path:
        relative = str(file_path).replace("\\", "/").lstrip("/")
        if ".." in relative.split("/"):
            relative = None
    resolved_abs = None
    root_key = None
    v2_root = roots["V2_SHARED"]
    if relative and _path_exists_dir(v2_root):
        joined = safe_join_under_root(v2_root, relative)
        if joined:
            resolved_abs = joined
            root_key = "V2_SHARED"
    elif relative:
        # Preserve the intended relative path even when the shared root is
        # missing on this host; do not invent an absolute location.
        root_key = "V2_SHARED"
    return {
        "source_local_path": file_path,
        "resolved_relative_path": relative,
        "resolved_absolute_path": resolved_abs,
        "media_root_key": root_key,
    }


def classify_v2_availability(payload: dict, paths: Optional[dict] = None) -> tuple[str, str]:
    paths = paths or resolve_v2_paths(payload)
    err = payload.get("download_error") or ""
    err_u = err.upper()
    file_id = payload.get("file_id")
    is_empty = bool(payload.get("is_empty"))
    is_downloaded = bool(payload.get("is_downloaded"))

    if file_id is None and (
        "FILE_ID IS NULL" in err_u
        or err.startswith("SOURCE: MEDIA present but file_id is null")
        or (payload.get("has_field") and not is_empty and not is_downloaded and file_id is None and not err)
    ):
        # Annotated null file_id OR has_field with no id and not empty/downloaded.
        if err.startswith("SOURCE:") or (payload.get("has_field") and file_id is None and not is_empty):
            return "UNRESOLVED_FILE_ID", err or "MEDIA indicated but file_id is null"

    if is_empty:
        return "EMPTY_AT_SOURCE", err or "source marked is_empty"
    if err_u.startswith("PERMANENT:") or "HTTP 400" in err_u or "HTTP 404" in err_u:
        return "DOWNLOAD_FAILED", err
    if "MISSING ON DISK" in err_u:
        return "BOOKKEEPING_DOWNLOADED_INACCESSIBLE", err
    if file_id is None and not is_empty and payload.get("has_field"):
        return "UNRESOLVED_FILE_ID", err or "file_id is null"
    if not is_downloaded:
        if not err and not file_id:
            return "PENDING", "not downloaded"
        if err:
            return "DOWNLOAD_FAILED", err
        return "PENDING", "not downloaded"
    # is_downloaded True — verify on this host
    check_path = paths.get("resolved_absolute_path")
    if check_path is None and paths.get("resolved_relative_path") is None and file_id:
        return "INVALID_PATH", "downloaded but no resolvable path under V2_SHARED"
    ok, size = _file_accessible(check_path)
    if ok:
        return "VERIFIED_ACCESSIBLE", f"readable file size={size}"
    return (
        "BOOKKEEPING_DOWNLOADED_INACCESSIBLE",
        f"is_downloaded=true but file inaccessible at {check_path!r}; {err}".strip(),
    )
