"""
Phase 4 driver: computes current-state *_unified tables from the Phase 3
*_source observations already in dopams_cctns. Reads only from
dopams_cctns (never V1/V2 directly -- the observation layer is the only
boundary that ever touched the sources). Writes only to dopams_cctns.

Dependency order: crimes/persons (roots) -> accused/arrests (depend on
crimes, and for V1 cross-link each other) -> everything else (depends only
on crimes, optionally persons).

Usage: python etl3/run_phase4_consolidation.py
"""
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from etl3.db import connections
from etl3.loaders import common
from etl3.merger import current_state as cs
from etl3.merger import field_maps


def _crime_id_v1(payload):
    return {"crime_id": payload.get("fir_reg_num")}


def _crime_id_v2(payload):
    return {"crime_id": payload.get("crime_id")}


def accused_relation_v2(payload, known_person_ids):
    """Link an accused row only when its person_id exists.

    A null person_id stays null. A person_id that is not in persons_unified
    is not guessed and is not written: the caller records it as unresolved.
    """
    person_id = payload.get("person_id") or None
    missing = bool(person_id) and person_id not in known_person_ids
    if missing:
        person_id = None
    return {
        "crime_id": payload.get("crime_id"),
        "person_id": person_id,
        "unlinked_person_flag": person_id is None,
        "missing_person": missing,
    }


# V2 entities merged into unified tables. fsl_case_property is not in this
# list: it stays in fsl_source and is not consolidated.
UNIFIED_V2_ONLY = (
    ("property", "properties_unified", "property_id", "properties_source", "PROPERTIES"),
    ("disposal", "disposal_unified", "disposal_id", "disposal_source", "DISPOSAL"),
)


