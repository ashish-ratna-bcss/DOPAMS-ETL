"""Assign V2 hierarchy codes onto a V1 crime.

Exact rule, applied first:

    normalize(V1.ps_name) == normalize(hierarchy.ps_name)
    AND
    normalize(V1.unit) == normalize(hierarchy.dist_name)

Exactly one hierarchy row may match. Several rows for that same pair
are ambiguous and receive no code.

When that pair misses, three narrower rules apply. None of them replace
the raw V1 station name or the raw V1 district stored on the crime.

1. The district name is in the hierarchy and the station name is not.
   Store that district's unit code. Leave ps_code empty. Keep the raw
   station name.
2. The station name equals exactly one hierarchy station, in a different
   district. Assign that station's ps_code and its unit. A name that
   exists in two districts is not a strong match and is not assigned.
3. Neither the station name nor the district is in the hierarchy. A
   fuzzy match is kept only when it is clearly closer than the next
   candidate. The raw V1 name and district are stored beside it.

The source observation is never rewritten.
"""
import json
import re
from difflib import SequenceMatcher

from psycopg2.extras import execute_values

from etl3.merger.current_state import fetch_latest_by_record_id

UNRESOLVED = "unresolved_v1_ps_code"
AMBIGUOUS = "ambiguous_v1_ps_code"
STATION_NOT_UNIQUE = "ambiguous_v1_station_name"

# Fuzzy acceptance. Both sides must be similar, and the winner must be
# clearly ahead of the next hierarchy row. A near tie is left unresolved.
FUZZY_MIN_SCORE = 0.80
FUZZY_MIN_STATION = 0.75
FUZZY_MIN_DISTRICT = 0.70
FUZZY_MIN_GAP = 0.08


def _tokens(value):
    if value is None:
        return []
    text = str(value).strip().lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return [token for token in text.split() if token]


def normalize_district(value):
    tokens = _tokens(value)
    return " ".join(tokens) or None


def normalize_ps_name(value):
    """Drop only a leading or trailing station designation.

    'Banjara Hills PS', 'Banjara Hills P.S.', 'Banjara Hills Police Station'
    and 'Banjara Hills' share one key. A blank result is not a key, so two
    empty names cannot match each other.
    """
    tokens = _tokens(value)

    def strip_edge(items, from_end):
        changed = True
        while changed and items:
            changed = False
            edge = (items[-2:], items[-1]) if from_end else (items[:2], items[0] if items else None)
            pair, single = edge
            if len(pair) == 2 and pair == ["police", "station"]:
                items = items[:-2] if from_end else items[2:]
                changed = True
            elif len(pair) == 2 and pair == ["p", "s"]:
                items = items[:-2] if from_end else items[2:]
                changed = True
            elif single == "ps":
                items = items[:-1] if from_end else items[1:]
                changed = True
        return items

    tokens = strip_edge(tokens, from_end=True)
    tokens = strip_edge(tokens, from_end=False)
    return " ".join(tokens) or None


def build_hierarchy_index(rows):
    """(normalized ps, normalized district) -> unique ps_code list.

    rows are hierarchy payloads. A key with more than one ps_code is
    ambiguous for every V1 crime that hits it.
    """
    index = {}
    for payload in rows:
        ps_name = normalize_ps_name(payload.get("ps_name"))
        district = normalize_district(payload.get("dist_name") or payload.get("district_name"))
        code = payload.get("ps_code")
        if not ps_name or not district or not code:
            continue
        codes = index.setdefault((ps_name, district), [])
        if code not in codes:
            codes.append(code)
    return index


def classify_station(ps_name, unit_name, index):
    """Return ('exact', ps_code), ('ambiguous', None), or ('unresolved', None)."""
    ps_name_key = normalize_ps_name(ps_name)
    district_key = normalize_district(unit_name)
    if not ps_name_key or not district_key:
        return "unresolved", None
    codes = index.get((ps_name_key, district_key), [])
    if len(codes) == 1:
        return "exact", codes[0]
    if len(codes) > 1:
        return "ambiguous", None
    return "unresolved", None


def _resolution(ps_code):
    return {
        "ps_code": ps_code,
        "hierarchy_ps_code": ps_code,
        "basis": "exact_ps_name_and_district",
    }


def hierarchy_records(payloads):
    """Normalized hierarchy rows that have a station, a district, and a code."""
    records = []
    for payload in payloads:
        ps_key = normalize_ps_name(payload.get("ps_name"))
        dist_key = normalize_district(payload.get("dist_name") or payload.get("district_name"))
        code = payload.get("ps_code")
        if not ps_key or not dist_key or not code:
            continue
        unit_code = payload.get("dist_code")
        records.append({
            "ps_key": ps_key,
            "dist_key": dist_key,
            "ps_code": str(code),
            "unit_code": None if unit_code is None else str(unit_code),
            "ps_name": payload.get("ps_name"),
            "dist_name": payload.get("dist_name") or payload.get("district_name"),
        })
    return records


