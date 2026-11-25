"""
Shared HTTP plumbing for all 4 CCTNS V1 endpoints: the date-chunk-with-
adaptive-halving logic that the Accused date-range endpoint needs (its
Oracle backend throws ORA-06502 "buffer too small" on wide date ranges --
confirmed from real captured runs in cctnsv1/response/).

FIR, Court and Accused Details do NOT need chunking -- confirmed from real
captured responses (01/02/03_*.json) that a single unfiltered GET returns the
full dataset cleanly in a few seconds. Only apis/accused.py uses the chunking
helpers here.

No auth headers -- confirmed live against all 4 endpoints that none of them
require Authorization/x-api-key.
"""
import time
import logging
from datetime import date, timedelta

import requests

logger = logging.getLogger("cctns_v1_etl.apis")

import os

ORA_BUFFER_ERROR = "ORA-06502"
MAX_SPLIT_DEPTH = 5
# Accused POST is pulled in 7-day windows (inclusive). A wider range hits ORA-06502.
DATE_CHUNK_DAYS = 7
REQUEST_TIMEOUT_SECS = int(os.environ.get("CCTNS_REQUEST_TIMEOUT_SECS", "300"))
MAX_RETRIES = 3
RETRY_BACKOFF_SECS = 5

# Whole-window retry: on top of the per-HTTP-call retries above, a transient
# failure gets this many additional attempts at the SAME 7-day window before
# it is recorded as unresolved. Separate from MAX_RETRIES because a window
# failure (all 3 HTTP attempts exhausted) is a stronger signal than one HTTP
# attempt failing, and deserves its own backoff schedule.
WINDOW_MAX_RETRIES = int(os.environ.get("CCTNS_WINDOW_MAX_RETRIES", "3"))
WINDOW_BACKOFF_BASE_SECS = int(os.environ.get("CCTNS_WINDOW_BACKOFF_BASE_SECS", "30"))
WINDOW_BACKOFF_MAX_SECS = int(os.environ.get("CCTNS_WINDOW_BACKOFF_MAX_SECS", "300"))

# Failure classification. Used only to decide whether a whole-window retry is
# worth attempting -- never to decide whether to report a window as failed.
KIND_ORA_GAP = "ora_gap"      # permanent Oracle buffer bug; halving already handles it
KIND_TRANSIENT = "transient"  # timeout / connection error; worth retrying
KIND_PERMANENT = "permanent"  # HTTP 4xx; retrying is very unlikely to help
KIND_UNKNOWN = "unknown"      # unclassified; treated as retryable (fail-closed, not fail-silent)


def to_ddmmyyyy(d: date) -> str:
    return d.strftime("%d-%m-%Y")


class ApiError(Exception):
    def __init__(self, message, *, kind: str = KIND_UNKNOWN):
        super().__init__(message)
        self.kind = kind


def request_with_retry(method: str, url: str, params: dict = None, json_body: dict = None):
    last_err = None
    last_kind = KIND_UNKNOWN
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.request(
                method, url,
                params=params if method == "GET" else None,
                json=json_body if method == "POST" else None,
                headers={"Accept": "application/json"},
                timeout=REQUEST_TIMEOUT_SECS,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.Timeout as err:
            last_err, last_kind = err, KIND_TRANSIENT
        except requests.exceptions.ConnectionError as err:
            last_err, last_kind = err, KIND_TRANSIENT
        except requests.exceptions.HTTPError as err:
            status = err.response.status_code if err.response is not None else None
            last_kind = KIND_PERMANENT if status and 400 <= status < 500 else KIND_TRANSIENT
            last_err = err
        except Exception as err:  # noqa: BLE001 - any other failure here should retry
            last_err, last_kind = err, KIND_UNKNOWN
        logger.warning("Request attempt %d/%d failed (%s %s): %s", attempt, MAX_RETRIES, method, url, last_err)
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_BACKOFF_SECS * attempt)
    raise ApiError(str(last_err), kind=last_kind)


_MISSING = object()


