"""Read-only analysis of the supplied person_enrichment CSV export.

For every row: compares raw_full_name, the CURRENTLY-STORED cleaned_full_name
(as exported from the real database), and what clean_person_names() produces
right now when run fresh against raw_full_name. Classifies each row so the
scope of the duplicate-name bug (and any other normalization gap) can be
established before any fix is written.

Writes no data anywhere. Run: python etl3/diagnostics/_analyze_name_csv.py
"""
import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.enrichment.names import clean_person_names

CSV_PATH = r"C:\Users\DELL\Downloads\data-1792049401351.csv"


def exact_repetition_ground_truth(text):
    """Independent, from-scratch "is this an exact repeated full name"
    check -- deliberately NOT sharing code with the fix under test, so it
    serves as a trustworthy ground truth for how many rows truly need it."""
    if not text:
        return None
    tokens = [t for t in re.split(r"[\s,;]+", text.strip()) if t]
    n = len(tokens)
    if n < 2:
        return None
    norm = [t.lower() for t in tokens]
    for k in range(1, n // 2 + 1):
        if n % k:
            continue
        if all(norm[i] == norm[i % k] for i in range(n)):
            return " ".join(tokens[:k])
    return None


def main():
    with open(CSV_PATH, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    print(f"total rows examined: {len(rows)}")

    categories = {
        "exact_duplicate_in_raw": [],      # raw_full_name itself is an exact repeat
        "stored_cleaned_still_duplicated": [],  # AND the stored cleaned_full_name failed to collapse it
        "current_code_still_duplicated": [],    # AND re-running clean_person_names TODAY still fails to collapse it
        "already_correct_no_duplicate": [],
        "stored_differs_from_fresh_rerun": [],  # stored cleaned_full_name != what the current function produces today (any reason)
        "null_or_empty_raw": [],
    }

    for row in rows:
        raw = row.get("raw_full_name")
        stored_cleaned = row.get("cleaned_full_name")
        pid = row.get("person_id")

        if raw is None or raw.strip() == "" or raw.strip().upper() == "NULL":
            categories["null_or_empty_raw"].append((pid, raw))
            continue

        ground_truth_collapsed = exact_repetition_ground_truth(raw)
        fresh = clean_person_names(raw)
        fresh_cleaned = fresh["cleaned_full_name"] if fresh else None

        if ground_truth_collapsed is not None:
            categories["exact_duplicate_in_raw"].append((pid, raw, stored_cleaned, fresh_cleaned, ground_truth_collapsed))
            if stored_cleaned and stored_cleaned.strip().lower() == raw.strip().lower():
                categories["stored_cleaned_still_duplicated"].append((pid, raw, stored_cleaned))
            if fresh_cleaned and fresh_cleaned.strip().lower() == raw.strip().lower():
                categories["current_code_still_duplicated"].append((pid, raw, fresh_cleaned))
        else:
            categories["already_correct_no_duplicate"].append(pid)

        if stored_cleaned != fresh_cleaned and not (stored_cleaned in (None, "") and fresh_cleaned in (None, "")):
            categories["stored_differs_from_fresh_rerun"].append((pid, raw, stored_cleaned, fresh_cleaned))

    print(f"\nrows with an EXACT whole-name repetition in raw_full_name: {len(categories['exact_duplicate_in_raw'])}")
    for pid, raw, stored, fresh, expected in categories["exact_duplicate_in_raw"]:
        print(f"  person_id={pid}")
        print(f"    raw_full_name      = {raw!r}")
        print(f"    stored cleaned     = {stored!r}")
        print(f"    fresh (today) clean= {fresh!r}")
        print(f"    expected collapse  = {expected!r}")

    print(f"\nrows where the STORED cleaned_full_name still contains the duplicate: {len(categories['stored_cleaned_still_duplicated'])}")
    print(f"rows where CURRENT (pre-fix) code still reproduces the duplicate: {len(categories['current_code_still_duplicated'])}")
    print(f"rows with no exact duplicate (other legitimate repeated-word names etc.): {len(categories['already_correct_no_duplicate'])}")
    print(f"rows with null/empty raw_full_name: {len(categories['null_or_empty_raw'])}")
    print(f"rows where stored cleaned_full_name != a fresh rerun today (any cause, informational): {len(categories['stored_differs_from_fresh_rerun'])}")
    for pid, raw, stored, fresh in categories["stored_differs_from_fresh_rerun"][:15]:
        print(f"  person_id={pid} raw={raw!r} stored={stored!r} fresh_today={fresh!r}")


if __name__ == "__main__":
    main()
