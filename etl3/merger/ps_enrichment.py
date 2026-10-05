"""Assign a V2 hierarchy ps_code onto a V1 crime only on an exact match.

Rule, and the only rule:

    normalize(V1.ps_name) == normalize(hierarchy.ps_name)
    AND
    normalize(V1.unit) == normalize(hierarchy.dist_name)

Exactly one hierarchy row may match. Zero matches and several matches
are recorded and do not receive a code. The source observation is never
rewritten. V1 ps_name and unit_district stay on the crime row; the
derived code and its hierarchy reference live beside them.
"""
import json
import re

from psycopg2.extras import execute_values

from etl3.merger.current_state import fetch_latest_by_record_id

UNRESOLVED = "unresolved_v1_ps_code"
AMBIGUOUS = "ambiguous_v1_ps_code"


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


def enrich_v1_ps_codes(conn) -> dict:
    """Write derived ps_code onto V1 crimes_unified. Does not touch *_source."""
    hierarchy = [
        payload
        for _record, _run, _created, _modified, payload, _obs
        in fetch_latest_by_record_id(conn, "hierarchy_source", "V2")
    ]
    index = build_hierarchy_index(hierarchy)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT crime_id, ps_name, unit_district, ps_code, additional_json_data,
                   current_as_of, current_source_run_id
            FROM crimes_unified
            WHERE source_system = 'V1'
            """
        )
        crimes = cur.fetchall()

    updates = []
    change_rows = []
    open_gaps = []
    resolve_exact = []
    counts = {"evaluated": 0, "exact": 0, "assigned": 0, "unresolved": 0, "ambiguous": 0, "unchanged": 0}
    for crime_id, ps_name, unit_name, current_code, extra, as_of, run_id in crimes:
        counts["evaluated"] += 1
        status, code = classify_station(ps_name, unit_name, index)
        extra = extra if isinstance(extra, dict) else {}
        if status == "exact":
            counts["exact"] += 1
            resolve_exact.append(crime_id)
            wanted = _resolution(code)
            if current_code == code and extra.get("ps_resolution") == wanted:
                counts["unchanged"] += 1
                continue
            updates.append((crime_id, code, json.dumps(wanted)))
            counts["assigned"] += 1
            if as_of is not None and current_code != code:
                change_rows.append((
                    "crime", crime_id, "ps_code",
                    None if current_code is None else str(current_code),
                    code, as_of, "V1", run_id, "business_change",
                ))
        else:
            counts[status] += 1
            open_gaps.append((status == "ambiguous" and AMBIGUOUS or UNRESOLVED, crime_id))
            if current_code is not None or "ps_resolution" in extra:
                updates.append((crime_id, None, None))
                if as_of is not None and current_code is not None:
                    change_rows.append((
                        "crime", crime_id, "ps_code", str(current_code), None,
                        as_of, "V1", run_id, "business_change",
                    ))

    with conn.cursor() as cur:
        if updates:
            execute_values(
                cur,
                """
                UPDATE crimes_unified AS c
                SET ps_code = v.ps_code,
                    additional_json_data = CASE
                        WHEN v.resolution IS NULL THEN COALESCE(c.additional_json_data, '{}'::jsonb) - 'ps_resolution'
                        ELSE COALESCE(c.additional_json_data, '{}'::jsonb)
                             || jsonb_build_object('ps_resolution', v.resolution::jsonb)
                    END
                FROM (VALUES %s) AS v(crime_id, ps_code, resolution)
                WHERE c.crime_id = v.crime_id AND c.source_system = 'V1'
                """,
                updates,
                template="(%s, %s, %s)",
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
        if resolve_exact:
            cur.execute(
                """
                UPDATE source_gap_ledger
                SET status = 'RESOLVED'
                WHERE source_system = 'V1'
                  AND gap_type IN (%s, %s)
                  AND gap_key = ANY(%s)
                  AND status = 'OPEN'
                """,
                (UNRESOLVED, AMBIGUOUS, resolve_exact),
            )
        # A crime that is now ambiguous must not keep an unresolved row, and
        # the reverse. The open insert above is the current reason.
        ambiguous_ids = [crime_id for gap_type, crime_id in open_gaps if gap_type == AMBIGUOUS]
        unresolved_ids = [crime_id for gap_type, crime_id in open_gaps if gap_type == UNRESOLVED]
        if ambiguous_ids:
            cur.execute(
                """
                UPDATE source_gap_ledger SET status = 'RESOLVED'
                WHERE source_system = 'V1' AND gap_type = %s AND gap_key = ANY(%s) AND status = 'OPEN'
                """,
                (UNRESOLVED, ambiguous_ids),
            )
        if unresolved_ids:
            cur.execute(
                """
                UPDATE source_gap_ledger SET status = 'RESOLVED'
                WHERE source_system = 'V1' AND gap_type = %s AND gap_key = ANY(%s) AND status = 'OPEN'
                """,
                (AMBIGUOUS, unresolved_ids),
            )
    return counts