def _records_from_payload(result, *, context: str):
    """Extract a list of records or raise ApiError (fail-closed on weird shapes)."""
    if isinstance(result, list):
        return result
    if not isinstance(result, dict):
        raise ApiError(f"{context}: unexpected response type {type(result).__name__}")
    message = str(result.get("message") or result.get("error") or "").strip()
    data = result.get("data", _MISSING)
    if data is _MISSING:
        if message:
            raise ApiError(f"{context}: {message}")
        raise ApiError(f"{context}: response has no 'data' key")
    if not isinstance(data, list):
        raise ApiError(f"{context}: 'data' must be a list, got {type(data).__name__}")
    # Explicit API error with empty payload must not look like a clean 0-row pull.
    if message and not data and (
        ORA_BUFFER_ERROR in message
        or "error" in message.lower()
        or message.lower().startswith("fail")
    ):
        raise ApiError(f"{context}: {message}")
    return data


def fetch_unfiltered(method: str, url: str, extra_params: dict = None):
    """Plain single call, no date range. Used by FIR / Court / Accused Details."""
    result = request_with_retry(method, url, params=extra_params or {})
    records = _records_from_payload(result, context=f"{method} {url}")
    return records, []


def date_chunk_ranges(start: date, end: date, chunk_days: int = DATE_CHUNK_DAYS):
    """Inclusive windows of `chunk_days` from start through end.

    Example with 7 days: 01-01-2002→07-01-2002, then 08-01-2002→14-01-2002.
    The last window is shorter when the remaining span is under 7 days.
    """
    if chunk_days < 1:
        raise ValueError("chunk_days must be at least 1")
    ranges = []
    cursor = start
    while cursor <= end:
        range_end = min(cursor + timedelta(days=chunk_days - 1), end)
        ranges.append((cursor, range_end))
        cursor = range_end + timedelta(days=1)
    return ranges


