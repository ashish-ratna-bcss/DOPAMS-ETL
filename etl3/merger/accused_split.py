"""Split a high-confidence packed CCTNS accused name into real, additional
``persons_unified`` / ``accused_unified`` rows.

This is deliberately a MERGER-layer module, not an enrichment one: per the
existing ownership boundary documented across this codebase (current_state.py,
field_maps.py), the merger is what creates ``*_unified`` rows; enrichment
only ever updates its own one-to-one ``*_enrichment`` tables for rows that
already exist. A split child is a real additional accused appearance, so
creating it belongs here, reusing ``current_state.UnifiedBatchWriter`` --
the same batched upsert/change_log engine every other merger write path
already uses -- rather than a parallel upsert implementation.

Once a split child's ``persons_unified``/``accused_unified`` rows exist,
nothing else in this module is needed to clean its name, resolve its
address, or classify/AI-enrich it as an accused: the existing, UNMODIFIED
``etl3.enrichment.runner._person_enrichment`` / ``_accused_enrichment`` /
AI roster pipeline does that on its next pass, exactly as it already does
for any other persons_unified/accused_unified row. This module only ever
decides WHETHER a split is warranted and WHAT the new rows' raw identity
and provenance fields are -- never their cleaned name, alias, relationship,
or resolved address.

Nothing here ever matches or merges a split person against any OTHER
existing person by name. Cross-system identity candidates for a split
person are produced later, if at all, by the pre-existing, unmodified
etl3.identity.person_matching candidate generator, which already treats
every persons_unified row the same way regardless of how it was created,
and which never auto-confirms a match.

Crime-level duplicate guard (added after FIR 869/2026 showed CCTNS V2
itself can carry the same people both as separate, independently-coded
accused AND as one combined-pack record on the same crime): before a
segment becomes a real child, it is checked against every OTHER already-
existing, non-split, non-superseded accused on the SAME crime_id via
etl3.merger.accused_overlap. A segment that overlaps is withheld -- never
auto-split, never auto-linked to the existing record -- and logged instead
(see _log_overlap_withheld below). This is deliberately NOT a persisted
review queue: there is no process or owner to triage one, so the evidence
goes to the application log (searchable, same as every other diagnostic
signal this pipeline already emits) rather than a new stateful table. A
withheld segment is re-evaluated fresh on every run; if the overlap stops
reproducing (the conflicting record is retired, or the text changes), the
segment is simply split normally on that later run -- no manual unblock
step exists or is needed.
"""
import hashlib
import logging
import re

from etl3.enrichment.multi_accused import detect_multi_accused
from etl3.merger.accused_overlap import find_crime_overlaps

PARSER_VERSION = "multi_accused_v1"

_SPLIT_MARKER = ":split:"

logger = logging.getLogger(__name__)


def _norm_for_id(text):
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _hash(payload) -> str:
    import json
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _pack_hash(raw_full_name) -> str:
    return hashlib.sha256((raw_full_name or "").encode("utf-8")).hexdigest()


def split_child_id(parent_id, segment_index, raw_segment_text):
    """Deterministic id for one split child.

    Keyed on (parent id, position, the raw text actually seen at that
    position) -- never a timestamp or random uuid, and never text alone
    (it is namespaced under the parent). If the text at a position changes
    between runs (correction, reordering), this produces a DIFFERENT id on
    purpose: the caller must supersede the old accused_unified/
    persons_unified rows rather than silently overwrite their identity (and
    whatever enrichment/AI work already landed on them) with a different
    person's content. See reconcile/diff_superseded_children below.
    """
    basis = f"{parent_id}|{segment_index}|{_norm_for_id(raw_segment_text)}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]


def _log_overlap_withheld(parent, segment, overlap):
    """The only record a withheld segment gets: an application log line
    with enough evidence (parent/crime/segment text, the existing accused
    it overlaps, and the match basis/score) for someone grepping logs or
    change-tracking this run to see why no child was created. Never
    writes to the database -- see the module docstring for why there is
    no persisted review queue."""
    logger.warning(
        "multi_accused_split: withheld segment %s (%r) of parent %s on crime %s -- "
        "overlaps existing accused %s (%r), basis=%s score=%s",
        segment["segment_index"], segment["raw_text"], parent["accused_id"],
        parent.get("crime_id"), overlap["accused_id"], overlap["full_name"],
        overlap["basis"], overlap["score"],
    )


