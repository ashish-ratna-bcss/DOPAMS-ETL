from config.settings import FIR_API_URL, require
from apis.client import fetch_unfiltered


def fetch_fir():
    """Full pull, every field, no date range -- confirmed this endpoint
    returns everything in one GET (7,305 records, ~4s in the real capture)."""
    require("FIR_API_URL")
    records, failed_windows = fetch_unfiltered("GET", FIR_API_URL)
    return records, failed_windows
