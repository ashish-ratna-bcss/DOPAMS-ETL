"""Person-name cleanup from the four cctns-v2 fix_fullname scripts, extended
per DOPAMS_ETL3_NAME_CLEANING_EDGE_CASE_AUDIT.md with: multi-alias chains
(@ and spelled-out "Alias"/"AKA"), spelled-out relationship phrases,
parenthetical role/nationality extraction, and invalid/unidentified-name
detection.

Applied in the same order as the live job:

1. fix_person_names.py — pull every @ or "Alias"-chained alias and an
   s/o, d/o, w/o (or spelled-out) relative out of the name when those
   fields are empty, then drop status/role/nationality/address noise.
2. fix_all_fullnames.py — clean full_name, or build it from given name,
   surname, and alias when full_name is empty.
3. fix_name_field.py — drop an @ alias from the given name.
4. fix_surname_field.py — drop an @ alias from the surname.

The source name is kept. Cleaned values are returned beside it. A value
that is not a name at all (an administrative placeholder, or nothing but
a role/relationship/nationality description) is never promoted to look
like one -- cleaned_full_name is null and name_identified is False.
"""
import re

from etl3.enrichment.address import NATIONALITY_TO_COUNTRY


def collapse_exact_repetition(text):
    """Collapse a name field that is the SAME word sequence repeated end to
    end -- e.g. "Amodi Chukuwudi Muomagolu Amodi Chukuwudi Muomagolu" or
    "China China" -- into one occurrence. A single data-entry artifact
    (the whole value typed/concatenated twice) is the only thing this
    removes.

    The test is exact and structural, not a resemblance heuristic: the
    token sequence must tile perfectly (comparison case-insensitive,
    tokens split on whitespace/comma/semicolon) with no leftover and no
    partial match. "Sri Sri Ravi Shankar" does NOT collapse (only "Sri"
    repeats, not the whole name); "Rama Rama Rao" does NOT collapse (the
    tail "Rao" breaks the tiling). Only a genuine whole-value duplicate --
    established by that exact tiling, never by "looks similar" -- is
    removed, so a legitimate repeated word or compound name is preserved
    exactly as given.

    Idempotent: a value with no such repetition (including an
    already-collapsed one) is returned unchanged.
    """
    if not text or not text.strip():
        return text
    tokens = [t for t in re.split(r"[\s,;]+", text.strip()) if t]
    n = len(tokens)
    if n < 2:
        return text
    normalized = [t.lower() for t in tokens]
    for period in range(1, n // 2 + 1):
        if n % period:
            continue
        if all(normalized[i] == normalized[i % period] for i in range(n)):
            return " ".join(tokens[:period])
    return text


def _dedupe_aliases(parts):
    """Every non-empty part, deduped case-insensitively, first-seen
    casing kept. Used by both the '@' and spelled-out 'Alias' chain
    extractors so a 3+ way chain never silently drops anything past the
    second entry (the confirmed bug in the single-alias-only version of
    extract_alias_from_name -- see audit finding G-2/G-3)."""
    aliases = []
    seen = set()
    for part in parts:
        alias = part.strip()
        if alias and alias.lower() not in seen:
            seen.add(alias.lower())
            aliases.append(alias)
    return aliases


def extract_alias_from_name(name):
    """Every alias chained after '@' in name -- not just the first.
    Returns (aliases_or_None, primary); primary is the text before the
    first '@'. (None, name) when there is no '@'."""
    if not name or "@" not in name:
        return None, name
    parts = name.split("@")
    primary = parts[0].strip()
    aliases = _dedupe_aliases(parts[1:])
    return (aliases or None), primary


_ALIAS_WORD_RE = re.compile(
    r"\b(?:alias|a\.?k\.?a\.?|also\s+known\s+as)\b\.?\s*", re.IGNORECASE,
)


def extract_named_aliases(name):
    """Every alias chained with the spelled-out word 'Alias' / 'AKA' /
    'A.K.A.' / 'Also Known As' -- e.g. "Ajay Alias Kittu Alias Bittu
    Kaleri" -> primary "Ajay", aliases ["Kittu", "Bittu Kaleri"]. Mirrors
    extract_alias_from_name's '@' handling exactly, for the delimiter the
    real dataset actually uses nearly 50x more often than '@' inside
    Full Name (127 vs 2 records, per the audit). (None, name) when the
    word never appears."""
    if not name or not _ALIAS_WORD_RE.search(name):
        return None, name
    parts = _ALIAS_WORD_RE.split(name)
    primary = parts[0].strip()
    aliases = _dedupe_aliases(parts[1:])
    return (aliases or None), primary


# Abbreviated and spelled-out relationship markers. Order only matters in
# that each is tried in turn and the first TYPE with any match wins (same
# simplification the original two-marker version already made) -- real
# records essentially never carry more than one relationship marker.
_RELATIONSHIP_MARKERS = (
    (r"s/o", "Father"),
    (r"d/o", "Father"),
    (r"w/o", "Husband"),
    (r"h/o", "Husband"),
    (r"c/o", "Care-of"),
    (r"son\s+of", "Father"),
    (r"daughter\s+of", "Father"),
    (r"wife\s+of", "Husband"),
    (r"care\s+of", "Care-of"),
    (r"mother\s+of", "Child"),
    (r"father\s+of", "Child"),
)

# Shared with clean_full_name's own independent strip (it may be called on
# text that never went through extract_relationship_info, e.g. the
# given/surname-built fallback) -- one source of truth for the marker list
# so the two never drift apart.
_RELATIONSHIP_STRIP_RE = re.compile(
    r"\b(?:" + "|".join(marker for marker, _ in _RELATIONSHIP_MARKERS) + r")\.?\s*[^,]*",
    re.IGNORECASE,
)


def extract_relationship_info(name):
    """Relation type + relative name pulled out of an abbreviated
    (s/o, d/o, w/o, h/o, c/o) or spelled-out (son of, daughter of, wife
    of, care of, mother of, father of) marker. A marker with nothing
    captured after it (a dangling "... S/o" at the end of the string) is
    still stripped from the returned name -- it is only rel_name that
    stays None in that case, never a reason to leave the marker in
    place."""
    if not name:
        return None, None, name
    for marker, relation_type in _RELATIONSHIP_MARKERS:
        pattern = re.compile(r"\b" + marker + r"\.?\s*([^,]*)", re.IGNORECASE)
        match = pattern.search(name)
        if match:
            relative = match.group(1).strip() or None
            cleaned = name[:match.start()] + name[match.end():]
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            return relation_type, relative, cleaned
    return None, None, name


# Occupation / accused-role phrases recognized ONLY when they are the
# entire content of a parenthetical span -- "(Supplier)", "(Main
# Peddler)". Deliberately excludes "absconding", "receiver", and "drug
# peddler": those three already have their own broader, pre-existing
# patterns in _strip_noise (handling bare-word and partial-phrase forms,
# not just a clean standalone parenthetical), so adding them here would
# be redundant, not wrong -- excluded to keep one source of truth per
# phrase rather than duplicate, possibly-drifting rules.
_ROLE_PHRASES = (
    "main peddler", "drug supplier", "medical shop owner", "lorry driver",
    "peddler", "supplier", "consumer", "customer", "dealer",
    "transporter", "financier", "kingpin", "agent",
)
_ROLE_PAREN_RE = re.compile(
    r"\(\s*(" + "|".join(re.escape(p) for p in _ROLE_PHRASES) + r")\s*\)",
    re.IGNORECASE,
)


def extract_role_hint(name):
    """A parenthetical occupation/accused-role phrase -- '(Supplier)',
    '(Main Peddler)' -- pulled out of the name. Only matches when the
    ENTIRE parenthetical content is one of the known role phrases, so a
    paren with other free text ("Native of Odisha - 9502892596") is never
    touched here, and a role word used as part of a sentence outside
    parentheses is left to the caller's own whole-value check instead of
    being guessed at here. Returns (role_or_None, name_with_role_removed).
    This is a display-cleanup signal only -- it never writes to, and never
    reads from, the AI-extracted accused_type/role_in_crime classification
    in etl3.enrichment.rules/accused_facts, which remains the sole
    authority for a crime's accused-role metadata."""
    if not name or "(" not in name:
        return None, name
    match = _ROLE_PAREN_RE.search(name)
    if not match:
        return None, name
    role = match.group(1).strip().title()
    remaining = name[:match.start()] + " " + name[match.end():]
    return role, remaining


# Reuses address.py's own nationality/demonym table so the words this
# strips from a NAME can never drift out of sync with the words
# address.resolve_name_country already uses to classify domicile. Never
# applied outside parentheses: a bare geographical word is frequently
# part of a genuine name in this dataset (confirmed real accused named
# "China", via NATIONALITY_TO_COUNTRY's own "china" entry; "Nepal
# Krishna"; "Khali Singh") and must never be guessed at without the
# explicit parenthetical signal.
_NATIONALITY_PAREN_WORDS = tuple(sorted(
    (k for k in NATIONALITY_TO_COUNTRY if k not in ("india", "indian")),
    key=len, reverse=True,
))
_NATIONALITY_PAREN_RE = re.compile(
    r"\(\s*(" + "|".join(re.escape(w) for w in _NATIONALITY_PAREN_WORDS) + r")"
    r"(?:\s+(?:lady|man|woman|national|nationalist|citizen))?\s*\)",
    re.IGNORECASE,
)


def extract_nationality_hint(name):
    """A parenthetical nationality/country mention -- '(Nigerian)',
    '(Russian Lady)' -- pulled out of the name for display purposes only.
    Domicile classification already derives this independently via
    address.resolve_name_country; this never changes that, never removes
    a bare (non-parenthesized) geographical word, and never feeds back
    into domicile logic -- it is purely a display-cleanup signal, kept
    deliberately one-directional."""
    if not name or "(" not in name:
        return None, name
    match = _NATIONALITY_PAREN_RE.search(name)
    if not match:
        return None, name
    nationality = match.group(1).strip().title()
    remaining = name[:match.start()] + " " + name[match.end():]
    return nationality, remaining


# Administrative placeholders and bare status words used standalone AS the
# name, with no actual identity content -- "Unknown", "N/A", a literal
# "Absconding" with nothing else. Matched only against the WHOLE value
# (after stripping one layer of surrounding brackets/quotes and a
# trailing period), never a substring, so "Absconding" embedded inside a
# real name phrase is untouched here (that is _strip_noise's job).
_INVALID_LITERALS = {
    "-", "--", "---", "n/a", "na", "n a", "unknown", "unknown person",
    "not known", "not available", "nil", "none", "null", ".",
    "no name", "noname", "not identified", "unidentified", "absconding",
}


def classify_invalid_literal(text):
    """"invalid_literal" when text, taken as a whole, is one of the fixed
    administrative-placeholder values above; None otherwise. Empty/blank
    input returns "empty" rather than None, so a caller can distinguish
    "nothing was ever given" from "something was given and it happens not
    to be a placeholder"."""
    if text is None:
        return "empty"
    stripped = re.sub(r"^[(\[{]+|[)\]}]+$", "", text.strip()).strip()
    if not stripped:
        return "empty"
    normalized = re.sub(r"\s+", " ", stripped).lower().rstrip(".")
    if normalized in _INVALID_LITERALS:
        return "invalid_literal"
    return None


def classify_noise_only(text):
    """Does `text` -- already past alias/relationship/role/nationality
    extraction -- consist ENTIRELY of recognized non-name content, so
    that returning it as a "cleaned" name would just be redisplaying
    noise? Checks the fixed invalid-literal list, then a whole-value
    match against a bare (unparenthesized) occupation/role phrase with
    nothing else alongside it -- e.g. the record's ENTIRE Full Name is
    "Medical Shop Owner" or "Absconding" with no name at all. Never a
    partial match: real content alongside the noise (e.g. "Anil
    (Supplier)", already reduced to "Anil" by the time this runs) is NOT
    noise-only, so this never fires for it. Returns a reason string, or
    None when real name content is present."""
    if not text or not text.strip():
        return "empty"
    literal = classify_invalid_literal(text)
    if literal:
        return literal
    bare = re.sub(r"^[(\[{]+|[)\]}]+$", "", text.strip()).strip().lower()
    if bare in _ROLE_PHRASES:
        return "role_only"
    return None


_REVIEW_INITIAL_ONLY_RE = re.compile(r"^[A-Za-z](\.[A-Za-z])*\.?$")


def detect_review_flags(cleaned_full_name):
    """Non-destructive signals for a value that was deliberately left
    unchanged (or changed only cosmetically) because no rule is confident
    enough to rewrite it, but a human reviewer may still want to look --
    an initial-only name ("A", "B.M"), or a trailing duplicated token
    ("A. Lokesh Lokesh", "Aaron Nile Joseph Joseph") that
    collapse_exact_repetition correctly leaves alone (it is not a whole-
    value tile, so collapsing it would be a guess, not a fact). Returns a
    list, never raises, never mutates the name."""
    flags = []
    if not cleaned_full_name:
        return flags
    stripped = cleaned_full_name.strip()
    if _REVIEW_INITIAL_ONLY_RE.match(stripped):
        flags.append("initial_only_name")
    tokens = [t.lower() for t in re.split(r"\s+", stripped) if t]
    if len(tokens) >= 2 and len(set(tokens)) < len(tokens):
        flags.append("duplicate_token")
    return flags


_SMART_PUNCT_MAP = {
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-",
}
_MOJIBAKE_STRAY_RE = re.compile("[Â�]")


def normalize_mojibake(name):
    """Cosmetic-only encoding cleanup: stray 'Â'/replacement-character
    sequences (classic UTF-8-read-as-Latin-1 mis-decoding) and smart
    quotes/en-/em-dashes normalized to plain ASCII punctuation. Runs
    before any identity-bearing extraction so an encoding artifact can
    never change an extraction decision; never touches a letter/digit."""
    if not name:
        return name
    for src, dst in _SMART_PUNCT_MAP.items():
        name = name.replace(src, dst)
    return _MOJIBAKE_STRAY_RE.sub("", name)


_ACCUSED_LABEL_RE = re.compile(
    r"^\s*accused(?:\s+person)?\s*:\s*", re.IGNORECASE,
)
# A bare list-number prefix with no "A" code letter -- "02.", "1)", "10.".
# Anchored to the very start of the string only: a number appearing later
# in the text is left completely alone (it is either multi_accused.py's
# concern, or genuinely part of other data, never guessed at here).
_BARE_NUMERIC_PREFIX_RE = re.compile(r"^\s*0*\d{1,2}\s*[.)]{1,2}\s*")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?91[-\s]?)?[6-9]\d{9}(?!\d)")


