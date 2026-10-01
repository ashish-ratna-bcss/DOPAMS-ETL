"""Parse extract failed_windows and persist OPEN / RESOLVED ledger rows."""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any
from uuid import UUID

from config.settings import PG_ETL_SCHEMA

# "05-05-2002 to 05-05-2002: Error ..." or structured dicts from newer code.
_WINDOW_RE = re.compile(
    r"^(\d{2}-\d{2}-\d{4})\s+to\s+(\d{2}-\d{2}-\d{4})\s*:\s*(.*)$",
    re.DOTALL,
)


class FailedWindowParseError(ValueError):
    """Raised when raw failed_windows cannot all be normalized (fail-closed)."""


def _parse_ddmmyyyy(value: str) -> date:
    return datetime.strptime(value.strip(), "%d-%m-%Y").date()


def normalize_failed_windows(raw: list | None) -> list[dict[str, Any]]:
    """Turn client strings or dicts into structured window records.

    Fail-closed: any unparseable entry raises FailedWindowParseError so the
    pipeline never treats a partial/garbled failure list as a clean extract.
    """
    if not raw:
        return []
    out: list[dict[str, Any]] = []
    unparsed: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            start = item.get("window_start")
            end = item.get("window_end")
            try:
                if isinstance(start, str):
                    start = _parse_ddmmyyyy(start)
                if isinstance(end, str):
                    end = _parse_ddmmyyyy(end)
            except (TypeError, ValueError):
                unparsed.append(repr(item)[:240])
                continue
            error = str(item.get("error") or item.get("error_message") or "")
            if start is None or end is None or not isinstance(start, date) or not isinstance(end, date):
                unparsed.append(repr(item)[:240])
                continue
            out.append(
                {
                    "window_start": start,
                    "window_end": end,
                    "error": error or "unknown",
                }
            )
            continue
        text = str(item)
        match = _WINDOW_RE.match(text)
        if not match:
            unparsed.append(text[:240])
            continue
        try:
            out.append(
                {
                    "window_start": _parse_ddmmyyyy(match.group(1)),
                    "window_end": _parse_ddmmyyyy(match.group(2)),
                    "error": match.group(3).strip() or text,
                }
            )
        except ValueError:
            unparsed.append(text[:240])
    if unparsed:
        preview = "; ".join(unparsed[:5])
        raise FailedWindowParseError(
            f"{len(unparsed)} of {len(raw)} failed_window entr(y/ies) unparseable: {preview}"
        )
    return out


def failed_windows_for_run_log(windows: list[dict[str, Any]]) -> list[dict[str, str]]:
    """JSONB-friendly snapshot for cctns_v1_etl_run_log.failed_windows."""
    return [
        {
            "window_start": w["window_start"].strftime("%d-%m-%Y"),
            "window_end": w["window_end"].strftime("%d-%m-%Y"),
            "error": w["error"],
        }
        for w in windows
    ]


def record_open_failed_windows(cur, entity: str, run_id: str | UUID, windows: list[dict[str, Any]]) -> int:
    """Upsert each failed window as OPEN (bump attempt_count / last_seen)."""
    if not windows:
        return 0
    rid = str(run_id)
    n = 0
    for w in windows:
        cur.execute(
            f"""
            INSERT INTO {PG_ETL_SCHEMA}.cctns_v1_failed_fetch_window
                (entity, window_start, window_end, error, attempt_count,
                 first_seen_at, last_seen_at, status, run_id)
            VALUES (%s, %s, %s, %s, 1, now(), now(), 'OPEN', %s::uuid)
            ON CONFLICT (entity, window_start, window_end)
            DO UPDATE SET
                error = EXCLUDED.error,
                attempt_count = {PG_ETL_SCHEMA}.cctns_v1_failed_fetch_window.attempt_count + 1,
                last_seen_at = now(),
                status = 'OPEN',
                run_id = EXCLUDED.run_id
            """,
            (entity, w["window_start"], w["window_end"], w["error"], rid),
        )
        n += 1
    return n


def resolve_windows_not_failing(
    cur,
    entity: str,
    run_id: str | UUID,
    still_failing: list[dict[str, Any]],
) -> int:
    """Mark OPEN ledger rows RESOLVED when this full extract no longer fails them."""
    rid = str(run_id)
    if not still_failing:
        cur.execute(
            f"""
            UPDATE {PG_ETL_SCHEMA}.cctns_v1_failed_fetch_window
            SET status = 'RESOLVED',
                last_seen_at = now(),
                run_id = %s::uuid
            WHERE entity = %s AND status = 'OPEN'
            """,
            (rid, entity),
        )
        return cur.rowcount

    pairs = [(w["window_start"], w["window_end"]) for w in still_failing]
    cur.execute(
        f"""
        UPDATE {PG_ETL_SCHEMA}.cctns_v1_failed_fetch_window AS f
        SET status = 'RESOLVED',
            last_seen_at = now(),
            run_id = %s::uuid
        WHERE f.entity = %s
          AND f.status = 'OPEN'
          AND NOT EXISTS (
              SELECT 1
              FROM unnest(%s::date[], %s::date[]) AS x(window_start, window_end)
              WHERE x.window_start = f.window_start AND x.window_end = f.window_end
          )
        """,
        (
            rid,
            entity,
            [p[0] for p in pairs],
            [p[1] for p in pairs],
        ),
    )
    return cur.rowcount
