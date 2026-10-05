"""
Generic current-state computation engine.

For a given unified entity and source, this:
  1. Reads ALL observations for that (source, table) from dopams_cctns
     (never from V1/V2 -- Phase 4 only ever touches dopams_cctns).
  2. Picks exactly one "winning" observation per logical record -- the one
     with the latest source-asserted timestamp (source_modified_at, falling
     back to source_created_at), tie-broken by the highest *_source.id
     (insertion order) -- never by processing order, so this is fully
     deterministic and produces the same result no matter how many times
     or in what order it's re-run.
  3. Diffs the winning observation's mapped fields against whatever is
     currently in the *_unified row (if any) and writes change_log entries
     for real differences -- never for a re-observation with no actual
     field change (stale-write prevention: an older-timestamped observation
     than what's already current never regresses the unified row, and
     recomputing from the full observation set is idempotent by
     construction, not because of any special-cased "skip if unchanged"
     branch).
  4. Upserts the *_unified row.

Everything is batched: one query to load existing *_unified state, one
bulk upsert, one bulk change_log insert per run_entity() call -- not one
round trip per row. An earlier per-row implementation made a full crimes
pass (16,840 rows) take minutes; this version does the same work in single
digits of seconds (the same N+1 lesson Phase 3 already learned once, for
the exact same class of bug).

Nothing here writes to V1 or V2, and nothing here invents a value not
present in some source payload.
"""
import datetime as _dt
import decimal as _decimal
import hashlib
import json

from psycopg2.extras import execute_values

_MIN_TS = _dt.datetime.min.replace(tzinfo=_dt.timezone.utc)

_DATE_TYPES = {"timestamp with time zone", "timestamp without time zone", "date"}


def _load_column_types(conn, table: str) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name, data_type, character_maximum_length "
            "FROM information_schema.columns WHERE table_name = %s",
            (table,),
        )
        return {name: (dtype, maxlen) for name, dtype, maxlen in cur.fetchall()}


def _coerce_value(value, data_type: str, max_length=None):
    """Parse a JSON-payload string into an actual Python datetime/date when
    the target unified column is a timestamp/date type. Fixes a real bug
    found while building this: a value read from *_source.payload (JSONB ->
    plain ISO string, since JSON has no native datetime type) compared
    byte-for-byte against the SAME value already stored in *_unified (which
    psycopg2 returns as a native datetime object) always looked "different"
    even when nothing had changed -- the exact same class of representation
    mismatch Phase 3 root-caused for V1's own record_key/natural_key
    divergence, now caught here before it could make change_log non-
    idempotent."""
    if value is None:
        return value
    if data_type == "boolean" and isinstance(value, str):
        # V1 encodes booleans as 'Y'/'N' strings (confirmed:
        # IS_ARRESTED ('Y'/'N') in the original column comparison report);
        # V2's are already native JSON booleans. Normalize so a freshly
        # re-read 'N' and a stored `False` compare equal instead of looking
        # like a change on every single rerun.
        lowered = value.strip().lower()
        if lowered in ("y", "yes", "true", "1"):
            return True
        if lowered in ("n", "no", "false", "0"):
            return False
        return value
    if data_type in ("numeric", "double precision", "real", "integer", "bigint", "smallint"):
        try:
            return float(value)
        except (TypeError, ValueError):
            return value
    if data_type not in _DATE_TYPES:
        if isinstance(value, str) and max_length and len(value) > max_length:
            # A source-side data-quality anomaly (e.g. a mobile_1 value that's
            # an obviously-garbled 21-digit string against a VARCHAR(20)
            # unified column) -- not "fixed" at the source (V1 is never
            # modified), just not allowed to break the current-state write.
            # The full, untruncated original value remains in *_source.payload
            # forever; only this computed display-state copy is shortened.
            return value[:max_length]
        return value
    parsed = value
    if isinstance(value, str):
        try:
            parsed = _dt.datetime.fromisoformat(value)
        except ValueError:
            return value
    if not isinstance(parsed, _dt.datetime):
        return parsed
    if data_type == "date":
        return parsed.date()
    if data_type == "timestamp with time zone" and parsed.tzinfo is None:
        # The source column this came from (e.g. V1's cctns_fir.reg_dt) is
        # itself a naive `timestamp without time zone`. When a naive value
        # is written into this project's timestamptz unified columns,
        # Postgres assumes the session timezone (UTC throughout this
        # project -- confirmed by every timestamp this whole effort has
        # ever compared) and returns it back as tz-aware UTC on read. A
        # freshly-parsed naive value must be given the same UTC assumption
        # before comparing, or it will never string-equal the already-
        # stored aware value even when nothing changed.
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed


