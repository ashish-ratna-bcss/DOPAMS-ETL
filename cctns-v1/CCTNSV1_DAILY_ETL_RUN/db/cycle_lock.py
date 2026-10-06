"""One exclusive lock for an entire CCTNS V1 daily cycle.

Held for the whole FIR -> court/accused-details -> accused run, including CLI.
A second cycle on the same host fails immediately instead of interleaving.
"""
from __future__ import annotations

import logging
import os
import threading
from types import TracebackType
from typing import Optional, Type

logger = logging.getLogger("cctns_v1_etl.cycle_lock")

# flock/LockFile exclude other processes. This excludes other threads in this
# process, including on Windows where a second handle in the same process
# would otherwise inherit the byte-range lock.
_process_guard = threading.Lock()

try:
    import fcntl
except ImportError:  # Windows hosts used for tests; production is Linux.
    fcntl = None

try:
    import msvcrt
except ImportError:
    msvcrt = None


def cycle_lock_path() -> str:
    lock_dir = os.environ.get("CCTNS_V1_ETL_LOCK_DIR", "/tmp")
    return os.path.join(lock_dir, "cctns_v1_etl_cycle.lock")


class CycleRunLock:
    """Non-blocking exclusive lock. Not re-entrant within one process."""

    def __init__(self, path: str | None = None):
        self.path = path or cycle_lock_path()
        self._fh = None
        self._holds_process_guard = False

    def __enter__(self) -> "CycleRunLock":
        if not _process_guard.acquire(blocking=False):
            raise RuntimeError(
                "Another cctns_v1 daily cycle is already running "
                f"(lock {self.path}). Refusing to overlap cycles."
            )
        self._holds_process_guard = True
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            self._fh = open(self.path, "a+b")
            _acquire(self._fh)
        except OSError as exc:
            if self._fh is not None:
                self._fh.close()
                self._fh = None
            self._release_process_guard()
            raise RuntimeError(
                "Another cctns_v1 daily cycle is already running "
                f"(lock {self.path}). Refusing to overlap cycles."
            ) from exc
        logger.info("Acquired V1 cycle lock path=%s pid=%s", self.path, os.getpid())
        return self

    def _release_process_guard(self) -> None:
        if self._holds_process_guard:
            self._holds_process_guard = False
            _process_guard.release()

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        try:
            if self._fh is not None:
                try:
                    _release(self._fh)
                finally:
                    self._fh.close()
                    self._fh = None
                    logger.info("Released V1 cycle lock")
        finally:
            self._release_process_guard()


def _acquire(fh) -> None:
    if fcntl is not None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return
    if msvcrt is None:
        raise OSError("No file-lock implementation is available")
    fh.seek(0)
    if fh.read(1) != b"\0":
        fh.seek(0)
        fh.write(b"\0")
        fh.flush()
    fh.seek(0)
    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)


def _release(fh) -> None:
    if fcntl is not None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        return
    if msvcrt is None:
        return
    fh.seek(0)
    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
