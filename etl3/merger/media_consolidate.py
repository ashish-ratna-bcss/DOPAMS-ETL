"""Consolidate media_source observations into media_unified with live path checks."""
from __future__ import annotations

from etl3.media.paths import (
    classify_v1_availability,
    classify_v2_availability,
    clear_dir_cache,
    resolve_v1_paths,
    resolve_v2_paths,
)
from etl3.merger.current_state import UnifiedBatchWriter, fetch_latest_by_record_id


def _v1_parent(payload: dict) -> dict:
    entity = (payload.get("entity_type") or "").upper()
    fir = payload.get("fir_reg_num")
    parent_type = "fir" if entity == "FIR" else ("court" if entity == "COURT" else entity.lower() or None)
    return {
        "attachment_category": entity or None,
        "parent_crime_id": fir,
        "parent_entity_type": parent_type,
        "parent_entity_id": fir,
        "original_attach_path": payload.get("attach_path"),
        "original_file_name": payload.get("dms_file_name"),
        "source_file_id": None,
        "file_size_bytes": payload.get("file_size_bytes"),
        "mime_type": None,
        "source_download_status": payload.get("status"),
        "downloaded_at": payload.get("downloaded_at"),
    }


def _v2_parent(payload: dict) -> dict:
    source_type = (payload.get("source_type") or "").lower()
    source_field = payload.get("source_field") or ""
    parent_id = payload.get("parent_id")
    category = f"{source_type}/{source_field}" if source_type or source_field else None
    # Map bookkeeping source_type to parent entity labels used in unified model.
    type_map = {
        "crime": "crime",
        "person": "person",
        "property": "property",
        "interrogation": "interrogation",
        "chargesheets": "chargesheet",
        "case_property": "case_property",
        "mo_seizures": "mo_seizure",
    }
    parent_type = type_map.get(source_type, source_type or None)
    parent_crime_id = parent_id if source_type == "crime" else None
    return {
        "attachment_category": category,
        "parent_crime_id": parent_crime_id,
        "parent_entity_type": parent_type,
        "parent_entity_id": str(parent_id) if parent_id is not None else None,
        "original_attach_path": payload.get("file_path"),
        "original_file_name": payload.get("media_name"),
        "source_file_id": str(payload.get("file_id")) if payload.get("file_id") is not None else None,
        "file_size_bytes": None,
        "mime_type": None,
        "source_download_status": _v2_status_label(payload),
        "downloaded_at": payload.get("downloaded_at"),
    }


def _v2_status_label(payload: dict) -> str:
    if payload.get("is_downloaded"):
        return "DOWNLOADED"
    if payload.get("is_empty"):
        return "EMPTY"
    if payload.get("download_error"):
        return "FAILED"
    if payload.get("file_id") is None:
        return "NO_FILE_ID"
    return "PENDING"


def _project_v1(payload: dict) -> dict:
    paths = resolve_v1_paths(payload)
    avail, detail = classify_v1_availability(payload, paths)
    fields = _v1_parent(payload)
    fields.update(
        {
            # Matches catalog source_table / observation source_table tag.
            "source_module": "media",
            "source_local_path": paths.get("source_local_path"),
            "resolved_relative_path": paths.get("resolved_relative_path"),
            "media_root_key": paths.get("media_root_key"),
            "availability_status": avail,
            "availability_detail": detail,
        }
    )
    return fields


def _project_v2(payload: dict) -> dict:
    paths = resolve_v2_paths(payload)
    avail, detail = classify_v2_availability(payload, paths)
    fields = _v2_parent(payload)
    fields.update(
        {
            "source_module": "file_media_bookkeeping",
            "source_local_path": paths.get("source_local_path"),
            "resolved_relative_path": paths.get("resolved_relative_path"),
            "media_root_key": paths.get("media_root_key"),
            "availability_status": avail,
            "availability_detail": detail,
        }
    )
    return fields


def run_media_consolidation(conn, consolidation_run_id: str) -> dict:
    """Derive media_unified from latest media_source observations.

    Re-evaluates filesystem accessibility on every run so BOOKKEEPING vs
    VERIFIED statuses stay honest on this host. Does not download files.
    """
    clear_dir_cache()
    stats = {"V1": None, "V2": None, "gaps_unresolved_parent": 0, "flush": {}}

    for source_system, project, source_table_filter, pk_namespace in (
        ("V1", _project_v1, "media", "V1:media"),
        ("V2", _project_v2, "file_media_bookkeeping", "V2:file_media_bookkeeping"),
    ):
        # One writer per source so each side can flush+commit independently.
        # A host crash mid-V2 must not lose an already-finished V1 flush.
        writer = UnifiedBatchWriter(conn, "media_unified", "media_id", "media")
        print(
            f"media_consolidate fetch start {source_system}/{source_table_filter}",
            flush=True,
        )
        rows = fetch_latest_by_record_id(
            conn, "media_source", source_system, source_table_filter
        )
        print(
            f"media_consolidate fetch done {source_system}: rows={len(rows)}",
            flush=True,
        )
        for i, (
            source_record_id,
            source_run_id,
            source_created_at,
            source_modified_at,
            payload,
            observation_id,
        ) in enumerate(rows, start=1):
            pk = f"{pk_namespace}:{source_record_id}"
            mapped = project(payload or {})
            if not mapped.get("parent_entity_id"):
                stats["gaps_unresolved_parent"] += 1
                _record_parent_gap(conn, source_system, source_record_id, mapped)
            writer.add(
                pk,
                source_system=source_system,
                source_record_id=str(source_record_id),
                mapped_fields=mapped,
                extra_fields={},
                source_run_id=source_run_id,
                current_as_of=source_modified_at or source_created_at,
                observation_id=observation_id,
            )
            if i % 10000 == 0 or i == len(rows):
                print(
                    f"media_consolidate progress {source_system}: {i}/{len(rows)}",
                    flush=True,
                )
        flush_counts = writer.flush()
        conn.commit()
        print(
            f"media_consolidate flushed {source_system}: {flush_counts}",
            flush=True,
        )
        stats[source_system] = {
            k: flush_counts.get(k, 0)
            for k in ("inserted", "updated", "unchanged", "skipped_no_pk")
        }
        for k, v in flush_counts.items():
            stats["flush"][k] = stats["flush"].get(k, 0) + v

    return stats


def _record_parent_gap(conn, source_system: str, source_record_id: str, mapped: dict):
    gap_type = "unresolved_media_parent"
    gap_key = f"{mapped.get('source_module')}:{source_record_id}"
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO source_gap_ledger
                (source_system, gap_type, gap_key, first_seen_at, status, source_evidence_table)
            VALUES (%s, %s, %s, now(), 'OPEN', 'media_source')
            ON CONFLICT (source_system, gap_type, gap_key) DO NOTHING
            """,
            (source_system, gap_type, gap_key),
        )