def fetch_latest_by_record_id(conn, source_table: str, source_system: str, source_table_filter: str = None):
    """SQL-native 'latest observation per source_record_id'."""
    where = "WHERE source_system = %s"
    params = [source_system]
    if source_table_filter:
        where += " AND source_table = %s"
        params.append(source_table_filter)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT DISTINCT ON (source_record_id)
                source_record_id, source_run_id, source_created_at, source_modified_at, payload, id
            FROM {source_table}
            {where}
            ORDER BY source_record_id,
                     CASE
                         WHEN COALESCE(source_modified_at, source_created_at) > now() + interval '1 day'
                         THEN NULL
                         ELSE COALESCE(source_modified_at, source_created_at)
                     END DESC NULLS LAST,
                     id DESC
            """,
            params,
        )
        return cur.fetchall()


def fetch_all(conn, source_table: str, source_system: str):
    """Full scan (for Python-side grouping on a logical key other than
    source_record_id -- V1 accused). Bounded by this project's current data
    volumes (tens of thousands of rows per table, not millions)."""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT source_record_id, source_run_id, source_created_at, source_modified_at, payload, id
            FROM {source_table}
            WHERE source_system = %s
            ORDER BY id
            """,
            (source_system,),
        )
        return cur.fetchall()


def apply_field_map(payload: dict, field_map: dict) -> dict:
    out = {}
    for unified_col, spec in field_map.items():
        if spec is None:
            out[unified_col] = None
        elif callable(spec):
            out[unified_col] = spec(payload)
        else:
            out[unified_col] = payload.get(spec)
    return out


def _normalize_for_compare(v):
    if isinstance(v, dict):
        return json.dumps(v, sort_keys=True, default=str)
    if isinstance(v, (int, float, _decimal.Decimal)) and not isinstance(v, bool):
        return repr(float(v))
    return str(v)


def _values_differ(old, new):
    if old is None and new is None:
        return False
    return _normalize_for_compare(old) != _normalize_for_compare(new)


def as_aware(value):
    """UTC datetime for ordering. None stays None. Naive values are UTC,
    matching how this database session stores timestamptz. A missing or
    unparseable value does not become 'now'."""
    if value is None or value == "":
        return None
    if isinstance(value, _dt.datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=_dt.timezone.utc)
        return value.astimezone(_dt.timezone.utc)
    if isinstance(value, _dt.date):
        return _dt.datetime(value.year, value.month, value.day, tzinfo=_dt.timezone.utc)
    if isinstance(value, str):
        try:
            parsed = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return as_aware(parsed)
    return None


def ordering_ts(value, *, now=None):
    """Timestamp used only to decide which observation wins.

    A value more than a day ahead of now is not treated as newer. One
    bad future stamp must not freeze the row against every later correction.
    The stored current_as_of keeps the source value unchanged.
    """
    ts = as_aware(value)
    if ts is None:
        return None
    now = now or _dt.datetime.now(_dt.timezone.utc)
    if ts > now + _dt.timedelta(days=1):
        return None
    return ts


