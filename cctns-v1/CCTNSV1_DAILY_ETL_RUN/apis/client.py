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


def to_ddmmyyyy(d: date) -> str:
    return d.strftime("%d-%m-%Y")


class ApiError(Exception):
    pass


def request_with_retry(method: str, url: str, params: dict = None, json_body: dict = None):
    last_err = None
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
        except Exception as err:  # noqa: BLE001 - any failure here should retry
            last_err = err
            logger.warning("Request attempt %d/%d failed (%s %s): %s", attempt, MAX_RETRIES, method, url, err)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECS * attempt)
    raise ApiError(str(last_err))


def fetch_unfiltered(method: str, url: str, extra_params: dict = None):
    """Plain single call, no date range. Used by FIR / Court / Accused Details."""
    result = request_with_retry(method, url, params=extra_params or {})
    if isinstance(result, dict):
        return result.get("data", []), []
    if isinstance(result, list):
        return result, []
    return [], []


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
    recursively. Returns (records, failed_windows)."""
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
        return [], [f"{to_ddmmyyyy(start)} to {to_ddmmyyyy(end)}: {err}"]

    message = str(result.get("message", "")) if isinstance(result, dict) else ""

    if ORA_BUFFER_ERROR in message:
        days = (end - start).days
        if days > 0 and depth < MAX_SPLIT_DEPTH:
            mid = start + timedelta(days=days // 2)
            recs1, fail1 = fetch_date_chunk_safe(method, url, base_params, start, mid, depth + 1)
            recs2, fail2 = fetch_date_chunk_safe(method, url, base_params, mid + timedelta(days=1), end, depth + 1)
            return recs1 + recs2, fail1 + fail2
        return [], [f"{to_ddmmyyyy(start)} to {to_ddmmyyyy(end)}: {message} (max split depth reached)"]

    records = result.get("data", []) if isinstance(result, dict) else (result if isinstance(result, list) else [])
    return records, []


def fetch_full_range_chunked(method: str, url: str, base_params: dict, start: date, end: date):
    """7-day pull with adaptive halving. Used by Accused date-range only."""
    all_records, all_failed = [], []
    for range_start, range_end in date_chunk_ranges(start, end):
        logger.info("Fetching %s to %s from %s", range_start, range_end, url)
        records, failed = fetch_date_chunk_safe(method, url, base_params, range_start, range_end)
        all_records.extend(records)
        all_failed.extend(failed)
    return all_records, all_failed