def _strip_noise(name):
    """Metadata removal shared by clean_name and clean_full_name."""
    name = _ACCUSED_LABEL_RE.sub("", name)
    name = _BARE_NUMERIC_PREFIX_RE.sub("", name)
    name = re.sub(r"\s*\(?\s*absconding\s*\)?\s*", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s*,?\s*r/o\.?\s*.*$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s*,?\s*N/o\.?\s*.*$", "", name, flags=re.IGNORECASE)
    # The labeled ("age:"/"age.") form must run BEFORE the bare-digit
    # form below -- otherwise the bare form's looser match (no "age"
    # prefix required) eats the digits+unit first and leaves a dangling,
    # unmatchable "Age." fragment behind (confirmed against "Aditya
    # Kumar , Age. 25 Years, ..." -- S.No. 1168).
    name = re.sub(r",?\s*age\.?\s*[:\s]+\d+\s*(?:yrs?|years?)\.?\s*", "", name, flags=re.IGNORECASE)
    name = re.sub(r",?\s*\d+\s*(?:yrs?|years?)\.?\s*", "", name, flags=re.IGNORECASE)
    name = re.sub(r",?\s*caste:\s*[^,]+", "", name, flags=re.IGNORECASE)
    name = re.sub(r",?\s*occ(?:upation)?\.?:\s*[^,]+", "", name, flags=re.IGNORECASE)
    name = re.sub(r",?\s*cell:\s*\d+", "", name, flags=re.IGNORECASE)
    name = re.sub(r",?\s*ph\.?\s*no\.?:\s*\d+", "", name, flags=re.IGNORECASE)
    # Confirmed gap (real records S.No. 48612, 50702, found via full-
    # dataset validation): the pattern above requires the literal word
    # "no" between "ph" and the colon, so the simpler, more common
    # "Ph: 9876543210" (no "No") label was left dangling once the bare
    # phone-number strip below removed only the digits.
    name = re.sub(r",?\s*ph\.?:\s*\d+", "", name, flags=re.IGNORECASE)
    name = re.sub(r",?\s*✆\s*\d+", "", name, flags=re.IGNORECASE)
    name = re.sub(r",?\s*\(?adhaar\.?\s*no\.?\s*[\d\s]+\)?", "", name, flags=re.IGNORECASE)
    # Leading accused-code reference on a SINGLE accused's own name only
    # ("A-1: Cheekati Srinivas", "A5:Somnath") -- now also matching a bare
    # trailing colon, which the original pattern's [)\.\s] class missed.
    # A code appearing mid-string is left alone: that is multi_accused.py's
    # multi-person-pack evidence, never this module's concern.
    name = re.sub(r"^A-?\d+[)\.:\s]+", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s+and\s+others\s*$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s*\([^)]*receiver[^)]*\)\s*", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s*\([^)]*drug\s+peddler[^)]*\)\s*", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s*owner\s+of\s+(bolero\s+)?vehicle.*$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s*driver\s+of.*$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s*under\s+trial\s+prisoner.*?,", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s*\(?\s*UT\s+prisoner\s+no\.?\s*\d+\s*\)?\s*", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s*CRPF.*$", "", name, flags=re.IGNORECASE)
    # A bare, unlabeled 10-digit Indian mobile number -- the labeled forms
    # above (cell:, ph.no.:, ✆) already consumed their own digits by this
    # point, so this only ever mops up an otherwise-unlabeled number
    # (e.g. "Saranga-9959075757", "Somnath# 9007155796").
    name = re.sub(r"(?<!\d)(?:\+?91[-\s]?)?[6-9]\d{9}(?!\d)", " ", name)
    name = normalize_mojibake(name)
    # Empty-parens cleanup runs BEFORE the whitespace collapse below --
    # confirmed idempotency failure otherwise (S.No. 42849, "Siddu (Ph:
    # 9346843200) (Abscondig)"): removing a labeled phone number from
    # inside its own parens leaves a bare "()" whose removal introduces a
    # double space that nothing afterward would then re-collapse.
    name = re.sub(r"\(\s*\)", "", name)
    name = re.sub(r"\s+", " ", name)
    # Trailing/leading junk cluster left dangling once a noise phrase next
    # to it is stripped (e.g. "ALLOY-Absconding" -> "ALLOY-", "Somnath# "
    # once its phone number is gone), or present in the source on its own
    # ("Mr. Chris -"). Only the outer edge of the string is touched -- an
    # internal hyphen (a hyphenated surname) is never affected.
    name = re.sub(r"^[,.\-\s#:✆]+|[,.\-\s#:✆]+$", "", name)
    return name.strip()