class UnifiedBatchWriter:
    """Batches all upserts + change_log entries for one *_unified table into
    a single load, single bulk upsert, and single bulk change_log insert."""

    def __init__(self, conn, unified_table: str, unified_pk_col: str, entity: str):
        self.conn = conn
        self.unified_table = unified_table
        self.unified_pk_col = unified_pk_col
        self.entity = entity
        self.counts = {"inserted": 0, "updated": 0, "unchanged": 0, "skipped_no_pk": 0}
        self._pending_rows = {}  # pk -> dict of all columns to write
        self._pending_change_log = []
        self._obs_ids = {}
        self._column_types = _load_column_types(conn, unified_table)
        self._existing = self._load_existing()

    def _load_existing(self):
        with self.conn.cursor() as cur:
            cur.execute(f"SELECT * FROM {self.unified_table}")
            cols = [d[0] for d in cur.description]
            return {row[0]: dict(zip(cols, row)) for row in cur.fetchall()}

    def add(self, pk_value, *, source_system, source_record_id, mapped_fields, extra_fields,
            source_run_id, current_as_of, observation_id=None):
        existing = self._existing.get(pk_value)
        all_fields = {**mapped_fields, **extra_fields}
        coerced_fields = {}
        for k, v in all_fields.items():
            dtype, maxlen = self._column_types.get(k, ("", None))
            coerced_fields[k] = _coerce_value(v, dtype, maxlen)
        all_fields = coerced_fields
        if self.entity == "crime" and existing is not None and "additional_json_data" in all_fields:
            # Derived hierarchy provenance is not a source field. A replay
            # of the V1 residual must not strip it or log it as a change.
            from etl3.merger.ps_enrichment import preserve_derived_crime_json
            all_fields["additional_json_data"] = preserve_derived_crime_json(
                existing.get("additional_json_data"),
                all_fields.get("additional_json_data"),
            )

        incoming_ts = as_aware(current_as_of)
        incoming_order = ordering_ts(current_as_of)
        if existing is not None:
            existing_order = ordering_ts(existing.get("current_as_of"))
            # A missing or unusable timestamp is not newer than a known
            # one, and a strictly older instant never replaces current
            # state. Equal instants, including two missing ones, are
            # ordered by observation id when both are known, so A-then-B
            # and B-then-A converge.
            if existing_order is not None and incoming_order is None:
                self.counts["unchanged"] += 1
                return
            if (
                existing_order is not None
                and incoming_order is not None
                and incoming_order < existing_order
            ):
                self.counts["unchanged"] += 1
                return
            if incoming_order == existing_order:
                prev_obs = self._obs_ids.get(pk_value)
                if (
                    observation_id is not None
                    and prev_obs is not None
                    and observation_id < prev_obs
                ):
                    self.counts["unchanged"] += 1
                    return

        any_change = existing is None
        for field, new_value in all_fields.items():
            old_value = existing.get(field) if existing else None
            if _values_differ(old_value, new_value):
                any_change = True
                if incoming_ts is not None:
                    classification = "initial_observation" if existing is None else "business_change"
                    self._pending_change_log.append((
                        self.entity, pk_value, field,
                        None if old_value is None else str(old_value),
                        None if new_value is None else str(new_value),
                        incoming_ts, source_system, source_run_id, classification,
                    ))

        row = {
            self.unified_pk_col: pk_value,
            "source_record_id": source_record_id,
            **all_fields,
            "current_source_run_id": source_run_id,
            "current_as_of": incoming_ts,
        }
        if "source_system" in self._column_types:
            row["source_system"] = source_system
        self._pending_rows[pk_value] = row
        if existing is None:
            self.counts["inserted"] += 1
        elif any_change:
            self.counts["updated"] += 1
        else:
            self.counts["unchanged"] += 1

        # keep in-memory view current in case the same pk is add()-ed twice
        # in one batch (shouldn't happen given DISTINCT ON upstream, but
        # makes repeated add() calls for the same pk safe either way)
        merged = dict(existing) if existing else {}
        merged.update(self._pending_rows[pk_value])
        self._existing[pk_value] = merged
        if observation_id is not None:
            self._obs_ids[pk_value] = observation_id

    def flush(self):
        if self._pending_change_log:
            with self.conn.cursor() as cur:
                execute_values(
                    cur,
                    """
                    INSERT INTO change_log
                        (entity, unified_id, field, old_value, new_value, observed_at,
                         source_system, source_run_id, change_classification)
                    VALUES %s
                    """,
                    self._pending_change_log,
                )

        if self._pending_rows:
            rows = list(self._pending_rows.values())
            columns = list(rows[0].keys())
            # every row must have the same column set for execute_values;
            # fill any missing with None (can happen if two calls to add()
            # used field_maps with different optional columns -- not
            # expected in this phase's usage, defensive only)
            all_cols = set()
            for r in rows:
                all_cols.update(r.keys())
            columns = [self.unified_pk_col] + sorted(c for c in all_cols if c != self.unified_pk_col)
            values = [
                tuple(
                    json.dumps(r.get(c)) if isinstance(r.get(c), dict) else r.get(c)
                    for c in columns
                )
                for r in rows
            ]
            update_set = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns if c != self.unified_pk_col)
            update_set += ", computed_at = now()"
            with self.conn.cursor() as cur:
                execute_values(
                    cur,
                    f"""
                    INSERT INTO {self.unified_table} ({", ".join(columns)})
                    VALUES %s
                    ON CONFLICT ({self.unified_pk_col}) DO UPDATE SET {update_set}
                    """,
                    values,
                )
        return dict(self.counts)