def _similarity(left, right):
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def _from_row(row, basis, ps_name, unit_name, score=None):
    """ps_code is set only when basis is a station match. District-only leaves it null."""
    station_match = basis != "district_unit_only"
    body = {
        "basis": basis,
        "ps_code": row["ps_code"] if station_match else None,
        "hierarchy_ps_code": row["ps_code"] if station_match else None,
        "unit_code": row["unit_code"],
        "hierarchy_ps_name": row["ps_name"] if station_match else None,
        "hierarchy_district": row["dist_name"],
        "raw_ps_name": ps_name,
        "raw_district": unit_name,
    }
    if score is not None:
        body["score"] = round(score, 4)
    return body


def fuzzy_hierarchy_match(ps_key, dist_key, records):
    """Return (record, score) when one hierarchy row is clearly the closest.

    Requires station and district similarity floors, a combined score floor,
    and a gap over the next candidate. A near tie returns None.
    """
    if not ps_key or not dist_key or not records:
        return None, None
    scored = []
    for record in records:
        station = _similarity(ps_key, record["ps_key"])
        district = _similarity(dist_key, record["dist_key"])
        if station < FUZZY_MIN_STATION or district < FUZZY_MIN_DISTRICT:
            continue
        score = (0.65 * station) + (0.35 * district)
        scored.append((score, record))
    if not scored:
        return None, None
    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0.0
    if best_score < FUZZY_MIN_SCORE or (best_score - second_score) < FUZZY_MIN_GAP:
        return None, None
    return best, best_score


def match_v1_station(ps_name, unit_name, records):
    """Decide how a V1 station relates to hierarchy records.

    Returns (basis, ps_code, unit_code, resolution).
    basis is exact, ambiguous, district_unit_only, station_name_unique,
    station_name_not_unique, fuzzy, or unresolved.
    """
    index = {}
    by_name = {}
    district_units = {}
    for record in records:
        codes = index.setdefault((record["ps_key"], record["dist_key"]), [])
        if record["ps_code"] not in codes:
            codes.append(record["ps_code"])
        by_name.setdefault(record["ps_key"], [])
        if record["ps_code"] not in [item["ps_code"] for item in by_name[record["ps_key"]]]:
            by_name[record["ps_key"]].append(record)
        if record["dist_key"] not in district_units or (
            district_units[record["dist_key"]] is None and record["unit_code"] is not None
        ):
            district_units[record["dist_key"]] = record["unit_code"]

    ps_key = normalize_ps_name(ps_name)
    dist_key = normalize_district(unit_name)
    if ps_key and dist_key:
        codes = index.get((ps_key, dist_key), [])
        if len(codes) == 1:
            row = next(item for item in by_name[ps_key] if item["ps_code"] == codes[0])
            resolution = _from_row(row, "exact_ps_name_and_district", ps_name, unit_name)
            return "exact", row["ps_code"], row["unit_code"], resolution
        if len(codes) > 1:
            return "ambiguous", None, None, None

    name_hits = by_name.get(ps_key, []) if ps_key else []
    if len(name_hits) == 1 and (not dist_key or name_hits[0]["dist_key"] != dist_key):
        row = name_hits[0]
        resolution = _from_row(row, "station_name_unique", ps_name, unit_name)
        return "station_name_unique", row["ps_code"], row["unit_code"], resolution
    if len(name_hits) > 1:
        return "station_name_not_unique", None, None, {
            "basis": "station_name_not_unique",
            "ps_code": None,
            "unit_code": None,
            "raw_ps_name": ps_name,
            "raw_district": unit_name,
        }

    if dist_key in district_units and not name_hits:
        unit_code = district_units[dist_key]
        sample = next(item for item in records if item["dist_key"] == dist_key)
        resolution = {
            "basis": "district_unit_only",
            "ps_code": None,
            "hierarchy_ps_code": None,
            "unit_code": unit_code,
            "hierarchy_ps_name": None,
            "hierarchy_district": sample["dist_name"],
            "raw_ps_name": ps_name,
            "raw_district": unit_name,
        }
        return "district_unit_only", None, unit_code, resolution

    if ps_key and dist_key and not name_hits and dist_key not in district_units:
        row, score = fuzzy_hierarchy_match(ps_key, dist_key, records)
        if row is not None:
            resolution = _from_row(row, "fuzzy_ps_and_district", ps_name, unit_name, score)
            return "fuzzy", row["ps_code"], row["unit_code"], resolution

    return "unresolved", None, None, None


def preserve_derived_crime_json(stored, replayed):
    """Keep a derived ps_resolution when the source residual is replayed.

    additional_json_data is rebuilt from the source payload on every
    consolidation. That rebuild does not know about the hierarchy code
    written afterwards, so without this copy the next run treats the
    provenance as a business change and strips it.
    """
    if not isinstance(stored, dict):
        return replayed
    derived = stored.get("ps_resolution")
    if derived is None:
        return replayed
    base = dict(replayed) if isinstance(replayed, dict) else {}
    base.setdefault("ps_resolution", derived)
    return base or None


