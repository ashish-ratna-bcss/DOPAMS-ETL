from config.settings import ACCUSED_DETAILS_API_URL, require
from apis.client import fetch_unfiltered


def fetch_accused_details():
    """Full pull, every field, no date range -- confirmed this endpoint
    returns everything in one GET (20,197 records, ~10s in the real capture)."""
    require("ACCUSED_DETAILS_API_URL")
    records, failed_windows = fetch_unfiltered("GET", ACCUSED_DETAILS_API_URL)
    return records, failed_windows