def run_entity(conn, *, entity: str, unified_table: str, unified_pk_col: str,
                source_table: str, source_system: str, field_map_entry: dict,
                consolidation_run_id: str, source_table_filter: str = None,
                extra_fields_fn=None, pk_namespace: str = None) -> dict:
    """extra_fields_fn(payload) -> dict of relationship/computed columns
    (crime_id, person_id, unlinked_person_flag, ...) not covered by the
    plain field_map."""
    field_map = field_map_entry["map"]
    unified_pk_fn = field_map_entry["unified_pk"]

    rows = fetch_latest_by_record_id(conn, source_table, source_system, source_table_filter)
    writer = UnifiedBatchWriter(conn, unified_table, unified_pk_col, entity)
    skipped = 0
    for source_record_id, source_run_id, source_created_at, source_modified_at, payload, observation_id in rows:
        pk_value = unified_pk_fn(payload)
        if not pk_value:
            skipped += 1
            continue
        if pk_namespace:
            pk_value = f"{pk_namespace}:{pk_value}"
        mapped = apply_field_map(payload, field_map)
        extra = extra_fields_fn(payload) if extra_fields_fn else {}
        current_as_of = source_modified_at or source_created_at
        writer.add(str(pk_value), source_system=source_system, source_record_id=source_record_id,
                   mapped_fields=mapped, extra_fields=extra,
                   source_run_id=source_run_id, current_as_of=current_as_of,
                   observation_id=observation_id)
    counts = writer.flush()
    counts["skipped_no_pk"] = skipped
    return counts


def run_v1_accused_grouped(conn, *, consolidation_run_id: str):
    """V1 accused_unified: grouped by logical key (not accused_id, which
    churns -- see v1_accused_grouping.py), picking the latest observation
    per group. source_record_id persisted is the WINNING row's own
    accused_id, for traceability back to accused_source, even though it
    wasn't the grouping key.

    person_id is resolved via the cross-feed correlation key (see
    v1_cross_link.py) against accused_details' person_code -- exactly one
    candidate required, else left NULL and tracked in source_gap_ledger.

    Returns (counts, correlation_lookup) where correlation_lookup maps
    {(fir, norm_name, norm_father): accused_unified.accused_id} for
    run_v1_arrests() to reuse without recomputing the grouping.
    """
    from etl3.merger import field_maps
    from etl3.merger.v1_accused_grouping import logical_key
    from etl3.merger.v1_cross_link import build_accused_details_lookup, correlation_key, record_unresolved_link_gap

    field_map = field_maps.ACCUSED["V1"]["map"]
    rows = fetch_all(conn, "accused_source", "V1")
    details_lookup = build_accused_details_lookup(conn)

    groups = {}
    for source_record_id, source_run_id, source_created_at, source_modified_at, payload, row_id in rows:
        key = logical_key(payload)
        ts = ordering_ts(source_modified_at or source_created_at)
        sort_key = (ts or _MIN_TS, row_id)
        if key not in groups or sort_key > groups[key][0]:
            groups[key] = (sort_key, source_record_id, source_run_id, payload)

    writer = UnifiedBatchWriter(conn, "accused_unified", "accused_id", "accused")
    link_counts = {"person_linked": 0, "person_unresolved": 0, "skipped_no_pk": 0, "logical_groups": len(groups)}
    correlation_lookup = {}
    unresolved_gaps = []
    for key, (sort_key, source_record_id, source_run_id, payload) in groups.items():
        ts = sort_key[0] if sort_key[0] != _MIN_TS else None
        mapped = apply_field_map(payload, field_map)
        fir = payload.get("fir_reg_num")
        if not fir:
            link_counts["skipped_no_pk"] += 1
            continue
        pk_value = "V1:" + hashlib.md5(key.encode("utf-8")).hexdigest()

        ck = correlation_key(fir, payload.get("accused_name"), payload.get("father_name"))
        correlation_lookup[ck] = pk_value
        candidates = details_lookup.get(ck, [])
        if len(candidates) == 1:
            person_id = candidates[0]
            link_counts["person_linked"] += 1
        else:
            person_id = None
            link_counts["person_unresolved"] += 1
            unresolved_gaps.append(("unresolved_accused_person_link", f"{ck}|candidates={len(candidates)}"))

        extra = {"crime_id": fir, "person_id": person_id, "unlinked_person_flag": person_id is None}
        writer.add(pk_value, source_system="V1", source_record_id=source_record_id,
                   mapped_fields=mapped, extra_fields=extra,
                   source_run_id=source_run_id, current_as_of=ts,
                   observation_id=sort_key[1])

    counts = writer.flush()
    counts.update(link_counts)
    _bulk_record_gaps(conn, unresolved_gaps)
    return counts, correlation_lookup