def fetch_date_chunk_safe(method: str, url: str, base_params: dict, start: date, end: date, depth: int = 0):
    """One date range, GET or POST. On Oracle buffer overflow, halve and retry
    recursively. Returns (records, failed_windows, kind).

    `kind` is None on success, else one of KIND_ORA_GAP / KIND_TRANSIENT /
    KIND_PERMANENT / KIND_UNKNOWN for the (single, already-irreducible)
    failure this call is reporting. Internal to this module -- callers needing
    the window-level retry decision should go through fetch_full_range_chunked.
    """
    params = dict(base_params)
    params["from_date"] = to_ddmmyyyy(start)
    params["to_date"] = to_ddmmyyyy(end)
    params["startDate"] = params["from_date"]
    params["endDate"] = params["to_date"]

    try:
        result = request_with_retry(method, url,
                                     params=params if method == "GET" else None,
                                     json_body=params if method == "POST" else None)
    except ApiError as err:
        kind = KIND_ORA_GAP if ORA_BUFFER_ERROR in str(err) else err.kind
        return [], [f"{to_ddmmyyyy(start)} to {to_ddmmyyyy(end)}: {err}"], kind

    message = str(result.get("message", "") or result.get("error", "") or "") if isinstance(result, dict) else ""

    if ORA_BUFFER_ERROR in message:
        days = (end - start).days
        if days > 0 and depth < MAX_SPLIT_DEPTH:
            mid = start + timedelta(days=days // 2)
            recs1, fail1, kind1 = fetch_date_chunk_safe(method, url, base_params, start, mid, depth + 1)
            recs2, fail2, kind2 = fetch_date_chunk_safe(method, url, base_params, mid + timedelta(days=1), end, depth + 1)
            return recs1 + recs2, fail1 + fail2, (kind1 or kind2)
        return [], [f"{to_ddmmyyyy(start)} to {to_ddmmyyyy(end)}: {message} (max split depth reached)"], KIND_ORA_GAP

    # Non-ORA error messages on a window → failed window (fail-closed), not silent empty success.
    # Not confirmed permanent (no HTTP error, no ORA signature) -- classify as
    # unknown/retryable rather than guessing it will never recover.
    if isinstance(result, dict) and message and (
        "error" in message.lower() or message.lower().startswith("fail")
    ):
        data = result.get("data")
        if not isinstance(data, list) or not data:
            return [], [f"{to_ddmmyyyy(start)} to {to_ddmmyyyy(end)}: {message}"], KIND_UNKNOWN

    try:
        records = _records_from_payload(
            result,
            context=f"{to_ddmmyyyy(start)} to {to_ddmmyyyy(end)}",
        )
    except ApiError as err:
        kind = KIND_ORA_GAP if ORA_BUFFER_ERROR in str(err) else err.kind
        return [], [f"{to_ddmmyyyy(start)} to {to_ddmmyyyy(end)}: {err}"], kind
    return records, [], None


def _fetch_window_with_recovery(method: str, url: str, base_params: dict, start: date, end: date):
    """One 7-day window with bounded whole-window retry for transient failures.

    Preserves the exact original (method, url, base_params, start, end) on
    every attempt -- only the attempt number and elapsed backoff change.
    Returns (records, failed_window_or_None, attempt_log) where attempt_log is
    a list of (attempt_number, outcome) for diagnostics, and failed_window is
    the same string format fetch_date_chunk_safe already produces (or None on
    success), so callers' existing parsing is unaffected.
    """
    attempt_log = []
    last_failed = None
    for attempt in range(1, WINDOW_MAX_RETRIES + 2):  # 1 initial try + N retries
        records, failed, kind = fetch_date_chunk_safe(method, url, base_params, start, end)
        if not failed:
            attempt_log.append((attempt, "success"))
            if attempt > 1:
                logger.info(
                    "Window %s to %s recovered on attempt %d/%d",
                    to_ddmmyyyy(start), to_ddmmyyyy(end), attempt, WINDOW_MAX_RETRIES + 1,
                )
            return records, None, attempt_log

        last_failed = failed[0]
        attempt_log.append((attempt, f"{kind}: {last_failed}"))

        if kind not in (KIND_TRANSIENT, KIND_UNKNOWN):
            # Known permanent (ORA gap or HTTP 4xx) -- another identical attempt
            # will not change the outcome. Do not retry known permanent errors
            # indiscriminately; report immediately instead.
            logger.warning(
                "Window %s to %s: %s failure, not retrying (%s)",
                to_ddmmyyyy(start), to_ddmmyyyy(end), kind, last_failed,
            )
            return [], last_failed, attempt_log

        if attempt <= WINDOW_MAX_RETRIES:
            backoff = min(WINDOW_BACKOFF_BASE_SECS * (2 ** (attempt - 1)), WINDOW_BACKOFF_MAX_SECS)
            logger.warning(
                "Window %s to %s: %s failure (attempt %d/%d), retrying in %ds",
                to_ddmmyyyy(start), to_ddmmyyyy(end), kind, attempt, WINDOW_MAX_RETRIES + 1, backoff,
            )
            time.sleep(backoff)

    logger.error(
        "Window %s to %s: unresolved after %d attempt(s)",
        to_ddmmyyyy(start), to_ddmmyyyy(end), WINDOW_MAX_RETRIES + 1,
    )
    return [], last_failed, attempt_log


def fetch_full_range_chunked(method: str, url: str, base_params: dict, start: date, end: date):
    """7-day pull with adaptive halving plus bounded whole-window retry.

    Same external contract as before: (all_records, all_failed) where
    all_failed is a list of "<start> to <end>: <error>" strings. A window is
    only ever in all_failed after its own retries (and, for ORA gaps, its own
    halving) are exhausted -- callers (failed_windows.py, pipeline_run.py) are
    unaffected by the retry happening underneath.
    """
    all_records, all_failed = [], []
    for range_start, range_end in date_chunk_ranges(start, end):
        logger.info("Fetching %s to %s from %s", range_start, range_end, url)
        records, failed_entry, _attempt_log = _fetch_window_with_recovery(
            method, url, base_params, range_start, range_end
        )
        all_records.extend(records)
        if failed_entry:
            all_failed.append(failed_entry)
    return all_records, all_failed
