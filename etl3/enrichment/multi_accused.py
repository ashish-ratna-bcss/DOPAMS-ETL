"""Conservative, high-precision detection of multiple accused packed into one
CCTNS name field (e.g. ``1) Abhishek Kumar, 2) Golla Srinivasulu``).

Detection only -- this module never touches the database and never decides
identity. A "high" confidence result means the numbering markers themselves
are unambiguous (strictly sequential, no repeats, every segment has
plausible name content); everything else -- unnumbered comma/"and" lists,
skipped/duplicated/out-of-order numbers, an implausible segment -- stays
"low" or "none" so the caller leaves the existing single record alone.

"and N others" / "and others" is recorded as other_count metadata on the
single record it was found on. It never creates a person or a segment --
CCTNS gives no name for an unnamed co-accused, so none is invented.

Known, accepted limitation: accused-code style markers (``A-1``, ``A.1``,
``A1)``) are deliberately NOT treated as split evidence in this version --
they read too much like a single accused's own code reference (and the
existing single-person cleaner in names.py already strips a leading one).
Supporting them as multi-person evidence is a possible future extension,
not required by the current conservative scope.
"""
import re

_OTHERS_NUMBERED_RE = re.compile(r"\s*,?\s*\band\s+(\d+)\s+others?\b\.?", re.IGNORECASE)
_OTHERS_UNKNOWN_RE = re.compile(r"\s*,?\s*\band\s+others\b\.?", re.IGNORECASE)
_SHARED_TRAILER_RE = re.compile(
    r"\s*,?\s*\b(?:both|all)\b\s+r/o\.?\s+.+$", re.IGNORECASE,
)
# The trailer kept on shared_trailer_text is "both/all R/o <place>" for
# audit/display; callers that want to feed it to the address resolver need
# just the place text, which trailer_address_text() strips down to.
_SHARED_TRAILER_PREFIX_RE = re.compile(r"^\s*\b(?:both|all)\b\s+r/o\.?\s+", re.IGNORECASE)
# A segment-leading marker: "1)" "01." "2." etc. Deliberately excludes
# "A-1" / "A.1" / "A1)" style accused-code markers and bare initials ("A.")
# -- both are real single-person patterns this module must not mistake for
# a second person (see tests for "A. Bhujangarao", "A1) Ravi, A2) Suresh").
_MARKER_RE = re.compile(r"(?:^|[,;\n]\s*)\s*0*([1-9]\d?)\s*[.)]\s+(?=\S)")

_NAME_LIKE_RE = re.compile(r"^[A-Za-z][A-Za-z.\s]{1,60}$")

_MAX_SEGMENT_LEN = 200


def _extract_others(text):
    """Strip a trailing "and N others" / "and others" phrase.

    Never turns it into a person. Returns (remaining_text, other_count,
    other_count_known).
    """
    if not text:
        return text, None, False
    match = _OTHERS_NUMBERED_RE.search(text)
    if match:
        count = int(match.group(1))
        remaining = (text[: match.start()] + text[match.end():]).strip()
        return remaining, count, True
    match = _OTHERS_UNKNOWN_RE.search(text)
    if match:
        remaining = (text[: match.start()] + text[match.end():]).strip()
        return remaining, None, True
    return text, None, False


def _split_shared_trailer(last_segment_text):
    """Pull a shared "both/all R/o ..." tail off the last segment only."""
    match = _SHARED_TRAILER_RE.search(last_segment_text)
    if not match:
        return last_segment_text, None
    remainder = last_segment_text[: match.start()].strip()
    trailer = last_segment_text[match.start():].lstrip(", ").strip()
    return remainder, (trailer or None)


def trailer_address_text(shared_trailer_text):
    """"both R/o Hyderabad" -> "Hyderabad" -- the bare place text a caller
    can feed straight to address.resolve_person_address as present/permanent
    address text. None when there is no shared trailer."""
    if not shared_trailer_text:
        return None
    return _SHARED_TRAILER_PREFIX_RE.sub("", shared_trailer_text).strip() or None


def _segment_is_plausible(raw_text):
    text = (raw_text or "").strip()
    if not text or len(text) > _MAX_SEGMENT_LEN:
        return False
    return bool(re.search(r"[A-Za-z]{2,}", text))


def _unnumbered_candidates(text):
    """Heuristic only, for review visibility -- never auto-split.

    Looks for 2+ comma/'and'/'&' separated chunks that each look like a
    bare name (letters/spaces/dots only). Deliberately crude: a false
    negative here is safe (a real list just gets no candidate flag); a
    false positive is never fed to any writer, only surfaced for review.
    """
    parts = re.split(r",|\band\b|&", text, flags=re.IGNORECASE)
    chunks = [p.strip() for p in parts if p.strip()]
    if len(chunks) < 2:
        return None
    if not all(_NAME_LIKE_RE.match(chunk) for chunk in chunks):
        return None
    return chunks


def detect_multi_accused(raw_name):
    """Return a dict describing whether ``raw_name`` packs multiple accused.

    confidence:
      "high" -- segments are safe to persist as separate accused occurrences.
      "low"  -- a pack-like or list-like shape was seen but evidence is
                ambiguous (skipped/duplicate/out-of-order numbers, an
                unnumbered comma/"and" list, or an implausible segment).
                Callers must not split; route for manual review.
      "none" -- a single name, nothing to split.
    """
    result = {
        "original_text": raw_name,
        "confidence": "none",
        "method": "single",
        "segments": [],
        "candidate_segments": None,
        "shared_trailer_text": None,
        "shared_address_text": None,
        "other_count": None,
        "other_count_known": False,
        "warnings": [],
    }
    if not raw_name or not str(raw_name).strip():
        return result

    core_text, other_count, other_known = _extract_others(str(raw_name))
    result["other_count"] = other_count
    result["other_count_known"] = other_known

    markers = list(_MARKER_RE.finditer(core_text))
    if len(markers) < 2:
        candidates = _unnumbered_candidates(core_text)
        if candidates and len(candidates) >= 2:
            result["confidence"] = "low"
            result["method"] = "unnumbered_candidate"
            result["candidate_segments"] = candidates
            result["warnings"].append("unnumbered_list_not_auto_split")
        return result

    labels = [int(m.group(1)) for m in markers]
    seg_starts = [m.end() for m in markers]
    seg_stops = [markers[i + 1].start() for i in range(len(markers) - 1)] + [len(core_text)]
    raw_segments = [
        core_text[seg_starts[i]:seg_stops[i]].rstrip(" ,;\n")
        for i in range(len(markers))
    ]

    shared_trailer = None
    if raw_segments:
        raw_segments[-1], shared_trailer = _split_shared_trailer(raw_segments[-1])

    sequential = all(labels[i + 1] == labels[i] + 1 for i in range(len(labels) - 1))
    if not sequential:
        result["confidence"] = "low"
        result["method"] = "numbered_pack_ambiguous"
        result["warnings"].append("non_sequential_marker_numbers")
        return result

    if not all(_segment_is_plausible(text) for text in raw_segments):
        result["confidence"] = "low"
        result["method"] = "numbered_pack_ambiguous"
        result["warnings"].append("implausible_segment")
        return result

    result["confidence"] = "high"
    result["method"] = "numbered_pack_v1"
    result["shared_trailer_text"] = shared_trailer
    result["shared_address_text"] = trailer_address_text(shared_trailer)
    result["segments"] = [
        {"segment_index": idx + 1, "label_number": label, "raw_text": text.strip()}
        for idx, (label, text) in enumerate(zip(labels, raw_segments))
    ]
    return result
