"""
V1's accused dossier (cctns_accused) and accused_details are two
INDEPENDENTLY fetched source feeds for the same FIR (confirmed:
ACCUSED_API_URL vs ACCUSED_DETAILS_API_URL, two different endpoints in
cctns-v1's own .env). Neither carries a shared key that points at the
other -- no accused_id/person_code cross-reference exists between them.

To link accused_unified (grouped from the dossier) to persons_unified
(keyed by accused_details.person_code), and to link arrests_unified (from
accused_details) back to the right accused_unified row, this module builds
one shared correlation key -- (fir_reg_num, normalized name, normalized
father_name) -- and requires an EXACT, UNAMBIGUOUS match (exactly one
candidate) before linking. Zero or multiple candidates for a key are left
unresolved and recorded in source_gap_ledger, never guessed.

This is deliberately a WEAKER key than the full logical_key used for
grouping accused_source itself (which also uses mobile/dob to avoid
collapsing distinct same-name people within one FIR) -- it is used only
for cross-table correlation where no stronger shared field exists, and its
ambiguity (multiple accused sharing a name within one FIR) is explicitly
tracked rather than resolved by guessing.
"""
import re


def _norm(value):
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).strip().lower())


def correlation_key(fir_reg_num, name, father_name):
    return (_norm(fir_reg_num), _norm(name), _norm(father_name))


def build_accused_details_lookup(conn):
    """{(fir, norm_name, norm_father): [person_code, ...]} from the latest
    accused_details (arrests_source) observation per accused_id."""
    from etl3.merger.current_state import fetch_latest_by_record_id

    rows = fetch_latest_by_record_id(conn, "arrests_source", "V1")
    lookup = {}
    for source_record_id, source_run_id, created_at, modified_at, payload in rows:
        key = correlation_key(payload.get("fir_reg_num"), payload.get("accused_name"), payload.get("father_name"))
        person_code = payload.get("person_code")
        if person_code:
            lookup.setdefault(key, []).append(person_code)
    return lookup


def record_unresolved_link_gap(conn, gap_type: str, gap_key: str):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO source_gap_ledger
                (source_system, gap_type, gap_key, first_seen_at, status, source_evidence_table)
            VALUES ('V1', %s, %s, now(), 'OPEN', 'cross-feed correlation (accused dossier vs accused_details)')
            ON CONFLICT (source_system, gap_type, gap_key) DO NOTHING
            """,
            (gap_type, gap_key),
        )