def build_split_rows(parent, raw_full_name, existing_crime_accused=None):
    """Parse one accused's raw full name and build rows for a high-
    confidence pack only.

    ``parent`` must have: accused_id, source_system, crime_id, person_id
    (may be None), current_source_run_id, current_as_of -- the parent's own
    already-current values, reused as-is for the children since their
    content is derived from that same source observation.

    ``existing_crime_accused``: optional list of (accused_id, full_name)
    for every OTHER non-split, non-superseded accused already on this
    parent's own crime_id -- the caller's responsibility to scope
    correctly (see etl3.merger.accused_overlap.find_crime_overlaps). When
    given, a segment whose text overlaps one of these (exact-normalized,
    exact-token-set-reordered, or strong fuzzy similarity -- see
    accused_overlap.name_overlap_score) is withheld from
    person_rows/accused_rows/audit_rows and logged instead (see
    _log_overlap_withheld) -- nothing here ever assigns that existing
    accused_id to the segment; withholding is the only action taken. When
    omitted (None, the default), every high-confidence segment is built
    exactly as before this parameter existed -- fully backward compatible
    with every existing caller.

    Returns (person_rows, accused_rows, audit_rows, parse_result). All
    three row lists are empty unless the parse is "high" confidence --
    callers must not write anything in that case, leaving every existing
    persons_unified / accused_unified / person_enrichment / accused_enrichment
    row exactly as it is today. Never fabricates a person for "and N
    others" (detect_multi_accused never returns segments for that case).
    """
    parse_result = detect_multi_accused(raw_full_name)
    if parse_result["confidence"] != "high" or not parse_result["segments"]:
        return [], [], [], parse_result

    parent_accused_id = parent["accused_id"]
    parent_person_id = parent.get("person_id")
    source_system = parent["source_system"]
    crime_id = parent["crime_id"]
    # Carried through per-row (not fixed per call) because one caller
    # batch may cover many parents, each with its own provenance -- see
    # write_split_rows, which passes these straight through to
    # UnifiedBatchWriter.add()'s source_run_id/current_as_of kwargs.
    parent_run_id = parent["current_source_run_id"]
    parent_as_of = parent["current_as_of"]
    shared_address_text = parse_result.get("shared_address_text")
    pack_hash = _pack_hash(raw_full_name)

    overlaps = (
        find_crime_overlaps(parse_result["segments"], existing_crime_accused)
        if existing_crime_accused
        else {}
    )

    person_rows, accused_rows, audit_rows = [], [], []
    for segment in parse_result["segments"]:
        idx = segment["segment_index"]
        raw_text = segment["raw_text"]

        overlap = overlaps.get(idx)
        if overlap is not None:
            _log_overlap_withheld(parent, segment, overlap)
            continue

        child_key = split_child_id(parent_accused_id, idx, raw_text)
        person_id = f"{parent_person_id or parent_accused_id}{_SPLIT_MARKER}{child_key}"
        accused_id = f"{parent_accused_id}{_SPLIT_MARKER}{child_key}"

        person_row = {
            "person_id": person_id,
            "source_system": source_system,
            "source_record_id": person_id,
            "full_name": raw_text,
            "derived_from_person_id": parent_person_id,
            "split_segment_index": idx,
            "_current_source_run_id": parent_run_id,
            "_current_as_of": parent_as_of,
        }
        if shared_address_text:
            person_row["present_address_text"] = shared_address_text
            person_row["permanent_address_text"] = shared_address_text
        person_rows.append(person_row)

        accused_rows.append({
            "accused_id": accused_id,
            "source_system": source_system,
            "source_record_id": accused_id,
            "crime_id": crime_id,
            "person_id": person_id,
            "unlinked_person_flag": False,
            "derived_from_accused_id": parent_accused_id,
            "split_segment_index": idx,
            "split_parser_version": PARSER_VERSION,
            "_current_source_run_id": parent_run_id,
            "_current_as_of": parent_as_of,
        })

        audit = {
            "split_audit_id": accused_id,
            "parent_accused_id": parent_accused_id,
            "created_accused_id": accused_id,
            "created_person_id": person_id,
            "segment_index": idx,
            "label_number": segment.get("label_number"),
            "raw_pack_text": raw_full_name,
            "raw_segment_text": raw_text,
            "parser_version": PARSER_VERSION,
            "parse_method": parse_result["method"],
            "pack_hash": pack_hash,
            "status": "active",
        }
        audit["input_hash"] = _hash(audit)
        audit_rows.append(audit)

    return person_rows, accused_rows, audit_rows, parse_result


def diff_superseded_children(existing_child_accused_ids, new_child_accused_ids):
    """Pure set-diff: existing split-child accused_ids not reproduced this
    run.

    Ids present in both sets are left alone entirely (their
    persons_unified/accused_unified rows upsert as a no-op change via
    UnifiedBatchWriter's own existing-value diff, and their accused_enrichment
    / AI work is untouched). Reordering is intentionally lossy-but-safe: if
    the text at two positions swaps, both old ids disappear and two new
    ids appear, rather than this trying to guess "the same person moved" --
    that guess is exactly what would risk handing one person's AI
    enrichment to a different person.
    """
    return sorted(set(existing_child_accused_ids) - set(new_child_accused_ids))


# ---------------------------------------------------------------------------
# Database-touching half. Requires migration 018 (the new nullable columns
# and accused_split_audit table) to already be applied. Every write here
# is additive/upsert; nothing is ever deleted.
# ---------------------------------------------------------------------------

