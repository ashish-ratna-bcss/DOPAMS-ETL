"""Independent (non-AI) detection of drug mentions missing from persisted
drug_extractions.

Used for the historical missed-extraction audit/recovery. AI output
(parsed_response, raw_response) is never consulted here as evidence that a
drug exists or was already captured -- only the verified kb.drug_categories
vocabulary and whatever is actually persisted in drug_extractions. This
keeps the detection independently verifiable against the source text.
"""
import re

from etl3.enrichment.ai import _VOLUME_MENTION_RE, _WEIGHT_MENTION_RE
from etl3.enrichment.kb import compact

# Data-quality artifacts in kb.drug_categories that are not real scannable
# drug-name tokens (a quantity+unit string; an extremely generic
# chemical/fertiliser word with no drug-specific meaning on its own); and
# pure appearance/colour descriptors with no actual drug-name token --
# "white powder", "whitish color substance", etc. -- which real sampled
# FIRs show commonly describe a DIFFERENT, already-named substance (one
# sampled case: text says "a very white powder... its name is known to be
# an intoxicant called Alprazolam" -- Alprazolam was already extracted;
# "white powder" alone must never independently claim a missing Cocaine).
# "toddy"/"taddy" alone are excluded for a different reason: they commonly
# appear as the accused's legal occupation ("Toddy Business") with no
# adulteration seizure in the same FIR at all; the more specific adulterated/
# spurious/unknown-toddy aliases are kept.
EXCLUDED_RAW_NAMES = frozenset({
    "1ml", "urea", "toddy", "taddy", "toddy (adulterated/spurious)",
    "kallu packets", "gudumba", "eeetha kallu", "unknown thati kallu",
    "white colour liquid",
    "white colored drug", "white color powder", "white colour powder",
    "white powder", "white powder substance", "whitish color substance",
    "white colour maddi powder", "white colour paste", "white paste",
    "white item like white lime", "white colored crystals similar to citric acid",
    "white colour crystals", "white crystal", "white crystal substance",
    "yellow colour powder", "unknown white colour powder", "unknown white powder",
    "katta powder", "chedu powder", "yeast powder",
})

# A count-style seizure mention ("03 packets", "60 Tablets") that the
# weight/volume regex in ai.py does not cover.
_COUNT_MENTION_RE = re.compile(
    r"\b\d[\d,]*(?:\.\d+)?\s*(?:pills?|tablets?|capsules?|packets?|pockets?|"
    r"sachets?|covers?|bottles?|blots?|strips?|boxes?|plants?)\b",
    re.IGNORECASE,
)

# A missing-drug candidate is only confident when a seizure-quantity
# mention (weight, volume, or count) falls within this many characters of
# the specific drug-term occurrence being checked -- not merely somewhere
# in the same (often very long, multi-item) document. This is what tells
# apart a real second seizure from "Ganja" used as a generic/background/
# habit word while the actually-weighed item is a different substance
# elsewhere in the same FIR (observed real case: "...narcotic substance of
# Ganja in the form of Liquid i.e. Hashish Oil is in the possession..." --
# only Hashish Oil was ever weighed; "Ganja" here is a generic descriptor,
# not a second seizure) or a pure consumption/urine-test-only mention with
# no seizure at all anywhere nearby.
LOCAL_EVIDENCE_WINDOW = 200