_ASSIGNED = {"exact", "station_name_unique", "fuzzy"}
_GAP_FOR = {
    "ambiguous": AMBIGUOUS,
    "station_name_not_unique": STATION_NOT_UNIQUE,
    "district_unit_only": UNRESOLVED,
    "unresolved": UNRESOLVED,
}


def _same(left, right):
    if left is None and right is None:
        return True
    return str(left) == str(right)


def enrich_v1_ps_codes(conn) -> dict:
    """Write derived station codes onto V1 crimes_unified. Does not touch *_source."""
    hierarchy = [
        payload
        for _record, _run, _created, _modified, payload, _obs
        in fetch_latest_by_record_id(conn, "hierarchy_source", "V2")
    ]
    records = hierarchy_records(hierarchy)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT crime_id, ps_name, unit_district, ps_code, unit_code,
                   additional_json_data, current_as_of, current_source_run_id
            FROM crimes_unified
            WHERE source_system = 'V1'
            """
        )
        crimes = cur.fetchall()

    updates = []
    change_rows = []
    open_gaps = []
    resolve_station = []
    counts = {
        "evaluated": 0, "exact": 0, "district_unit_only": 0,
        "station_name_unique": 0, "station_name_not_unique": 0,
        "fuzzy": 0, "unresolved": 0, "ambiguous": 0,
        "assigned": 0, "unchanged": 0,
    }
    for crime_id, ps_name, unit_name, current_code, current_unit, extra, as_of, run_id in crimes:
        counts["evaluated"] += 1
        status, code, unit_code, resolution = match_v1_station(ps_name, unit_name, records)
        extra = extra if isinstance(extra, dict) else {}
        counts[status] += 1
        stored = extra.get("ps_resolution")
        if status in _ASSIGNED:
            resolve_station.append(crime_id)
        else:
            open_gaps.append((_GAP_FOR[status], crime_id))
        if (
            _same(current_code, code)
            and _same(current_unit, unit_code)
            and stored == resolution
        ):
            counts["unchanged"] += 1
            continue
        updates.append((
            crime_id,
            code,
            unit_code,
            None if resolution is None else json.dumps(resolution),
        ))
        counts["assigned"] += 1
        if as_of is not None and not _same(current_code, code):
            change_rows.append((
                "crime", crime_id, "ps_code",
                None if current_code is None else str(current_code),
                None if code is None else str(code),
                as_of, "V1", run_id, "business_change",
            ))
        if as_of is not None and not _same(current_unit, unit_code):
            change_rows.append((
                "crime", crime_id, "unit_code",
                None if current_unit is None else str(current_unit),
                None if unit_code is None else str(unit_code),
                as_of, "V1", run_id, "business_change",
            ))

    with conn.cursor() as cur:
        if updates:
            execute_values(
                cur,
                """
                UPDATE crimes_unified AS c
                SET ps_code = v.ps_code,
                    unit_code = v.unit_code,
                    additional_json_data = CASE
                        WHEN v.resolution IS NULL THEN COALESCE(c.additional_json_data, '{}'::jsonb) - 'ps_resolution'
                        ELSE COALESCE(c.additional_json_data, '{}'::jsonb)
                             || jsonb_build_object('ps_resolution', v.resolution::jsonb)
                    END
                FROM (VALUES %s) AS v(crime_id, ps_code, unit_code, resolution)
                WHERE c.crime_id = v.crime_id AND c.source_system = 'V1'
                """,
                updates,
                template="(%s, %s, %s, %s)",
            )
        if change_rows:
            execute_values(
                cur,
                """
                INSERT INTO change_log
                    (entity, unified_id, field, old_value, new_value, observed_at,
                     source_system, source_run_id, change_classification)
                VALUES %s
                """,
                change_rows,
            )
        if open_gaps:
            execute_values(
                cur,
                """
                INSERT INTO source_gap_ledger
                    (source_system, gap_type, gap_key, first_seen_at, status, source_evidence_table)
                VALUES %s
                ON CONFLICT (source_system, gap_type, gap_key) DO NOTHING
                """,
                [("V1", gap_type, crime_id) for gap_type, crime_id in open_gaps],
                template="(%s, %s, %s, now(), 'OPEN', 'hierarchy')",
            )
        station_gap_types = (UNRESOLVED, AMBIGUOUS, STATION_NOT_UNIQUE)
        if resolve_station:
            cur.execute(
                """
                UPDATE source_gap_ledger
                SET status = 'RESOLVED'
                WHERE source_system = 'V1'
                  AND gap_type = ANY(%s)
                  AND gap_key = ANY(%s)
                  AND status = 'OPEN'
                """,
                (list(station_gap_types), resolve_station),
            )
        # Only the current reason stays open.
        for gap_type in station_gap_types:
            ids = [crime_id for open_type, crime_id in open_gaps if open_type == gap_type]
            others = [item for item in station_gap_types if item != gap_type]
            if ids:
                cur.execute(
                    """
                    UPDATE source_gap_ledger SET status = 'RESOLVED'
                    WHERE source_system = 'V1' AND gap_type = ANY(%s)
                      AND gap_key = ANY(%s) AND status = 'OPEN'
                    """,
                    (others, ids),
                )
    return counts
