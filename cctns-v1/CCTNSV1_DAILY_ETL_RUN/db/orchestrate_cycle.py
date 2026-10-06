"""Run one V1 daily cycle under a single lock and a single run_id.

Court and accused details run together only after FIR returns. Accused runs
only after both of those return. The succeeded marker is written only when
the stored entity rows pass the shared cycle rules. A partial CLI run uses
the same lock and run_id and is always stored as incomplete.
"""
from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Callable, Optional

from db.cycle_lock import CycleRunLock
from db.cycle_rules import (
    CYCLE_SUCCEEDED,
    DONE_STATUSES,
    IST,
    assess_entity_rows,
    cycle_boundary,
    cycle_slot_start,
)

EntityRunner = Callable[[str, str], dict]


def _ok(result: dict) -> bool:
    return result.get("status") in DONE_STATUSES


def _summary(run_id: str, status: str, reason: str, entities: dict, boundary: datetime) -> dict:
    return {
        "status": status,
        "run_id": run_id,
        "reason": reason,
        "cycle_start": boundary.isoformat(),
        "entities": entities,
    }


def orchestrate_daily_cycle(
    *,
    execute_entity: EntityRunner,
    open_log: Callable,
    clock: Optional[Callable[[], datetime]] = None,
    lock: Optional[CycleRunLock] = None,
) -> dict:
    """Full data cycle. Does not include media."""
    lock_cm = lock if lock is not None else CycleRunLock()
    with lock_cm:
        cycle_log = open_log()
        now = clock() if clock else datetime.now(IST)
        slot = cycle_slot_start(now)
        floor = cycle_boundary(now)
        run_id = str(uuid.uuid4())
        entities: dict[str, dict] = {}
        try:
            cycle_log.abandon_stale()
            cycle_log.start(run_id, slot)
            fir = execute_entity("fir", run_id)
            entities["fir"] = fir
            if not _ok(fir):
                reason = f"fir did not complete ({fir.get('status')})"
                cycle_log.finish(run_id, "failed", reason)
                return _summary(run_id, "failed", reason, entities, slot)

            with ThreadPoolExecutor(max_workers=2) as pool:
                court_future = pool.submit(execute_entity, "court", run_id)
                details_future = pool.submit(execute_entity, "accused_details", run_id)
                entities["court"] = court_future.result()
                entities["accused_details"] = details_future.result()

            failed_mid = [
                name
                for name in ("court", "accused_details")
                if not _ok(entities[name])
            ]
            if failed_mid:
                reason = "did not complete: " + ",".join(
                    f"{name}={entities[name].get('status')}" for name in failed_mid
                )
                cycle_log.finish(run_id, "failed", reason)
                return _summary(run_id, "failed", reason, entities, slot)

            accused = execute_entity("accused", run_id)
            entities["accused"] = accused
            if not _ok(accused):
                reason = f"accused did not complete ({accused.get('status')})"
                cycle_log.finish(run_id, "failed", reason)
                return _summary(run_id, "failed", reason, entities, slot)

            rows = cycle_log.read_entities(run_id)
            ok, reason = assess_entity_rows(rows, run_id, floor)
            if not ok:
                cycle_log.finish(run_id, "failed", reason)
                return _summary(run_id, "failed", reason, entities, slot)
            cycle_log.finish(run_id, CYCLE_SUCCEEDED, None)
            return _summary(run_id, CYCLE_SUCCEEDED, reason, entities, slot)
        except Exception as exc:
            try:
                cycle_log.finish(run_id, "failed", str(exc))
            except Exception:
                pass
            raise


def orchestrate_partial_cycle(
    entity_names: tuple[str, ...],
    *,
    execute_entity: EntityRunner,
    open_log: Callable,
    clock: Optional[Callable[[], datetime]] = None,
    lock: Optional[CycleRunLock] = None,
) -> dict:
    """CLI subset. Same lock and one run_id. Never writes a success marker."""
    lock_cm = lock if lock is not None else CycleRunLock()
    with lock_cm:
        cycle_log = open_log()
        now = clock() if clock else datetime.now(IST)
        slot = cycle_slot_start(now)
        run_id = str(uuid.uuid4())
        entities: dict[str, dict] = {}
        reason = (
            "partial CLI run is not a V1 cycle; "
            "success marker withheld (" + ",".join(entity_names) + ")"
        )
        try:
            cycle_log.abandon_stale()
            cycle_log.start(run_id, slot)
            for name in entity_names:
                entities[name] = execute_entity(name, run_id)
            cycle_log.finish(run_id, "incomplete", reason)
            return _summary(run_id, "incomplete", reason, entities, slot)
        except Exception as exc:
            try:
                cycle_log.finish(run_id, "failed", str(exc))
            except Exception:
                pass
            raise