def run_with_run_log(conn, work):
    """
    Open a consolidation_run_log row, run work, then mark it success.

    work(conn, run_id, progress) -> (sources_processed, rows_observed).
    progress is filled by the work function as steps complete. If work
    raises, the same row is marked failed (after rolling back the aborted
    transaction) and the exception is re-raised. A clean return is never
    marked failed.
    """
    run_id = common.start_consolidation_run(conn)
    conn.commit()
    # A hard stop cannot run the except handler below. The next run closes
    # that leftover row so `running` cannot accumulate across restarts.
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE consolidation_run_log
            SET finished_at = now(), status = 'failed',
                error_message = 'interrupted: run did not finish'
            WHERE status = 'running' AND run_id <> %s
            """,
            (run_id,),
        )
    conn.commit()
    progress = {}
    try:
        sources_processed, rows_observed = work(conn, run_id, progress)
        common.finish_consolidation_run(
            conn, run_id, status="success",
            sources_processed=sources_processed,
            rows_observed=rows_observed,
        )
        conn.commit()
        return run_id
    except Exception as exc:
        try:
            conn.rollback()
            from etl3.sync.cursor import mark_cursors_failed
            mark_cursors_failed(conn)
            common.finish_consolidation_run(
                conn, run_id, status="failed",
                sources_processed={k: "phase4" for k in progress},
                rows_observed=0,
                error_message=f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
            )
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
        raise


def _consolidate(conn, run_id, progress):
    print(f"consolidation_run_id = {run_id}\n")

    results = {}

    def report(name, counts):
        results[name] = counts
        progress[name] = counts
        print(f"{name:28s} {counts}")

    t0 = time.time()

    # --- crimes (root) ---
    report("crimes[V1]", cs.run_entity(conn, entity="crime", unified_table="crimes_unified",
           unified_pk_col="crime_id", source_table="crimes_source", source_system="V1",
           field_map_entry=field_maps.CRIMES["V1"], consolidation_run_id=run_id))
    conn.commit()
    report("crimes[V2]", cs.run_entity(conn, entity="crime", unified_table="crimes_unified",
           unified_pk_col="crime_id", source_table="crimes_source", source_system="V2",
           field_map_entry=field_maps.CRIMES["V2"], consolidation_run_id=run_id))
    conn.commit()

    # --- persons (root; V2 direct, V1 deferred until accused cross-link below) ---
    report("persons[V2]", cs.run_entity(conn, entity="person", unified_table="persons_unified",
           unified_pk_col="person_id", source_table="persons_source", source_system="V2",
           field_map_entry=field_maps.PERSONS["V2"], consolidation_run_id=run_id))
    conn.commit()

    # V1 persons: derived from accused_details (arrests_source), keyed on person_code
    report("persons[V1]", cs.run_entity(conn, entity="person", unified_table="persons_unified",
           unified_pk_col="person_id", source_table="arrests_source", source_system="V1",
           field_map_entry=field_maps.PERSONS["V1"], consolidation_run_id=run_id))
    from etl3.merger.v1_person_keys import record_v1_missing_person_keys
    report("gaps[V1 person key]", record_v1_missing_person_keys(conn))
    conn.commit()

    # --- accused (depends on crimes; V1 grouped + cross-linked to persons) ---
    with conn.cursor() as cur:
        cur.execute("SELECT person_id FROM persons_unified")
        known_person_ids = {row[0] for row in cur.fetchall()}

    def _accused_extra_v2(payload):
        relation = accused_relation_v2(payload, known_person_ids)
        if relation["missing_person"]:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO source_gap_ledger
                        (source_system, gap_type, gap_key, first_seen_at, status, source_evidence_table)
                    VALUES ('V2', 'unresolved_accused_person_link', %s, now(), 'OPEN', 'accused')
                    ON CONFLICT (source_system, gap_type, gap_key) DO NOTHING
                    """,
                    (f"accused_id={payload.get('accused_id')}|person_id={payload.get('person_id')}",),
                )
        return {
            "crime_id": relation["crime_id"],
            "person_id": relation["person_id"],
            "unlinked_person_flag": relation["unlinked_person_flag"],
        }

    v2_accused_counts = cs.run_entity(conn, entity="accused", unified_table="accused_unified",
           unified_pk_col="accused_id", source_table="accused_source", source_system="V2",
           field_map_entry=field_maps.ACCUSED["V2"], consolidation_run_id=run_id,
           extra_fields_fn=_accused_extra_v2)
    report("accused[V2]", v2_accused_counts)
    conn.commit()

    v1_accused_counts, v1_correlation_lookup = cs.run_v1_accused_grouped(conn, consolidation_run_id=run_id)
    report("accused[V1]", v1_accused_counts)
    conn.commit()

    # --- arrests (depends on crimes + accused) ---
    report("arrests[V1]", cs.run_v1_arrests(conn, v1_correlation_lookup, consolidation_run_id=run_id))
    conn.commit()

    # V2 arrests: resolve accused_id via (crime_id, person_id) lookup against accused_unified[V2]
    with conn.cursor() as cur:
        cur.execute("SELECT accused_id, crime_id, person_id FROM accused_unified WHERE source_system='V2'")
        v2_accused_lookup = {(r[1], r[2]): r[0] for r in cur.fetchall() if r[2]}

    def _arrests_extra_v2(payload):
        crime_id = payload.get("crime_id")
        person_id = payload.get("person_id")
        accused_pk = v2_accused_lookup.get((crime_id, person_id))
        return {"crime_id": crime_id, "accused_id": accused_pk}

    report("arrests[V2]", cs.run_entity(conn, entity="arrest", unified_table="arrests_unified",
           unified_pk_col="arrest_id", source_table="arrests_source", source_system="V2",
           field_map_entry=field_maps.ARRESTS["V2"], consolidation_run_id=run_id,
           extra_fields_fn=_arrests_extra_v2))
    conn.commit()

    # Visibility only. Does not change accused_id, person_id, or change_log.
    from etl3.merger.v2_arrest_gaps import (
        record_v2_unresolved_arrest_accused_gaps,
        resolve_arrest_gaps_now_linked,
    )
    report("gaps[V2 arrest->accused]", record_v2_unresolved_arrest_accused_gaps(conn))
    report("gaps[arrest linked]", {"resolved": resolve_arrest_gaps_now_linked(conn)})
    conn.commit()

    # --- chargesheets (depends on crimes only) ---
    report("chargesheets[V1]", cs.run_entity(conn, entity="chargesheet", unified_table="chargesheets_unified",
           unified_pk_col="charge_sheet_id", source_table="chargesheets_source", source_system="V1",
           field_map_entry=field_maps.CHARGESHEETS["V1"], consolidation_run_id=run_id,
           extra_fields_fn=_crime_id_v1))
    conn.commit()
    report("chargesheets[V2:chargesheets]", cs.run_entity(conn, entity="chargesheet", unified_table="chargesheets_unified",
           unified_pk_col="charge_sheet_id", source_table="chargesheets_source", source_system="V2",
           source_table_filter="chargesheets", field_map_entry=field_maps.CHARGESHEETS["V2:chargesheets"],
           consolidation_run_id=run_id, extra_fields_fn=_crime_id_v2))
    conn.commit()
    report("chargesheets[V2:charge_sheet_updates]", cs.run_entity(conn, entity="chargesheet", unified_table="chargesheets_unified",
           unified_pk_col="charge_sheet_id", source_table="chargesheets_source", source_system="V2",
           source_table_filter="charge_sheet_updates", field_map_entry=field_maps.CHARGESHEETS["V2:charge_sheet_updates"],
           consolidation_run_id=run_id, extra_fields_fn=_crime_id_v2))
    conn.commit()

    # --- seizures (depends on crimes only) ---
    report("seizures[V1]", cs.run_entity(conn, entity="seizure", unified_table="seizures_unified",
           unified_pk_col="seizure_id", source_table="accused_source", source_system="V1",
           field_map_entry=field_maps.SEIZURES["V1"], consolidation_run_id=run_id,
           extra_fields_fn=_crime_id_v1))
    conn.commit()
    report("seizures[V2]", cs.run_entity(conn, entity="seizure", unified_table="seizures_unified",
           unified_pk_col="seizure_id", source_table="seizures_source", source_system="V2",
           field_map_entry=field_maps.SEIZURES["V2"], consolidation_run_id=run_id,
           extra_fields_fn=_crime_id_v2))
    conn.commit()

    # --- V2-only entities (depend on crimes only) ---
    # fsl_case_property is intentionally not merged into fsl_unified.
    for entity, table, pk, source_table, fmap_name in UNIFIED_V2_ONLY:
        report(f"{entity}[V2]", cs.run_entity(conn, entity=entity, unified_table=table,
               unified_pk_col=pk, source_table=source_table, source_system="V2",
               field_map_entry=getattr(field_maps, fmap_name)["V2"], consolidation_run_id=run_id,
               extra_fields_fn=_crime_id_v2))
        conn.commit()

    # interrogation: person_id must be validated against persons_unified
    # before being set (V2 has 11 known IR rows pointing at a missing
    # person -- confirmed earlier this project) -- never a blind FK write.
    # known_person_ids was loaded before accused consolidation.
    def _interrogation_extra_v2(payload):
        crime_id = payload.get("crime_id")
        person_id = payload.get("person_id")
        if person_id and person_id not in known_person_ids:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO source_gap_ledger
                        (source_system, gap_type, gap_key, first_seen_at, status, source_evidence_table)
                    VALUES ('V2', 'unresolved_interrogation_person_link', %s, now(), 'OPEN', 'interrogation_reports')
                    ON CONFLICT (source_system, gap_type, gap_key) DO NOTHING
                    """,
                    (f"person_id={person_id}",),
                )
            person_id = None
        return {"crime_id": crime_id, "person_id": person_id}

    report("interrogation[V2]", cs.run_entity(conn, entity="interrogation", unified_table="interrogation_unified",
           unified_pk_col="interrogation_report_id", source_table="interrogation_source", source_system="V2",
           field_map_entry=field_maps.INTERROGATION["V2"], consolidation_run_id=run_id,
           extra_fields_fn=_interrogation_extra_v2))
    conn.commit()

    # --- hierarchy (reference data, no crime_id) ---
    report("hierarchy[V2]", cs.run_entity(conn, entity="hierarchy", unified_table="hierarchy_unified",
           unified_pk_col="ps_code", source_table="hierarchy_source", source_system="V2",
           field_map_entry=field_maps.HIERARCHY["V2"], consolidation_run_id=run_id))
    conn.commit()

    from etl3.merger.ps_enrichment import enrich_v1_ps_codes
    report("ps_code[V1]", enrich_v1_ps_codes(conn))
    conn.commit()

    # --- identity linking (persons only; never auto-confirmed) ---
    from etl3.identity import person_matching as pm
    candidates = pm.generate_candidates(conn)
    identity_result = pm.write_candidates(conn, candidates, run_id)
    report("identity_links[V1<->V2]", identity_result)
    conn.commit()

    # --- consolidation cursor: highest observed source run, never a regression ---
    from etl3.sync.cursor import advance_observed_cursors
    cursor_results = advance_observed_cursors(conn)
    conn.commit()
    print("consolidation_cursor:", cursor_results)

    dt = time.time() - t0
    total_changed = sum(c.get("inserted", 0) + c.get("updated", 0) for c in results.values())
    print(f"\nTotal inserted+updated unified rows: {total_changed}  ({dt:.1f}s)")
    return {k: "phase4" for k in results}, total_changed


def main():
    conn = connections.get_unified_connection()
    try:
        run_id = run_with_run_log(conn, _consolidate)
        print(f"consolidation_run_id {run_id} marked success.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
