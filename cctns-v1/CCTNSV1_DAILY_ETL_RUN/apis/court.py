from config.settings import COURT_API_URL, require
from apis.client import fetch_unfiltered


def fetch_court():
    """Full pull, every field, no date range -- confirmed this endpoint
    returns everything in one GET (7,660 records, ~2s in the real capture).

    Note: the source API itself has been observed returning exact-duplicate
    records in its response (134 confirmed in the real capture) -- this is
    not a bug in this code, it's the source. The load step's upsert makes
    this harmless (duplicate input rows just no-op on the second write)."""
    require("COURT_API_URL")
    records, failed_windows = fetch_unfiltered("GET", COURT_API_URL)
    return records, failed_windows
