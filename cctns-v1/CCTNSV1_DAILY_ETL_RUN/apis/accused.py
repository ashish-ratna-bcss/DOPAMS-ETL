from datetime import date, datetime

from config.settings import ACCUSED_API_URL, ACCUSED_FULL_PULL_START_DATE, require
from apis.client import fetch_full_range_chunked


def fetch_accused():
    """The one endpoint that needs date-chunking: POST, and its Oracle
    backend throws ORA-06502 on wide date ranges (confirmed from real
    captured runs with failedWindows in cctnsv1/response/accused_list_yearly_range/).

    Pulls month by month across the full configured history, every night
    (no incremental/"since last run" filter -- this API doesn't reliably
    support one). failed_windows lists any date ranges that never succeeded,
    even after adaptive halving, so nothing silently disappears."""
    require("ACCUSED_API_URL")
    start = datetime.strptime(ACCUSED_FULL_PULL_START_DATE, "%d-%m-%Y").date()
    end = date.today()
    records, failed_windows = fetch_full_range_chunked("POST", ACCUSED_API_URL, {}, start, end)
    return records, failed_windows
