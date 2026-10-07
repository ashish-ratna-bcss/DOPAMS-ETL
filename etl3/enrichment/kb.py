"""Drug name knowledge base.

The live cctns-v2 database has no drug_categories table. dev-2 is not a
runtime dependency. The static alias file shipped with the old drug
standardization job is the reference data ETL-3 loads. Lookup order matches
resolve_primary_drug_name for the in-memory tiers: exact, then KB-key inside
the raw string, then raw inside a KB key when the raw string is at least 4
characters. The first key in file order wins. There is no fuzzy tier, because
pg_trgm against a missing table would be a guess.
"""
import json
import re
from pathlib import Path

_KB_PATH = Path(__file__).resolve().parent / "drug_mappings.json"


def load_kb(path=None):
    data = json.loads(Path(path or _KB_PATH).read_text(encoding="utf-8"))
    # File order is preserved by json.load on CPython 3.7+.
    return list(data.items())


def compact(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def resolve_primary_name(raw_name, kb_items):
    """Return (standard_name or None, tier).

    tier is exact, substring_kb_in_raw, substring_raw_in_kb, or none.
    A None standard_name means the caller must keep the raw name and must
    not invent a canonical label.
    """
    if not raw_name or not kb_items:
        return None, "none"
    raw = str(raw_name).lower().strip()
    if not raw or raw == "unknown":
        return None, "none"
    raw_compact = compact(raw)

    for key, standard in kb_items:
        if raw == str(key).lower().strip() or (raw_compact and raw_compact == compact(key)):
            return standard, "exact"

    for key, standard in kb_items:
        key_l = str(key).lower().strip()
        key_c = compact(key)
        if key_l and key_l in raw:
            return standard, "substring_kb_in_raw"
        if key_c and raw_compact and key_c in raw_compact and key_c != raw_compact:
            return standard, "substring_kb_in_raw"

    if len(raw_compact) >= 4:
        for key, standard in kb_items:
            key_c = compact(key)
            if raw_compact and raw_compact in key_c and raw_compact != key_c:
                return standard, "substring_raw_in_kb"
    return None, "none"
