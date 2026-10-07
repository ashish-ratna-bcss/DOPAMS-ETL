"""Person-name cleanup from the four cctns-v2 fix_fullname scripts.

Applied in the same order as the live job:

1. fix_person_names.py — pull an @ alias and an s/o, d/o, or w/o relative
   out of the name when those fields are empty, then drop status and
   address noise.
2. fix_all_fullnames.py — clean full_name, or build it from given name,
   surname, and alias when full_name is empty.
3. fix_name_field.py — drop an @ alias from the given name.
4. fix_surname_field.py — drop an @ alias from the surname.

The source name is kept. Cleaned values are returned beside it.
"""
import re


def extract_alias_from_name(name):
    if not name or "@" not in name:
        return None, name
    parts = name.split("@")
    if len(parts) >= 2:
        primary = parts[0].strip()
        alias = re.sub(r"@+", "", parts[1]).strip()
        return (alias or None), primary
    return None, name


def extract_relationship_info(name):
    if not name:
        return None, None, name
    patterns = (
        (r"\bs/o\.?\s+([^,]+)", "Father"),
        (r"\bd/o\.?\s+([^,]+)", "Father"),
        (r"\bw/o\.?\s+([^,]+)", "Husband"),
    )
    for pattern, relation_type in patterns:
        match = re.search(pattern, name, re.IGNORECASE)
        if match:
            relative = match.group(1).strip()
            cleaned = re.sub(pattern, "", name, flags=re.IGNORECASE).strip()
            return relation_type, relative or None, cleaned
    return None, None, name


def _strip_noise(name):
    """Metadata removal shared by clean_name and clean_full_name."""
    name = re.sub(r"\s*\(?\s*absconding\s*\)?\s*", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s*,?\s*r/o\s+.*$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s*,?\s*N/o\s+.*$", "", name, flags=re.IGNORECASE)
    name = re.sub(r",?\s*\d+\s*yrs?\.?\s*", "", name, flags=re.IGNORECASE)
    name = re.sub(r",?\s*age\.?\s*[:\s]+\d+\s*yrs?\.?\s*", "", name, flags=re.IGNORECASE)
    name = re.sub(r",?\s*caste:\s*[^,]+", "", name, flags=re.IGNORECASE)
    name = re.sub(r",?\s*cell:\s*\d+", "", name, flags=re.IGNORECASE)
    name = re.sub(r",?\s*ph\.?\s*no\.?:\s*\d+", "", name, flags=re.IGNORECASE)
    name = re.sub(r",?\s*✆\s*\d+", "", name, flags=re.IGNORECASE)
    name = re.sub(r",?\s*\(?adhaar\.?\s*no\.?\s*[\d\s]+\)?", "", name, flags=re.IGNORECASE)
    name = re.sub(r"^A-?\d+[)\.\s]+", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s+and\s+others\s*$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s*\([^)]*receiver[^)]*\)\s*", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s*\([^)]*drug\s+peddler[^)]*\)\s*", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s*owner\s+of\s+(bolero\s+)?vehicle.*$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s*driver\s+of.*$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s*under\s+trial\s+prisoner.*?,", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s*\(?\s*UT\s+prisoner\s+no\.?\s*\d+\s*\)?\s*", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s*CRPF.*$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s+", " ", name)
    name = re.sub(r"^[,.\s]+|[,.\s]+$", "", name)
    name = re.sub(r"\(\s*\)", "", name)
    return name.strip()


def clean_full_name(full_name):
    if not full_name:
        return full_name
    original = full_name
    if "@" in full_name:
        full_name = full_name.split("@")[0].strip()
    full_name = re.sub(r"\bs/o\.?\s+[^,]+", "", full_name, flags=re.IGNORECASE).strip()
    full_name = re.sub(r"\bd/o\.?\s+[^,]+", "", full_name, flags=re.IGNORECASE).strip()
    full_name = re.sub(r"\bw/o\.?\s+[^,]+", "", full_name, flags=re.IGNORECASE).strip()
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

    working = original_full or ""
    if "@" in working and not ali:
        extracted_alias, without_alias = extract_alias_from_name(working)
        if extracted_alias:
            ali = extracted_alias
            working = without_alias

    if working and (not rel_name or not rel_type):
        found_type, found_name, without_relation = extract_relationship_info(working)
        if found_type and found_name:
            if not rel_type:
                rel_type = found_type
            if not rel_name:
                rel_name = found_name
            working = without_relation

    cleaned_full = clean_full_name(working) if working else None
    if not cleaned_full:
        constructed = construct_full_name(extract_clean_name(given), clean_surname(sur) or None, ali)
        cleaned_full = clean_full_name(constructed) if constructed else None

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
    }
