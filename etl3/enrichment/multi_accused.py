"""Conservative, high-precision detection of multiple accused packed into one
CCTNS name field (e.g. ``1) Abhishek Kumar, 2) Golla Srinivasulu`` or
``A1. Person A, A2. Person B``).

Detection only -- this module never touches the database and never decides
identity. A "high" confidence result means the numbering markers themselves
are unambiguous (strictly sequential FROM 1, no repeats, no gap, every
segment has plausible name content, and every marker boundary is
unambiguous); everything else -- unnumbered comma/"and" lists, skipped/
duplicated/out-of-order/gapped numbers, an implausible segment, a marker
preceded only by bare whitespace rather than a comma/semicolon/"and" --
stays "low" or "none" so the caller leaves the existing single record
alone.

"and N others" / "and others" is recorded as other_count metadata on the
single record it was found on. It never creates a person or a segment --
CCTNS gives no name for an unnamed co-accused, so none is invented.

Accused-code markers (``A-1``, ``A1)``, ``A-1:``) ARE treated as split
evidence (extended per DOPAMS_ETL3_NAME_CLEANING_EDGE_CASE_AUDIT.md /
the follow-up implementation task), under the exact same guards already
proven safe for the plain ``N.``/``N)`` style: 2+ strictly sequential
markers starting at 1, every segment plausible, no weak boundary. A
single accused-code reference to itself ("A-1: Cheekati Srinivas") never
reaches this path at all, because it is only ever ONE marker and 2+ are
always required -- the previous version's "too risky" concern was really
about a single-marker false positive, which the existing 2+ guard already
rules out regardless of which marker style is used.
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

# A segment-leading marker: "1)" "01." "2." "1)." etc. Boundary before the
# marker is start-of-string, comma, semicolon, newline, "and ", OR bare
# whitespace following any non-space character -- the last alternative
# (named "weak" below) catches a numbered list with no punctuation
# separator at all ("1. Name 2. Name"), which previously went completely
# undetected. A weak-boundary marker is real, useful signal, but strictly
# less certain than a comma/semicolon/"and" boundary (it could, in theory,
# coincide with an ordinary sentence containing a number), so any result
# built from even one weak-boundary marker is capped at "low" confidence
# -- see _build_pack_result. Deliberately excludes bare initials ("A.")
# because the label group requires a DIGIT, never a letter.
_MARKER_RE = re.compile(
    r"(?P<boundary>^|[,;\n]\s*|\band\s+|(?<=\S)\s+)"
    r"\s*0*(?P<label>[1-9]\d?)\s*[.)]{1,2}\s*(?=\S)",
)
# Accused-code marker: "A1.", "A-1)", "A1:", "A-1.)" -- same boundary and
# "weak boundary" rules as _MARKER_RE, same 2+-required guard applied by
# the caller, same strictly-sequential-from-1 and plausibility checks via
# the shared _build_pack_result pipeline. A single reference to the
# accused's own code ("A-1: Cheekati Srinivas") never qualifies: it is
# always exactly one marker, and 2+ are always required before any of
# this module's checks even run.
_A_CODE_MARKER_RE = re.compile(
    r"(?P<boundary>^|[,;\n]\s*|\band\s+|(?<=\S)\s+)"
    r"\s*A-?0*(?P<label>[1-9]\d?)\s*[.):]{1,2}\s*(?=\S)",
    re.IGNORECASE,
)

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


def _is_weak_boundary(match):
    """True when this marker's boundary was bare whitespace only -- no
    comma, semicolon, newline, or "and" -- the weakest, least certain
    signal this module recognizes (see _MARKER_RE's own docstring note)."""
    boundary = match.group("boundary")
    return boundary != "" and boundary.strip() == ""


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


def _empty_result(raw_name):
    return {
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


def _build_pack_result(raw_name, core_text, markers, other_count, other_known, method_name):
    """Shared validation pipeline for a set of 2+ markers (whichever style
    found them -- plain ``N.``/``N)`` or accused-code ``A-N``). Every check
    here is a DOWNGRADE-only gate: a result starts eligible for "high" and
    is capped at "low" (never discarded, never auto-split) the moment any
    guard below is not satisfied. The guards, in order:

    1. Strictly sequential label numbers (no skip, no repeat, no reorder).
    2. Every segment has plausible name-shaped content.
    3. The FIRST marker is label 1 -- a sequence that starts at 2 or later
       (e.g. "Bishan Singh, A2. Karnati Sharath Kumar, A3. Badari Sai
       Reddy", S.No. 63) implies an unmarked leading accused ("Bishan
       Singh") that this function cannot safely fold into any segment on
       its own authority. Silently losing that person (by starting the
       split at A2) is worse than not splitting at all -- so this stays
       "low", with the found segments still surfaced as review candidates
       rather than being discarded.
    4. No marker relied on a "weak" (bare-whitespace-only) boundary -- see
       _MARKER_RE's docstring. A space-separated numbered list is real,
       useful signal for a reviewer, but not certain enough to auto-split
       purely on its own say-so.
    """
    labels = [int(m.group("label")) for m in markers]
    seg_starts = [m.end() for m in markers]
    seg_stops = [markers[i + 1].start() for i in range(len(markers) - 1)] + [len(core_text)]
    raw_segments = [
        core_text[seg_starts[i]:seg_stops[i]].rstrip(" ,;\n")
        for i in range(len(markers))
    ]

    shared_trailer = None
    if raw_segments:
        raw_segments[-1], shared_trailer = _split_shared_trailer(raw_segments[-1])

    result = _empty_result(raw_name)
    result["confidence"] = "low"
    result["method"] = "numbered_pack_ambiguous"
    result["other_count"] = other_count
    result["other_count_known"] = other_known

    sequential = all(labels[i + 1] == labels[i] + 1 for i in range(len(labels) - 1))
    if not sequential:
        result["warnings"].append("non_sequential_marker_numbers")
        return result

    if not all(_segment_is_plausible(text) for text in raw_segments):
        result["warnings"].append("implausible_segment")
        return result

    if labels[0] != 1:
        result["warnings"].append("missing_leading_marker")
        result["candidate_segments"] = [s.strip() for s in raw_segments]
        return result

    if any(_is_weak_boundary(m) for m in markers):
        result["warnings"].append("weak_space_boundary")
        result["candidate_segments"] = [s.strip() for s in raw_segments]
        return result

    result["confidence"] = "high"
    result["method"] = method_name
    result["shared_trailer_text"] = shared_trailer
    result["shared_address_text"] = trailer_address_text(shared_trailer)
    result["segments"] = [
        {"segment_index": idx + 1, "label_number": label, "raw_text": text.strip()}
        for idx, (label, text) in enumerate(zip(labels, raw_segments))
    ]
    return result


def detect_multi_accused(raw_name):
    """Return a dict describing whether ``raw_name`` packs multiple accused.

    confidence:
      "high" -- segments are safe to persist as separate accused occurrences.
      "low"  -- a pack-like or list-like shape was seen but evidence is
                ambiguous (skipped/duplicate/out-of-order/gapped numbers,
                an unnumbered comma/"and" list, an implausible segment, or
                a weak whitespace-only marker boundary). Callers must not
                split; route for manual review.
      "none" -- a single name, nothing to split.
    """
    if not raw_name or not str(raw_name).strip():
        return _empty_result(raw_name)

    core_text, other_count, other_known = _extract_others(str(raw_name))

    n_markers = list(_MARKER_RE.finditer(core_text))
    if len(n_markers) >= 2:
        return _build_pack_result(
            raw_name, core_text, n_markers, other_count, other_known, "numbered_pack_v1",
        )

    a_markers = list(_A_CODE_MARKER_RE.finditer(core_text))
    if len(a_markers) >= 2:
        return _build_pack_result(
            raw_name, core_text, a_markers, other_count, other_known, "a_code_pack_v1",
        )

    result = _empty_result(raw_name)
    result["other_count"] = other_count
    result["other_count_known"] = other_known
    candidates = _unnumbered_candidates(core_text)
    if candidates and len(candidates) >= 2:
        result["confidence"] = "low"
        result["method"] = "unnumbered_candidate"
        result["candidate_segments"] = candidates
        result["warnings"].append("unnumbered_list_not_auto_split")
    return result
