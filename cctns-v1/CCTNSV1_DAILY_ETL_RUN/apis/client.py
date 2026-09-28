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

ORA_BUFFER_ERROR = "ORA-06502"
MAX_SPLIT_DEPTH = 5
REQUEST_TIMEOUT_SECS = 60
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


def month_ranges(start: date, end: date):
    ranges = []
    cursor = date(start.year, start.month, 1)
    while cursor <= end:
        next_month = date(cursor.year + 1, 1, 1) if cursor.month == 12 else date(cursor.year, cursor.month + 1, 1)
        range_start = max(cursor, start)
        range_end = min(next_month - timedelta(days=1), end)
        ranges.append((range_start, range_end))
        cursor = next_month
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
    """Month-by-month pull with adaptive halving. Used by Accused date-range only."""
    all_records, all_failed = [], []
    for range_start, range_end in month_ranges(start, end):
        logger.info("Fetching %s to %s from %s", range_start, range_end, url)
        records, failed = fetch_date_chunk_safe(method, url, base_params, range_start, range_end)
        all_records.extend(records)
        all_failed.extend(failed)
    return all_records, all_failed