def load_kb_vocab(conn):
    """(compiled word-boundary regex, raw_name(lower) -> standard_name) from
    verified kb.drug_categories, excluding EXCLUDED_RAW_NAMES. Longest-first
    alternation so a multi-word alias is not cut short by a shorter one."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT raw_name, standard_name FROM kb.drug_categories WHERE is_verified = true"
        )
        rows = cur.fetchall()
    terms = [(raw, std) for raw, std in rows if raw.lower().strip() not in EXCLUDED_RAW_NAMES]
    terms.sort(key=lambda t: len(t[0]), reverse=True)
    pattern = re.compile(
        r"\b(" + "|".join(re.escape(raw) for raw, _ in terms) + r")\b", re.IGNORECASE,
    )
    raw_to_std = {raw.lower(): std for raw, std in terms}
    return pattern, raw_to_std


def _quantity_mention_spans(text):
    spans = []
    for pat in (_WEIGHT_MENTION_RE, _VOLUME_MENTION_RE, _COUNT_MENTION_RE):
        spans.extend((m.start(), m.end()) for m in pat.finditer(text))
    return spans


def _has_local_seizure_evidence(text, term_pattern, quantity_spans):
    for m in term_pattern.finditer(text):
        for qs, qe in quantity_spans:
            if qs - LOCAL_EVIDENCE_WINDOW <= m.start() <= qe + LOCAL_EVIDENCE_WINDOW:
                return True
    return False


# "Methylenedioxymethamphetamine" (MDMA's own full chemical name) is
# routinely hyphen- or space-split in Brief Facts -- "Methylenedioxy-
# methamphetamine", "Methylene Di Oxy Methamphetamine", "Methylenedioxy-N-
# methamphetamine" -- which puts a word boundary in front of
# "methamphetamine" and makes it match on its own. This is never a second,
# independently seized Methamphetamine; it is MDMA's own name. Observed
# repeatedly (6+ crimes) during the Tier-2 manual reconciliation.
_MDMA_CHEMICAL_NAME_PREFIX_RE = re.compile(
    r"methylene[\s-]*d[ii]?[\s-]*oxy[\s-]*(?:n[\s-]*)?$", re.IGNORECASE,
)


def _is_mdma_chemical_name_split(text, match_start):
    before = text[max(0, match_start - 40):match_start]
    return bool(_MDMA_CHEMICAL_NAME_PREFIX_RE.search(before))


def find_missing_drug_mentions(text, existing_rows, pattern, raw_to_std):
    """Compare KB-recognized drug mentions in ``text`` against what is
    already persisted for this crime.

    ``existing_rows`` must be the crime's CURRENTLY PERSISTED
    drug_extractions rows only (any provenance) -- an iterable of
    ``(raw_drug_name, primary_drug_name)`` tuples. Never pass AI
    parsed_response/raw_response here as if it were independent evidence;
    doing so would defeat the point of an independent text-based check.

    Returns ``(confirmed_missing, review_missing, matched_raw_terms)``:
    two sorted lists of standard_name strings -- confirmed has a local
    seizure-quantity mention near the term, review does not (likely
    consumption-only, suspected-only, or a background/alias reference) --
    and the sorted list of matched raw terms actually found in the text.
    """
    hits = set()
    for m in pattern.finditer(text):
        term = m.group(1).lower()
        if term == "methamphetamine" and _is_mdma_chemical_name_split(text, m.start()):
            continue
        hits.add(term)
    if not hits:
        return [], [], []
    mentioned_std = {raw_to_std[h] for h in hits}

    existing_compacts = set()
    for raw_name, primary_name in existing_rows:
        if primary_name:
            existing_compacts.add(compact(primary_name))
        if raw_name:
            existing_compacts.add(compact(raw_name))

    missing_std = set()
    for std in mentioned_std:
        std_compact = compact(std)
        # Also compact every raw alias mapping to this std, so an existing
        # row stored under an alias spelling still counts as already covered.
        alias_compacts = {compact(raw) for raw, s in raw_to_std.items() if s == std}
        if std_compact in existing_compacts:
            continue
        if existing_compacts & alias_compacts:
            continue
        if any(std_compact in ec or ec in std_compact for ec in existing_compacts if len(ec) >= 4):
            continue
        missing_std.add(std)

    if not missing_std:
        return [], [], sorted(hits)

    quantity_spans = _quantity_mention_spans(text)
    confirmed, review = set(), set()
    for std in missing_std:
        terms_for_std = [h for h in hits if raw_to_std[h] == std]
        term_pattern = re.compile(
            r"\b(" + "|".join(re.escape(t) for t in terms_for_std) + r")\b", re.IGNORECASE,
        )
        if _has_local_seizure_evidence(text, term_pattern, quantity_spans):
            confirmed.add(std)
        else:
            review.add(std)
    return sorted(confirmed), sorted(review), sorted(hits)
