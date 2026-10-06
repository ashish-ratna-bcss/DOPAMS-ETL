"""Process-level locks so the same CCTNS V1 entity cannot extract concurrently."""
from __future__ import annotations

import logging
import os
from types import TracebackType
from typing import Optional, Type

logger = logging.getLogger("cctns_v1_etl.run_lock")


def entity_lock_path(entity: str) -> str:
    lock_dir = os.environ.get("CCTNS_V1_ETL_LOCK_DIR", "/tmp")
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in entity)
    return os.path.join(lock_dir, f"cctns_v1_etl_{safe}.lock")


class EntityRunLock:
    """Non-blocking exclusive flock; second worker fails immediately."""

    def __init__(self, entity: str):
        self.entity = entity
        self.path = entity_lock_path(entity)
        self._fh = None

    def __enter__(self) -> "EntityRunLock":
        os.makedirs(os.path.dirname(self.path) or "/tmp", exist_ok=True)
        self._fh = open(self.path, "w", encoding="utf-8")
        try:
            import fcntl

            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._fh.close()
            self._fh = None
            raise RuntimeError(
                f"Another cctns_v1 ETL for entity={self.entity!r} is already running "
                f"(lock {self.path}). Refusing concurrent extract against the same API."
            ) from exc
        self._fh.write(str(os.getpid()))
        self._fh.flush()
        logger.info("Acquired run lock for entity=%s path=%s pid=%s", self.entity, self.path, os.getpid())
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        if self._fh is None:
            return
        try:
            import fcntl

            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            self._fh.close()
            self._fh = None
            logger.info("Released run lock for entity=%s", self.entity)
