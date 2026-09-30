"""Ordered CCTNS V2 date windows.

A checkpoint may advance only after every window in the run was fetched
and the caller chooses to release it. A failed window is persisted as a
replay floor so the next start cannot jump past it.
"""

from __future__ import annotations

import logging
import sys
from typing import Callable, Iterable, Optional, Tuple

logger = logging.getLogger(__name__)

DateWindow = Tuple[str, str]


class WindowGuard:
    def __init__(self) -> None:
        self.failed_window: Optional[DateWindow] = None
        self.current: Optional[DateWindow] = None

    def begin(self, from_date: str, to_date: str) -> None:
        self.current = (str(from_date), str(to_date))

    def fail(self, from_date: str, to_date: str) -> None:
        if self.failed_window is None:
            self.failed_window = (str(from_date), str(to_date))

    def fail_current(self) -> None:
        if self.current is not None:
            self.fail(self.current[0], self.current[1])

    @property
    def failed(self) -> bool:
        return self.failed_window is not None

    def may_advance(self) -> bool:
        return self.failed_window is None


def run_ordered_windows(
    date_ranges: Iterable[DateWindow],
    process_fn: Callable[[str, str], None],
    guard: WindowGuard,
) -> None:
    """Run date windows from earliest to latest. Stop after the first failure."""
    for from_date, to_date in date_ranges:
        if guard.failed:
            logger.error(
                "Not fetching later window %s → %s after failed window %s → %s",
                from_date,
                to_date,
                guard.failed_window[0],
                guard.failed_window[1],
            )
            break
        guard.begin(from_date, to_date)
        try:
            process_fn(from_date, to_date)
        except Exception:
            guard.fail(from_date, to_date)
            logger.exception("Date window %s → %s raised", from_date, to_date)
            break
        if guard.failed:
            logger.error(
                "Date window %s → %s was not fetched and committed. "
                "Later windows will not run and the checkpoint will not advance.",
                from_date,
                to_date,
            )
            break


def clamp_iso(start_iso: str, replay_iso: Optional[str]) -> str:
    """Pull a resume timestamp back to a failed window's calendar day."""
    if not start_iso or not replay_iso:
        return start_iso
    start_day = str(start_iso)[:10]
    replay_day = str(replay_iso)[:10]
    if replay_day < start_day:
        return replay_day + "T00:00:00+05:30"
    return start_iso


def replay_module_name(module_name: str) -> str:
    return f"{module_name}__replay_from"


def _connect(etl):
    factory = getattr(etl, "_replay_connect", None)
    if factory is not None:
        return factory()
    import psycopg2
    module = sys.modules[type(etl).__module__]
    return psycopg2.connect(**module.DB_CONFIG)


class _ConnCtx:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.conn.commit()
            else:
                self.conn.rollback()
        finally:
            close = getattr(self.conn, "close", None)
            if close is not None:
                close()
        return False


def _connection(etl):
    return _ConnCtx(_connect(etl))


def read_replay_iso(etl, module_name: str) -> Optional[str]:
    name = replay_module_name(module_name)
    with _connection(etl) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT watermark FROM etl_bookkeeping WHERE kind = 'run_state' AND module_name = %s",
            (name,),
        )
        row = cursor.fetchone()
        if not row or row[0] is None:
            return None
        value = row[0]
        return value.isoformat() if hasattr(value, "isoformat") else str(value)


def write_replay_floor(etl, module_name: str, from_date: str) -> None:
    day = str(from_date)[:10] + "T00:00:00+05:30"
    name = replay_module_name(module_name)
    with _connection(etl) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO etl_bookkeeping (kind, module_name, watermark, updated_at)
            VALUES ('run_state', %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (kind, module_name) WHERE kind = 'run_state' DO UPDATE SET
                watermark = EXCLUDED.watermark,
                updated_at = CURRENT_TIMESTAMP
            """,
            (name, day),
        )


def clear_replay_floor(etl, module_name: str) -> None:
    name = replay_module_name(module_name)
    with _connection(etl) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM etl_bookkeeping WHERE kind = 'run_state' AND module_name = %s",
            (name,),
        )


def apply_replay_floor(etl, module_name: str, start_iso: str) -> str:
    try:
        replay = read_replay_iso(etl, module_name)
    except Exception as exc:
        logger.error(
            "Could not read replay floor for %s; refusing the later cursor: %s",
            module_name,
            exc,
        )
        raise
    clamped = clamp_iso(start_iso, replay)
    if clamped != start_iso:
        logger.warning(
            "Replay floor holds %s start at %s (cursor was %s)",
            module_name,
            clamped,
            start_iso,
        )
    return clamped



def begin_run(etl, module_name: str, start_iso: str) -> None:
    """Pin the resume floor before any window in this run commits.

    A crash before checkpoint release leaves the floor in place, so the next
    run starts at this position instead of a later table cursor.
    """
    try:
        write_replay_floor(etl, module_name, start_iso)
    except Exception as exc:
        logger.error(
            "Could not pin replay floor for %s at %s; no windows will run: %s",
            module_name,
            start_iso,
            exc,
        )
        raise


def release_checkpoint(etl, module_name: str) -> bool:
    """Return True only when this run's windows may advance the checkpoint."""
    guard = etl.window_guard
    if guard.may_advance():
        try:
            clear_replay_floor(etl, module_name)
        except Exception as exc:
            logger.error(
                "Could not clear replay floor for %s; checkpoint will not advance: %s",
                module_name,
                exc,
            )
            return False
        return True
    failed = guard.failed_window
    logger.error(
        "Not advancing checkpoint for %s. Failed window %s → %s.",
        module_name,
        failed[0],
        failed[1],
    )
    try:
        write_replay_floor(etl, module_name, failed[0])
    except Exception as exc:
        logger.error("Could not persist replay floor for %s: %s", module_name, exc)
    return False