def existing_active_children(conn, parent_accused_id):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT accused_id FROM accused_unified
            WHERE derived_from_accused_id = %s AND split_superseded_at IS NULL
            """,
            (parent_accused_id,),
        )
        return [row[0] for row in cur.fetchall()]


def supersede_children(conn, accused_ids):
    """Mark accused_unified rows (and their matching audit rows) retired.

    Never deletes. A superseded row's own persons_unified row is left
    exactly as it is -- it simply stops being reachable from any live
    accused, the same way an orphaned person record behaves everywhere
    else in this schema.
    """
    if not accused_ids:
        return 0
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE accused_unified SET split_superseded_at = now()
            WHERE accused_id = ANY(%s) AND split_superseded_at IS NULL
            """,
            (list(accused_ids),),
        )
        updated = cur.rowcount
        cur.execute(
            """
            UPDATE accused_split_audit SET status = 'superseded'
            WHERE created_accused_id = ANY(%s) AND status = 'active'
            """,
            (list(accused_ids),),
        )
    return updated


def supersede_parent_if_unset(conn, parent_accused_id):
    """Mark the packed parent retired from future AI roster consideration,
    once, the first time it produces a high-confidence split. Idempotent:
    a rerun that reproduces the same split leaves the timestamp untouched.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE accused_unified SET split_superseded_at = now()
            WHERE accused_id = %s AND split_superseded_at IS NULL
            """,
            (parent_accused_id,),
        )
        return cur.rowcount


def unsupersede_parent_if_set(conn, parent_accused_id):
    """Reverse of supersede_parent_if_unset -- used ONLY when a pack's
    source text has stopped parsing as a multi-accused pack at all (e.g.
    a 2-segment pack's text shrank to a single remaining name;
    detect_multi_accused correctly requires 2+ markers to recognize a
    pack, and that rule is never touched here) and its real split
    children have just been retired by the same reconciliation pass (see
    etl3.enrichment.runner._multi_accused_split's shrink-to-single-name
    branch). The parent's own persons_unified/accused_unified row is
    never frozen by split_superseded_at -- the ordinary, unmodified
    merger keeps its content current regardless of that flag -- so once
    it stops being a pack, it is once again the correct, sole live
    representation of this accused and must become visible to DOPAMS-BE
    and the AI roster again, exactly like any other ordinary accused row.
    Idempotent: a rerun that finds nothing to unset is a no-op.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE accused_unified SET split_superseded_at = NULL
            WHERE accused_id = %s AND split_superseded_at IS NOT NULL
            """,
            (parent_accused_id,),
        )
        return cur.rowcount


_ROW_META_KEYS = ("_current_source_run_id", "_current_as_of")


def write_split_rows(conn, person_rows, accused_rows, audit_rows, *, enrichment_run_id):
    """Upsert split children into persons_unified / accused_unified via the
    same UnifiedBatchWriter the merger already uses for every other write
    to those tables, plus the slim audit trail via the existing enrichment
    upsert_rows helper (accused_split_audit has its own input_hash, unlike
    persons_unified/accused_unified).

    Each row carries its own parent's current_source_run_id/current_as_of
    (see build_split_rows) -- a single call here may cover many different
    parent accused, each with different provenance, batched into one
    UnifiedBatchWriter / one flush() for the same reason the merger itself
    batches: _load_existing() reads the whole table once, not once per row.
    ``enrichment_run_id`` is used only for the audit table's own
    enrichment_run_id column, not for persons_unified/accused_unified
    (those track source provenance, not the ETL3 enrichment run).
    """
    from etl3.enrichment.persist import upsert_rows
    from etl3.merger.current_state import UnifiedBatchWriter

    person_writer = UnifiedBatchWriter(conn, "persons_unified", "person_id", "person")
    for row in person_rows:
        mapped = {
            k: v for k, v in row.items()
            if k not in ("person_id", "source_system", "source_record_id", *_ROW_META_KEYS)
        }
        person_writer.add(
            row["person_id"], source_system=row["source_system"],
            source_record_id=row["source_record_id"], mapped_fields=mapped,
            extra_fields={}, source_run_id=row["_current_source_run_id"],
            current_as_of=row["_current_as_of"],
        )
    person_stats = person_writer.flush()

    accused_writer = UnifiedBatchWriter(conn, "accused_unified", "accused_id", "accused")
    for row in accused_rows:
        mapped = {
            k: v for k, v in row.items()
            if k not in ("accused_id", "source_system", "source_record_id", *_ROW_META_KEYS)
        }
        accused_writer.add(
            row["accused_id"], source_system=row["source_system"],
            source_record_id=row["source_record_id"], mapped_fields=mapped,
            extra_fields={}, source_run_id=row["_current_source_run_id"],
            current_as_of=row["_current_as_of"],
        )
    accused_stats = accused_writer.flush()

    audit_stats = upsert_rows(
        conn, "accused_split_audit", "split_audit_id", audit_rows,
        ["split_audit_id", "parent_accused_id", "created_accused_id",
         "created_person_id", "segment_index", "label_number",
         "raw_pack_text", "raw_segment_text", "parser_version",
         "parse_method", "pack_hash", "status", "input_hash"],
        enrichment_run_id, "accused_split_audit",
    )
    return {"persons": person_stats, "accused": accused_stats, "audit": audit_stats}