def clean_full_name(full_name):
    if not full_name:
        return full_name
    original = full_name
    if "@" in full_name:
        full_name = full_name.split("@")[0].strip()
    full_name = _RELATIONSHIP_STRIP_RE.sub("", full_name).strip()
    full_name = re.sub(r",?\s*H\.?\s*[Nn]o\.?\s*[\d\-\s]+", "", full_name, flags=re.IGNORECASE)
    full_name = re.sub(r",?\s*H/No\.?\s*[\d\-\s]+", "", full_name, flags=re.IGNORECASE)
    full_name = _strip_noise(full_name)
    if not full_name or len(full_name) < 2:
        return original
    return full_name


def construct_full_name(name, surname, alias):
    parts = []
    if name:
        parts.append(name.strip())
    if surname:
        parts.append(surname.strip())
    if alias:
        parts.append(f"@{alias.strip()}")
    return " ".join(parts) if parts else None


def extract_clean_name(name_with_alias):
    if not name_with_alias or "@" not in name_with_alias:
        return name_with_alias
    return name_with_alias.split("@")[0].strip()


def clean_surname(surname):
    if not surname:
        return surname
    if surname.strip().startswith("@"):
        return ""
    if "@" in surname:
        return surname.split("@")[0].strip()
    return surname


def _text(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _raw(value):
    """Keep the source characters, including trailing spaces. Blank stays empty."""
    if value is None:
        return None
    text = str(value)
    return text if text.strip() else None


def clean_person_names(full_name, given_name=None, surname=None, alias=None,
                       relative_name=None, relation_type=None):
    """Return source and cleaned name parts. Empty input stays empty."""
    original_full = _raw(full_name)
    given = _text(given_name)
    sur = _text(surname)
    ali = _text(alias)
    rel_name = _text(relative_name)
    rel_type = _text(relation_type)
    if not any((original_full, given, sur, ali)):
        return None

    # raw_full_name (returned below) stays exactly as the source gave it --
    # collapsing/normalization only ever feeds the derived
    # cleaned_full_name/alias/relationship extraction that follows.
    #
    # Deliberately NOT applied to given_name/surname: those fields are
    # typically 1-2 words, where an exact 2-word tiling match (k=1) is far
    # more likely to be a genuine short repeated-word name -- confirmed
    # against live data, where this would have erased "Dung Dung" (a real
    # Munda/Oraon surname) down to "Dung". The combined full_name field is
    # where 341 confirmed real duplicates were found with zero false
    # positives; given_name/surname have no comparable evidence, so they
    # are left exactly as the source gave them.
    working = collapse_exact_repetition(original_full) if original_full else ""
    working = normalize_mojibake(working) if working else working
    working = _ACCUSED_LABEL_RE.sub("", working) if working else working

    role_hint = None
    nationality_hint = None

    if "@" in working:
        extracted_aliases, without_alias = extract_alias_from_name(working)
        if extracted_aliases:
            # The chain is stripped out of `working` regardless of whether
            # `ali` is already populated from a separate alias column --
            # otherwise, when the primary name before "@" is empty
            # ("@Johnson Manumala @Goutham"), the untouched "@..." text
            # would flow all the way into clean_full_name, whose OWN
            # independent "@" split would leave nothing behind and fall
            # back to returning that same untouched "@..." original
            # (confirmed idempotency failure against real data, S.No.
            # 16547/28810, when a separate alias value already exists).
            # Only the ASSIGNMENT to `ali` -- never the strip -- is
            # conditional on it not already being set.
            if not ali:
                ali = "; ".join(extracted_aliases)
            # Re-check after the alias split: "Ravi Kumar Ravi Kumar @ Raju"
            # does not tile as a WHOLE string (the alias breaks it), but the
            # name alone, once the unambiguous "@" boundary has removed the
            # alias, does. A boundary this explicit is safe to re-check
            # against; an unmarked trailing word is not (see
            # extract_relationship_info below and collapse_exact_repetition's
            # own docstring on "Hassan Ali Hassan Ali Jer" staying untouched).
            working = collapse_exact_repetition(without_alias)
    elif working:
        extracted_aliases, without_alias = extract_named_aliases(working)
        if extracted_aliases:
            # Same "always strip, only conditionally assign" rule as the
            # "@" branch above (confirmed idempotency failure otherwise,
            # S.No. 22705/24446/34688: "Mannem Abhishek Alias Chandu" with
            # a separately-populated alias column left "Alias Chandu"
            # stuck in the name on a first pass, since clean_full_name has
            # no knowledge of the spelled-out marker at all).
            if not ali:
                ali = "; ".join(extracted_aliases)
            working = collapse_exact_repetition(without_alias)

    if working and (not rel_name or not rel_type):
        found_type, found_name, without_relation = extract_relationship_info(working)
        # Strip the marker whenever ANY relationship type is recognized --
        # even with nothing captured after it (a dangling "... S/o" at the
        # end of the string). Only found_name, never found_type alone, is
        # a precondition for filling rel_name: a marker with nothing after
        # it legitimately means the relative's name is simply missing from
        # the source, not that the marker should stay attached to the name.
        if found_type:
            working = collapse_exact_repetition(without_relation)
            if not rel_type:
                rel_type = found_type
            if not rel_name and found_name:
                rel_name = found_name

    if working:
        role_hint, working = extract_role_hint(working)
    if working:
        nationality_hint, working = extract_nationality_hint(working)

    cleaned_full = clean_full_name(working) if working else None

    given_clean = None
    surname_clean = None
    if not cleaned_full:
        given_clean = extract_clean_name(given) if given else None
        surname_clean = clean_surname(sur) if sur else None
        if given_clean or surname_clean:
            constructed = construct_full_name(given_clean, surname_clean, ali)
            cleaned_full = clean_full_name(constructed) if constructed else None
        # else: no given/surname, and full_name (after every extraction
        # stage above) left nothing real behind -- leave cleaned_full
        # None. Whatever WAS recognized (an alias, a relationship, a role,
        # a nationality) is already preserved in its own field; none of
        # it is a person's name, so none of it is promoted into one.

    unidentified_reason = None
    if cleaned_full:
        noise_reason = classify_noise_only(cleaned_full)
        if noise_reason:
            # clean_full_name's own safety net returned either the
            # untouched original (nothing confidently stripped) or a
            # fully-stripped-down leftover that still turns out, on this
            # whole-value check, to be nothing but a known placeholder or
            # bare role phrase -- e.g. "(Absconding)" or "Medical Shop
            # Owner" with no actual name anywhere in the record. Do not
            # display it as if it were a real name.
            cleaned_full = None
            unidentified_reason = noise_reason
    if not cleaned_full and unidentified_reason is None:
        if not working:
            if ali and not (given_clean or surname_clean):
                unidentified_reason = "alias_only"
            elif rel_type:
                unidentified_reason = "relationship_only"
            elif role_hint:
                unidentified_reason = "role_only"
            elif nationality_hint:
                unidentified_reason = "nationality_only"
            else:
                unidentified_reason = "empty"
        else:
            unidentified_reason = "unrecoverable"

    review_flags = detect_review_flags(cleaned_full)

    cleaned_given = extract_clean_name(given) if given else None
    if cleaned_given == "":
        cleaned_given = None
    cleaned_sur = clean_surname(sur) if sur is not None else None
    if cleaned_sur == "":
        cleaned_sur = None

    return {
        "raw_full_name": original_full,
        "cleaned_full_name": cleaned_full,
        "cleaned_given_name": cleaned_given,
        "surname": cleaned_sur,
        "cleaned_alias": ali,
        "relation_type": rel_type,
        "cleaned_relative_name": rel_name,
        "name_identified": cleaned_full is not None,
        "unidentified_reason": unidentified_reason,
        "extracted_role_hint": role_hint,
        "extracted_nationality_hint": nationality_hint,
        "review_flags": review_flags,
    }
