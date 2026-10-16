"""Address and geography knowledge-base lookup.

Same order as cctns-v2/etl-address/resolver: normalize the structured
person fields, then confirm them against geo_reference and geo_countries.
Exact canon comes first. A parent-bounded trigram match is used only when
the knowledge base actually contains that parent. An empty knowledge base
confirms nothing. This does not call the address language model and does
not write the source person row.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional

SIM_STATE = 0.80
SIM_DISTRICT = 0.75
SIM_MANDAL = 0.65
SIM_VILLAGE = 0.55
SIM_COUNTRY_STATE = 0.80
SIM_COUNTRY = 0.70

STATE_ABBREV = {
    "ap": "Andhra Pradesh", "ts": "Telangana", "tg": "Telangana",
    "tn": "Tamil Nadu", "ka": "Karnataka", "kl": "Kerala",
    "mh": "Maharashtra", "wb": "West Bengal", "up": "Uttar Pradesh",
    "mp": "Madhya Pradesh", "rj": "Rajasthan", "gj": "Gujarat",
    "br": "Bihar", "or": "Odisha", "od": "Odisha", "pb": "Punjab",
    "hr": "Haryana", "hp": "Himachal Pradesh", "jk": "Jammu and Kashmir",
    "uk": "Uttarakhand", "ut": "Uttarakhand", "ga": "Goa",
    "cg": "Chhattisgarh", "jh": "Jharkhand", "as": "Assam",
    "mn": "Manipur", "ml": "Meghalaya", "mz": "Mizoram", "nl": "Nagaland",
    "sk": "Sikkim", "tr": "Tripura", "ar": "Arunachal Pradesh",
    "dl": "Delhi", "py": "Puducherry", "ch": "Chandigarh",
    "an": "Andaman and Nicobar Islands",
    "dn": "Dadra and Nagar Haveli and Daman and Diu",
    "ld": "Lakshadweep",
}

COUNTRY_ALIASES = {
    "us": "United States", "usa": "United States", "u s": "United States",
    "u s a": "United States", "united states of america": "United States",
    "uk": "United Kingdom", "u k": "United Kingdom",
    "uae": "United Arab Emirates", "u a e": "United Arab Emirates",
}

CITY_NICKNAMES = {
    "hyd": "Hyderabad", "sec": "Secunderabad", "vij": "Vijayawada",
    "vizag": "Visakhapatnam", "vskp": "Visakhapatnam",
    "bglr": "Bengaluru", "blr": "Bengaluru", "bangalore": "Bengaluru",
    "mum": "Mumbai", "bom": "Mumbai", "del": "Delhi", "ncr": "Delhi",
    "rr": "Ranga Reddy", "rr dist": "Ranga Reddy", "rrdist": "Ranga Reddy",
    "madras": "Chennai",
}

# CCTNS district labels that do not match kb.geo_reference names 1:1.
DISTRICT_ALIASES = {
    "mumbai": "Mumbai Suburban",
    "mumbai city": "Mumbai Suburban",
    "bombay": "Mumbai Suburban",
    "bom": "Mumbai Suburban",
    "mum": "Mumbai Suburban",
}

# Nationality / demonym words often stuffed into the accused name field.
NATIONALITY_TO_COUNTRY = {
    "indian": "India",
    "india": "India",
    "nigerian": "Nigeria",
    "nigeria": "Nigeria",
    "nigerien": "Niger",
    "niger": "Niger",
    "ghanaian": "Ghana",
    "ghana": "Ghana",
    "kenyan": "Kenya",
    "kenya": "Kenya",
    "ugandan": "Uganda",
    "uganda": "Uganda",
    "tanzanian": "Tanzania",
    "tanzania": "Tanzania",
    "russian": "Russia",
    "russia": "Russia",
    "chinese": "China",
    "china": "China",
    "nepalese": "Nepal",
    "nepali": "Nepal",
    "nepal": "Nepal",
    "bangladeshi": "Bangladesh",
    "bangladesh": "Bangladesh",
    "pakistani": "Pakistan",
    "pakistan": "Pakistan",
    "afghan": "Afghanistan",
    "afghanistan": "Afghanistan",
    "dutch": "Netherlands",
    "netherland": "Netherlands",
    "netherlands": "Netherlands",
    "holland": "Netherlands",
    "american": "United States",
    "usa": "United States",
    "u s a": "United States",
    "british": "United Kingdom",
    "uk": "United Kingdom",
    "u k": "United Kingdom",
    "somali": "Somalia",
    "somalia": "Somalia",
    "ethiopian": "Ethiopia",
    "ethiopia": "Ethiopia",
    "cameroonian": "Cameroon",
    "cameroon": "Cameroon",
    "congolese": "Congo",
    "liberian": "Liberia",
    "liberia": "Liberia",
    "senegalese": "Senegal",
    "senegal": "Senegal",
    "malawian": "Malawi",
    "malawi": "Malawi",
    "zambian": "Zambia",
    "zambia": "Zambia",
    "zimbabwean": "Zimbabwe",
    "zimbabwe": "Zimbabwe",
    "sudanese": "Sudan",
    "sudan": "Sudan",
    "egyptian": "Egypt",
    "egypt": "Egypt",
    "iranian": "Iran",
    "iran": "Iran",
    "iraqi": "Iraq",
    "iraq": "Iraq",
    "sri lankan": "Sri Lanka",
    "srilankan": "Sri Lanka",
    "sri lanka": "Sri Lanka",
    "burmese": "Myanmar",
    "myanmar": "Myanmar",
    "thai": "Thailand",
    "thailand": "Thailand",
    "filipino": "Philippines",
    "philippines": "Philippines",
    "indonesian": "Indonesia",
    "indonesia": "Indonesia",
    "malaysian": "Malaysia",
    "malaysia": "Malaysia",
    "australian": "Australia",
    "australia": "Australia",
    "canadian": "Canada",
    "canada": "Canada",
    "brazilian": "Brazil",
    "brazil": "Brazil",
    "mexican": "Mexico",
    "mexico": "Mexico",
    "turkish": "Turkey",
    "turkey": "Turkey",
    "uzbek": "Uzbekistan",
    "uzbekistan": "Uzbekistan",
    "emirati": "United Arab Emirates",
    "uae": "United Arab Emirates",
    "u a e": "United Arab Emirates",
    "saudi": "Saudi Arabia",
    "saudi arabian": "Saudi Arabia",
}

# Parenthetical / trailing tokens that are not countries.
_NAME_COUNTRY_NOISE = {
    "absconding", "consumer", "peddler", "drug supplier", "drug peddler",
    "phone", "lady", "man", "unknown", "person", "source", "receiver",
    "europe", "africa", "asia",
}

LOCALITY_TO_DISTRICT_MANDAL = {
    "suraram": ("Telangana", "Medchal Malkajgiri", "Quthbullapur"),
    "suraram colony": ("Telangana", "Medchal Malkajgiri", "Quthbullapur"),
    "jagadgirigutta": ("Telangana", "Medchal Malkajgiri", "Quthbullapur"),
    "rgk": ("Telangana", "Medchal Malkajgiri", "Quthbullapur"),
    "bachupally": ("Telangana", "Medchal Malkajgiri", "Bachupally"),
    "medipally": ("Telangana", "Medchal Malkajgiri", "Medipally"),
    "perzadiguda": ("Telangana", "Medchal Malkajgiri", "Medipally"),
    "beeramguda": ("Telangana", "Sangareddy", "Ameenpur"),
    "ameenpur": ("Telangana", "Sangareddy", "Ameenpur"),
    "mamillagudem": ("Telangana", "Nalgonda", "Miryalaguda"),
    "achanpally": ("Telangana", "Nizamabad", "Bodhan"),
    "yedapally": ("Telangana", "Nizamabad", "Yedapally"),
    "bhainsa": ("Telangana", "Nirmal", "Bhainsa"),
    "rayanch enclave": ("Telangana", "Medchal Malkajgiri", "Medipally"),
}

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s\-&/.]", re.UNICODE)


def _trigrams(value: str) -> set:
    padded = f"  {value.lower()} "
    return {padded[i:i + 3] for i in range(len(padded) - 2)}


def trigram_similarity(left: str, right: str) -> float:
    """Jaccard similarity of character trigrams, the same measure pg_trgm uses."""
    if not left or not right:
        return 0.0
    a = _trigrams(left)
    b = _trigrams(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _best(token: str, choices: list, floor: float):
    best_name = None
    best_score = 0.0
    for name in choices:
        score = trigram_similarity(token, name)
        if score > best_score:
            best_name = name
            best_score = score
    if best_name is not None and best_score >= floor:
        return best_name
    return None


class GeoKB:
    """In-memory geo_reference and geo_countries. Empty until load_rows is called."""

    def __init__(self):
        self.state_canon = {}
        self.district_canon = {}
        self.mandal_canon = {}
        self.village_to_mandal = {}
        self.country_canon = {}
        self.state_to_country = {}
        self.districts_by_state = {}
        self.mandals_by_district = {}
        self.villages_by_district = {}

    @property
    def loaded(self) -> bool:
        return bool(self.state_canon or self.country_canon)

    def add_reference(self, state, district, mandal, village):
        if not state:
            return
        state = state.strip()
        self.state_canon.setdefault(state.lower(), state)
        if not district:
            return
        district = district.strip()
        key = (state.lower(), district.lower())
        self.district_canon.setdefault(key, district)
        self.districts_by_state.setdefault(state.lower(), set()).add(district)
        if mandal and mandal.strip():
            mandal = mandal.strip()
            self.mandal_canon.setdefault((state.lower(), district.lower(), mandal.lower()), mandal)
            self.mandals_by_district.setdefault(key, set()).add(mandal)
        if village and village.strip() and mandal and str(mandal).strip():
            village = village.strip()
            mandal = mandal.strip()
            self.village_to_mandal.setdefault((state.lower(), district.lower(), village.lower()), mandal)
            self.villages_by_district.setdefault(key, set()).add(village)

    def add_country(self, country, state=None):
        if not country:
            return
        country = country.strip()
        self.country_canon.setdefault(country.lower(), country)
        if state and state.strip():
            self.state_to_country.setdefault(state.strip().lower(), country)

    def canon_state(self, name):
        if not name:
            return None
        return self.state_canon.get(name.lower())

    def canon_district(self, state, district):
        if not state or not district:
            return None
        return self.district_canon.get((state.lower(), district.lower()))

    def canon_mandal(self, state, district, mandal):
        if not state or not district or not mandal:
            return None
        return self.mandal_canon.get((state.lower(), district.lower(), mandal.lower()))

    def canon_village_mandal(self, state, district, village):
        if not state or not district or not village:
            return None
        return self.village_to_mandal.get((state.lower(), district.lower(), village.lower()))

    def canon_country(self, name):
        if not name:
            return None
        return self.country_canon.get(name.lower())

    def country_of_indian_state(self, state):
        if state and state.lower() in self.state_canon:
            return "India"
        return None

    def country_of_foreign_state(self, state):
        if not state:
            return None
        return self.state_to_country.get(state.lower())


def norm_token(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = unicodedata.normalize("NFKC", str(value))
    text = "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))
    text = _PUNCT.sub(" ", text)
    text = _WS.sub(" ", text).strip()
    return text or None


def _titlecase(text: str) -> str:
    parts = []
    for part in text.split():
        if part.isupper() and len(part) <= 3:
            parts.append(part)
        else:
            parts.append(part.capitalize())
    return " ".join(parts)


def expand_state(raw):
    token = norm_token(raw)
    if not token:
        return None
    key = token.lower().strip().rstrip(".")
    if key in STATE_ABBREV:
        return STATE_ABBREV[key]
    return _titlecase(token)


def expand_country(raw):
    token = norm_token(raw)
    if not token:
        return None
    key = token.lower().strip().rstrip(".")
    if key in COUNTRY_ALIASES:
        return COUNTRY_ALIASES[key]
    return _titlecase(token)


def expand_city(raw):
    token = norm_token(raw)
    if not token:
        return None
    key = token.lower().strip().rstrip(".")
    if key in CITY_NICKNAMES:
        return CITY_NICKNAMES[key]
    return _titlecase(token)


def expand_district(raw):
    """Normalize a district token, including CCTNS aliases for KB names."""
    token = norm_token(raw)
    if not token:
        return None
    key = token.lower().strip().rstrip(".")
    if key in DISTRICT_ALIASES:
        return DISTRICT_ALIASES[key]
    if key in CITY_NICKNAMES:
        nick = CITY_NICKNAMES[key]
        return DISTRICT_ALIASES.get(nick.lower(), nick)
    return _titlecase(token)


def _payload_text(payload, key):
    value = payload.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _candidate(payload, prefix, nationality):
    state = expand_state(_payload_text(payload, f"{prefix}_state_ut"))
    district = expand_district(_payload_text(payload, f"{prefix}_district"))
    mandal = expand_city(_payload_text(payload, f"{prefix}_area_mandal"))
    country = expand_country(_payload_text(payload, f"{prefix}_country"))
    locality = norm_token(_payload_text(payload, f"{prefix}_locality_village"))
    ward = norm_token(_payload_text(payload, f"{prefix}_ward_colony"))
    street = norm_token(_payload_text(payload, f"{prefix}_street_road_no"))
    landmark = norm_token(_payload_text(payload, f"{prefix}_landmark_milestone"))
    combined = " ".join(part for part in (
        _payload_text(payload, f"{prefix}_ward_colony"),
        _payload_text(payload, f"{prefix}_street_road_no"),
        _payload_text(payload, f"{prefix}_locality_village"),
        _payload_text(payload, f"{prefix}_landmark_milestone"),
    ) if part).lower()
    if combined and not (state and district and mandal and country):
        for phrase, (hit_state, hit_district, hit_mandal) in LOCALITY_TO_DISTRICT_MANDAL.items():
            if phrase in combined:
                state = state or hit_state
                district = district or hit_district
                mandal = mandal or hit_mandal
                break
    return {
        "state": state,
        "district": district,
        "mandal": mandal,
        "country": country,
        "locality": locality,
        "ward": ward,
        "street": street,
        "landmark": landmark,
        "nationality": nationality,
    }


def _fuzzy_state(kb: GeoKB, token):
    return _best(token, list(kb.state_canon.values()), SIM_STATE)


def _fuzzy_district(kb: GeoKB, state, token):
    # sorted(), not list(): districts_by_state values are sets, whose iteration
    # order depends on the process's string hash seed. A sorted order keeps
    # tie-breaking (the first candidate at the best score) stable across runs.
    choices = sorted(kb.districts_by_state.get(state.lower(), ()))
    return _best(token, choices, SIM_DISTRICT)


def _fuzzy_district_any(kb: GeoKB, token):
    best = None
    best_state = None
    best_score = 0.0
    for state_key, names in kb.districts_by_state.items():
        for name in sorted(names):
            score = trigram_similarity(token, name)
            if score > best_score:
                best = name
                best_state = kb.state_canon.get(state_key)
                best_score = score
    if best is not None and best_score >= SIM_DISTRICT:
        return best, best_state
    return None, None


def _fuzzy_mandal(kb: GeoKB, state, district, token):
    # sorted(), not list(): see _fuzzy_district -- mandals_by_district values
    # are sets too, so this keeps tied mandal matches deterministic.
    choices = sorted(kb.mandals_by_district.get((state.lower(), district.lower()), ()))
    return _best(token, choices, SIM_MANDAL)


def _fuzzy_village_mandal(kb: GeoKB, state, district, token):
    best_mandal = None
    best_score = 0.0
    # sorted(): villages_by_district values are sets; see _fuzzy_district.
    for village in sorted(kb.villages_by_district.get((state.lower(), district.lower()), ())):
        score = trigram_similarity(token, village)
        if score > best_score:
            best_mandal = kb.village_to_mandal.get((state.lower(), district.lower(), village.lower()))
            best_score = score
    if best_mandal is not None and best_score >= SIM_VILLAGE:
        return best_mandal
    return None


def _fuzzy_country(kb: GeoKB, token):
    return _best(token, list(kb.country_canon.values()), SIM_COUNTRY)


def _fuzzy_country_from_state(kb: GeoKB, token):
    best_country = None
    best_score = 0.0
    for state_key, country in kb.state_to_country.items():
        score = trigram_similarity(token, state_key)
        if score > best_score:
            best_country = country
            best_score = score
    if best_country is not None and best_score >= SIM_COUNTRY_STATE:
        return best_country
    return None


def resolve_candidate(kb: GeoKB, cand: dict) -> dict:
    """Confirm one address slot. Unconfirmed fields stay null."""
    if kb is None or not kb.loaded:
        return {"country": None, "state": None, "district": None, "mandal": None, "path": None}

    state = kb.canon_state(cand.get("state"))
    if state is None and cand.get("state"):
        state = _fuzzy_state(kb, cand["state"])

    district = kb.canon_district(state, cand.get("district")) if state else None
    if district is None and state and cand.get("district"):
        district = _fuzzy_district(kb, state, cand["district"])
    if district is None and cand.get("district") and not state:
        district, state_guess = _fuzzy_district_any(kb, cand["district"])
        if state_guess and state is None:
            state = state_guess

    mandal = None
    path = "kb"
    if state and district and cand.get("mandal"):
        mandal = kb.canon_mandal(state, district, cand["mandal"])
        if mandal is None:
            mandal = _fuzzy_mandal(kb, state, district, cand["mandal"])
    if mandal is None and state and district:
        for token in (cand.get("locality"), cand.get("landmark"), cand.get("ward"), cand.get("street")):
            if not token:
                continue
            village_mandal = kb.canon_village_mandal(state, district, token)
            if village_mandal is None:
                village_mandal = _fuzzy_village_mandal(kb, state, district, token)
            if village_mandal:
                mandal = village_mandal
                path = "kb+village"
                break
            mandal = _fuzzy_mandal(kb, state, district, token)
            if mandal:
                break

    country = kb.canon_country(cand.get("country"))
    if country is None:
        country = kb.country_of_indian_state(state)
    if country is None:
        country = kb.country_of_foreign_state(state)
    if country is None and (state or cand.get("state")):
        country = _fuzzy_country_from_state(kb, state or cand.get("state"))
    if country is None and cand.get("country"):
        country = _fuzzy_country(kb, cand["country"])
    if country is None and cand.get("nationality"):
        country = kb.canon_country(cand["nationality"]) or _fuzzy_country(kb, cand["nationality"])

    return {
        "country": country,
        "state": state,
        "district": district,
        "mandal": mandal,
        "path": path if any((country, state, district, mandal)) else None,
    }


def _match_district_token(kb: GeoKB, state, token):
    expanded = expand_district(token) or token
    hit = kb.canon_district(state, expanded) if state else None
    if hit is None and state and expanded:
        hit = _fuzzy_district(kb, state, expanded)
    return hit


def _name_country_candidates(name: str) -> list:
    """Pull country-like tokens from accused name patterns.

    Supports ``Name (Country)``, ``Name, Country``, ``Name - Country``, and
    ``Name of Country`` -- every pattern requires an explicit marker
    (parenthesis, comma, dash, or "of"). A name with none of these is never
    read as naming a nationality, however its words happen to look.
    """
    if not name or not str(name).strip():
        return []
    text = unicodedata.normalize("NFKC", str(name))
    candidates = []

    for match in re.finditer(r"\(([^)]*)\)", text):
        inner = match.group(1).strip()
        if not inner:
            continue
        for part in re.split(r"[,/|]+", inner):
            token = norm_token(part)
            if token:
                candidates.append(token)

    # Name, Country  /  Name, Country, more
    for match in re.finditer(r",\s*([^,()]+)", text):
        token = norm_token(match.group(1))
        if token and len(token.split()) <= 4:
            candidates.append(token)

    # Name - Country (end of string)
    dash = re.search(r"[-–—]\s*([A-Za-z][A-Za-z .']{0,40})\s*$", text)
    if dash:
        token = norm_token(dash.group(1))
        if token:
            candidates.append(token)

    # Name of Country
    of_match = re.search(r"\bof\s+([A-Za-z][A-Za-z .']{0,40})\s*$", text, re.IGNORECASE)
    if of_match:
        token = norm_token(of_match.group(1))
        if token:
            candidates.append(token)

    # Deliberately NO bare "last 1-3 words of the name" fallback: confirmed
    # against live data that it misreads common Indian surnames/given names
    # that happen to collide with a country already in the geo KB (e.g.
    # "Mali" the caste/surname vs. Mali the country -- 14 of 30 real
    # name_country rows; also "Israel", "Sudan" as given names). Every
    # remaining candidate here comes from an explicit marker -- parens,
    # comma, dash, or "of <X>" -- so a name is only read as naming a
    # nationality when something in the text actually marks it as one.

    # De-dupe, preserve order. Keep multi-word phrases so demonym+role
    # (e.g. ``Russian Lady``) can still resolve via the first word.
    seen = set()
    out = []
    for token in candidates:
        key = token.lower()
        if key in seen:
            continue
        if key in _NAME_COUNTRY_NOISE and len(key.split()) == 1:
            continue
        seen.add(key)
        out.append(token)
    return out


def resolve_name_country(name, kb: GeoKB | None = None):
    """Map a country/nationality embedded in the person name via aliases + KB.

    Returns canonical country name (e.g. ``Nigeria``, ``India``) or None.
    """
    for token in _name_country_candidates(name):
        key = token.lower().strip().rstrip(".")
        # Strip trailing role words: "Russian Lady" → try "Russian"
        words = key.split()
        trial_keys = [key]
        if len(words) >= 2:
            trial_keys.append(words[0])
            trial_keys.append(" ".join(words[:2]))

        for trial in trial_keys:
            if trial in _NAME_COUNTRY_NOISE:
                continue
            mapped = NATIONALITY_TO_COUNTRY.get(trial)
            if mapped == "India":
                return "India"
            if mapped:
                if kb is not None and kb.loaded:
                    return kb.canon_country(mapped) or mapped
                return mapped
            if kb is not None and kb.loaded:
                hit = kb.canon_country(trial) or kb.canon_country(expand_country(trial))
                if hit is None and trial:
                    hit = _fuzzy_country(kb, expand_country(trial) or trial)
                if hit:
                    return hit
            elif trial in ("india", "indian"):
                return "India"
    return None


def resolve_nationality_label(raw, kb: GeoKB | None = None):
    """Canonical country for a nationality field value (Indian → India, etc.)."""
    token = norm_token(raw)
    if not token:
        return None
    key = token.lower().strip().rstrip(".")
    if key in ("default", "others", "other", "unknown", "n/a", "na"):
        return None
    mapped = NATIONALITY_TO_COUNTRY.get(key)
    if mapped == "India":
        return "India"
    if mapped:
        if kb is not None and kb.loaded:
            return kb.canon_country(mapped) or mapped
        return mapped
    if kb is not None and kb.loaded:
        hit = kb.canon_country(key) or kb.canon_country(expand_country(token))
        if hit is None:
            hit = _fuzzy_country(kb, expand_country(token) or token)
        if hit:
            return hit
    if key in ("india", "indian"):
        return "India"
    return _titlecase(token)


def resolve_address_text(text, kb: GeoKB):
    """Confirm state/district/mandal from free-text such as ``Adilabad,Telangana``.

    Tokens are split on commas and slashes. The knowledge base must confirm
    each field; unmatched tokens are ignored. When a geo_reference state is
    confirmed, country is India. Remaining tokens may resolve mandal via
    village or mandal names in kb.geo_reference.
    """
    if kb is None or not kb.loaded or not text:
        return None
    tokens = []
    for part in re.split(r"[,/|]+", str(text)):
        token = norm_token(part)
        if token:
            tokens.append(token)
    if not tokens:
        return None

    state = None
    state_idx = None
    for i in range(len(tokens) - 1, -1, -1):
        expanded = expand_state(tokens[i])
        hit = kb.canon_state(expanded) if expanded else None
        if hit is None and expanded:
            hit = _fuzzy_state(kb, expanded)
        if hit is not None:
            state = hit
            state_idx = i
            break

    district = None
    district_idx = None
    if state is not None:
        for i, token in enumerate(tokens):
            if i == state_idx:
                continue
            hit = _match_district_token(kb, state, token)
            if hit is not None:
                district = hit
                district_idx = i
                break
    else:
        # No state token: try district anywhere and inherit its state.
        for i, token in enumerate(tokens):
            expanded = expand_district(token) or token
            hit, state_guess = _fuzzy_district_any(kb, expanded)
            if hit is None:
                for state_key, names in kb.districts_by_state.items():
                    for name in names:
                        if name.lower() == expanded.lower():
                            hit, state_guess = name, kb.state_canon.get(state_key)
                            break
                    if hit:
                        break
            if hit and state_guess:
                district, state = hit, state_guess
                district_idx = i
                break

    if state is None and district is None:
        return None

    mandal = None
    path = "kb+text"
    if state and district:
        for i, token in enumerate(tokens):
            if i in (state_idx, district_idx):
                continue
            village_mandal = kb.canon_village_mandal(state, district, token)
            if village_mandal is None:
                village_mandal = _fuzzy_village_mandal(kb, state, district, token)
            if village_mandal:
                mandal = village_mandal
                path = "kb+text+village"
                break
            hit = kb.canon_mandal(state, district, token)
            if hit is None:
                hit = _fuzzy_mandal(kb, state, district, token)
            if hit:
                mandal = hit
                break

    country = kb.country_of_indian_state(state)
    if country is None:
        country = kb.country_of_foreign_state(state)
    return {
        "country": country,
        "state": state,
        "district": district,
        "mandal": mandal,
        "path": path,
    }


def _merge_address_hits(kb: GeoKB, structured: dict, text_hit: dict | None):
    """Combine structured + free-text KB hits. Fill missing village/mandal/district/state.

    A nationality-only country match (no state/district/mandal) is not treated as
    a complete address; free-text geography is still applied. Confirmed Indian
    state always sets address country to India for mapping purposes.
    """
    out = {
        "country": structured.get("country"),
        "state": structured.get("state"),
        "district": structured.get("district"),
        "mandal": structured.get("mandal"),
        "path": structured.get("path"),
    }
    has_geo = any((out.get("state"), out.get("district"), out.get("mandal")))
    if out.get("path") and not has_geo:
        # Country-only structured hit (often nationality) — keep as weak country.
        weak_country = out.get("country")
        out = {
            "country": None, "state": None, "district": None,
            "mandal": None, "path": None,
        }
    else:
        weak_country = None

    if text_hit:
        for key in ("state", "district", "mandal"):
            if not out.get(key) and text_hit.get(key):
                out[key] = text_hit[key]
        if not out.get("path") and text_hit.get("path"):
            out["path"] = text_hit["path"]
        elif text_hit.get("path") and text_hit.get("mandal") and not structured.get("mandal"):
            out["path"] = text_hit["path"]
        if not out.get("country") and text_hit.get("country"):
            out["country"] = text_hit["country"]

    if out.get("state") and kb.country_of_indian_state(out["state"]):
        out["country"] = "India"
    elif not out.get("country") and weak_country:
        out["country"] = weak_country

    if not any((out.get("state"), out.get("district"), out.get("mandal"))):
        return {"country": None, "state": None, "district": None, "mandal": None, "path": None}
    if not out.get("path"):
        out["path"] = "kb"
    return out


def resolve_person_address(payload, kb: GeoKB):
    """Return {permanent, present} for slots the knowledge base confirmed.

    Structured person fields are confirmed first, then free-text
    ``*_address_text`` fills any missing state/district/mandal/village from
    kb.geo_reference. Foreign nationality does not block address mapping;
    domicile classification is decided separately.
    """
    if kb is None or not kb.loaded or not isinstance(payload, dict):
        return None
    nationality = norm_token(_payload_text(payload, "nationality"))
    resolved = {}
    for slot, prefix in (("permanent", "permanent"), ("present", "present")):
        structured = resolve_candidate(kb, _candidate(payload, prefix, nationality))
        text_hit = resolve_address_text(
            _payload_text(payload, f"{prefix}_address_text"), kb,
        )
        found = _merge_address_hits(kb, structured, text_hit)
        if found["path"]:
            resolved[slot] = found
    return resolved or None


def load_geo_kb(conn, schema="kb") -> GeoKB:
    """Read geo_reference and geo_countries from the local KB schema.

    Runtime uses dopams_cctns_v2.kb.* only. Missing or empty tables stay empty.
    """
    kb = GeoKB()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = %s AND table_name = 'geo_reference'
            """,
            (schema,),
        )
        if cur.fetchone():
            cur.execute(
                f"""
                SELECT DISTINCT
                    TRIM(state_name),
                    TRIM(district_name),
                    TRIM(COALESCE(sub_district_name, '')),
                    TRIM(COALESCE(village_name_english, ''))
                FROM {schema}.geo_reference
                WHERE state_name IS NOT NULL
                """
            )
            for state, district, mandal, village in cur.fetchall():
                kb.add_reference(state, district, mandal, village)
        cur.execute(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = %s AND table_name = 'geo_countries'
            """,
            (schema,),
        )
        if cur.fetchone():
            cur.execute(
                f"""
                SELECT DISTINCT TRIM(country_name), TRIM(COALESCE(state_name, ''))
                FROM {schema}.geo_countries
                WHERE country_name IS NOT NULL
                """
            )
            for country, state in cur.fetchall():
                kb.add_country(country, state or None)
    return kb