def run_v1_arrests(conn, correlation_lookup: dict, *, consolidation_run_id: str) -> dict:
    """V1 arrests_unified, from accused_details (arrests_source). Links back
    to accused_unified via the same correlation key run_v1_accused_grouped
    used -- exactly one candidate required, else accused_id left NULL
    (nullable since migration 002) and tracked in source_gap_ledger."""
    from etl3.merger import field_maps
    from etl3.merger.v1_cross_link import correlation_key

    field_map = field_maps.ARRESTS["V1"]["map"]
    rows = fetch_latest_by_record_id(conn, "arrests_source", "V1")
    writer = UnifiedBatchWriter(conn, "arrests_unified", "arrest_id", "arrest")
    link_counts = {"accused_linked": 0, "accused_unresolved": 0, "skipped_no_pk": 0}
    unresolved_gaps = []
    for source_record_id, source_run_id, created_at, modified_at, payload, observation_id in rows:
        fir = payload.get("fir_reg_num")
        if not fir:
            link_counts["skipped_no_pk"] += 1
            continue
        mapped = apply_field_map(payload, field_map)
        ck = correlation_key(fir, payload.get("accused_name"), payload.get("father_name"))
        accused_pk = correlation_lookup.get(ck)
        if accused_pk:
            link_counts["accused_linked"] += 1
        else:
            link_counts["accused_unresolved"] += 1
            unresolved_gaps.append(("unresolved_arrest_accused_link", f"{ck}|accused_details_id={source_record_id}"))
        extra = {"crime_id": fir, "accused_id": accused_pk}
        current_as_of = modified_at or created_at
        writer.add(f"V1:{source_record_id}", source_system="V1", source_record_id=source_record_id,
                   mapped_fields=mapped, extra_fields=extra,
                   source_run_id=source_run_id, current_as_of=current_as_of,
                   observation_id=observation_id)
    counts = writer.flush()
    counts.update(link_counts)
    _bulk_record_gaps(conn, unresolved_gaps)
    return counts


def _bulk_record_gaps(conn, gaps):
    if not gaps:
        return
    rows = [("V1", gap_type, gap_key) for gap_type, gap_key in gaps]
    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO source_gap_ledger
                (source_system, gap_type, gap_key, first_seen_at, status, source_evidence_table)
            VALUES %s
            ON CONFLICT (source_system, gap_type, gap_key) DO NOTHING
            """,
            rows,
            template="(%s, %s, %s, now(), 'OPEN', 'cross-feed correlation (accused dossier vs accused_details)')",
        )
