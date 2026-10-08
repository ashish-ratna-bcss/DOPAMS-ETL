"""Drug name knowledge base backed by dopams_cctns_v2.kb.

Historical resolve_primary_drug_name tiers (brief_facts_ai):
  1. ignore/generic check on RAW input (kb.drug_ignore_list)
  2. exact lowercase / compact match on raw_name
  3. substring_kb_in_raw (KB key appears inside the raw text)
  4. substring_raw_in_kb (raw equals a whole token of a longer KB key)
  5. pg_trgm similarity >= 0.35 against is_verified = true rows only

category_group on drug_categories is KB metadata and is NOT the enrichment
drug_category label (Cannabis/Opioid/...). That map lives in rules.py.

Static etl3/enrichment/drug_mappings.json is a supplementary alias file.
Precedence (documented, deterministic):
  1. kb.drug_categories (verified rows) for exact/substring/trgm
  2. static aliases only when the raw key is absent from kb.drug_categories
  3. conflicts (same raw key, different standard) keep the KB value and are
     reported by compare_static_mappings()

Invariant: the KB normalizes source-supported names. It must not invent a
specific drug from a generic descriptor (e.g. drugs → Ganja Chocolates).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_KB_PATH = Path(__file__).resolve().parent / "drug_mappings.json"
SIMILARITY_THRESHOLD = 0.35
TIER_IGNORED_GENERIC = "ignored_generic"


def compact(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def _tokens(value: str) -> list[str]:
    """Alphanumeric tokens of a label, compacted."""
    return [compact(t) for t in re.split(r"[^a-z0-9]+", str(value or "").lower()) if compact(t)]


def load_static_mappings(path=None):
    data = json.loads(Path(path or _KB_PATH).read_text(encoding="utf-8"))
    return list(data.items())


class DrugKB:
    """In-memory exact/substring lookup plus optional SQL trigram fallback."""

    def __init__(self, lookup_items, ignore_terms=None, conn=None, static_extras=None):
        # preserve insertion order for substring first-match-wins
        self.items = list(lookup_items or [])
        self.lookup = {}
        for key, standard in self.items:
            k = str(key).lower().strip()
            if k and k not in self.lookup:
                self.lookup[k] = standard
        self.ignore = {
            str(t).lower().strip()
            for t in (ignore_terms or [])
            if t is not None and str(t).strip()
        }
        self.ignore_compact = {compact(t) for t in self.ignore if compact(t)}
        self.conn = conn
        self.static_extras = list(static_extras or [])

    def is_ignored(self, name) -> bool:
        """True when name (raw or primary) is on kb.drug_ignore_list."""
        if name is None:
            return False
        text = str(name).lower().strip()
        if not text:
            return False
        if text in self.ignore:
            return True
        c = compact(text)
        if c and c in self.ignore_compact:
            return True
        return False

    def fuzzy_match(self, raw_drug_name):
        if self.conn is None or not raw_drug_name or not str(raw_drug_name).strip():
            return None
        needle = str(raw_drug_name).lower().strip()
        if len(compact(needle)) < 3:
            return None
        with self.conn.cursor() as cur:
            # Use the trigram % operator so the GIN index can filter candidates
            # before similarity() ranks them (same threshold semantics).
            cur.execute(
                """
                SELECT standard_name, similarity(raw_name, %s) AS sim
                FROM kb.drug_categories
                WHERE COALESCE(is_verified, true) = true
                  AND raw_name %% %s
                  AND similarity(raw_name, %s) >= %s
                ORDER BY sim DESC
                LIMIT 1
                """,
                (needle, needle, needle, SIMILARITY_THRESHOLD),
            )
            row = cur.fetchone()
        return row[0] if row else None


def load_kb(path=None):
    """Legacy helper: static file only as (key, standard) list."""
    return load_static_mappings(path)


def load_drug_kb(conn, include_static_extras=True) -> DrugKB:
    """Load verified KB rows + ignore list from kb schema on the unified DB."""
    items = []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT raw_name, standard_name
            FROM kb.drug_categories
            WHERE COALESCE(is_verified, true) = true
            ORDER BY standard_name, raw_name
            """
        )
        items = [(r[0], r[1]) for r in cur.fetchall()]
        cur.execute("SELECT term FROM kb.drug_ignore_list ORDER BY id")
        ignore = [r[0] for r in cur.fetchall()]

    static_extras = []
    if include_static_extras:
        kb_keys = {str(k).lower().strip() for k, _ in items}
        for key, standard in load_static_mappings():
            k = str(key).lower().strip()
            if not k:
                continue
            if k in kb_keys:
                # conflict or duplicate: KB wins; extras skipped
                continue
            static_extras.append((key, standard))
            items.append((key, standard))

    return DrugKB(items, ignore_terms=ignore, conn=conn, static_extras=static_extras)


def compare_static_mappings(conn, path=None):
    """Return conflict/extra stats between static JSON and kb.drug_categories."""
    with conn.cursor() as cur:
        cur.execute("SELECT lower(trim(raw_name)), standard_name FROM kb.drug_categories")
        db = {r[0]: r[1] for r in cur.fetchall()}
    conflicts = []
    extras = []
    for key, standard in load_static_mappings(path):
        k = str(key).lower().strip()
        if k in db:
            if db[k] != standard:
                conflicts.append({"raw": key, "kb": db[k], "static": standard})
        else:
            extras.append({"raw": key, "static": standard})
    return {
        "kb_rows": len(db),
        "static_rows": len(load_static_mappings(path)),
        "conflicts": conflicts,
        "static_only_extras": extras,
        "precedence": "kb.drug_categories wins on conflict; static extras fill missing keys only",
    }


def resolve_primary_name(raw_name, kb_items, conn=None, drug_kb: DrugKB | None = None):
    """Return (standard_name or None, tier).

    tier: ignored_generic | exact | substring_kb_in_raw | substring_raw_in_kb
          | pgtrgm | none

    Precedence:
      1. authoritative ignore/generic check on RAW input (when DrugKB present)
      2. exact match
      3. safe substring (KB key in raw text; raw as whole token of KB key)
      4. trigram
      5. unresolved
    """
    if drug_kb is not None:
        kb_items = drug_kb.items
        conn = drug_kb.conn if conn is None else conn
        fuzzy = drug_kb.fuzzy_match
    else:
        fuzzy = None

    if not raw_name:
        return None, "none"

    raw = str(raw_name).lower().strip()
    if not raw or raw == "unknown":
        return None, "none"
    raw_compact = compact(raw)

    # Generic/ignored RAW terms must never resolve to a specific drug.
    if drug_kb is not None and drug_kb.is_ignored(raw):
        return None, TIER_IGNORED_GENERIC

    if not kb_items:
        if drug_kb is not None:
            hit = drug_kb.fuzzy_match(raw_name)
            if hit:
                return hit, "pgtrgm"
        return None, "none"

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

    # Safe substring_raw_in_kb: raw must equal a whole token of a multi-token
    # KB key. Character-level "drugs" ∈ "drugsmixedchocolates" is rejected.
    if len(raw_compact) >= 4:
        for key, standard in kb_items:
            tokens = _tokens(key)
            if len(tokens) > 1 and raw_compact in tokens:
                return standard, "substring_raw_in_kb"

    if drug_kb is not None:
        hit = fuzzy(raw_name)
        if hit:
            return hit, "pgtrgm"
    elif conn is not None:
        tmp = DrugKB([], conn=conn)
        hit = tmp.fuzzy_match(raw_name)
        if hit:
            return hit, "pgtrgm"
    return None, "none"
