"""Rules for one CCTNS V1 daily cycle.

No database or Airflow imports. ETL-3 loads this file directly so the writer
and the readiness gate share one definition of a finished cycle.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))

# FIR, then court and accused details, then accused. Media is not part of this cycle.
REQUIRED_ENTITIES = ("fir", "court", "accused_details", "accused")
DONE_STATUSES = frozenset({"loaded", "loaded_with_known_gaps"})
CYCLE_SUCCEEDED = "succeeded"


# Fixed clock copied from the live V2 crontab (`0 */6 * * *` UTC). V2 itself
# is not changed. Slots are 05:30, 11:30, 17:30, and 23:30 IST. A late finish
# does not move the next slot.
CYCLE_SLOT = timedelta(hours=6)
_SLOT_HOURS = (5, 11, 17, 23)
_SLOT_MINUTE = 30


def _as_ist_now(now: datetime) -> datetime:
    if now.tzinfo is None:
        now = now.replace(tzinfo=IST)
    else:
        now = now.astimezone(IST)
    return now


def cycle_slot_start(now: datetime) -> datetime:
    """Start of the fixed 6-hour slot that contains `now`.

    The grid is 05:30, 11:30, 17:30, 23:30 IST, the same instants as V2's
    live `0 */6 * * *` UTC cron. Completion time does not move the next boundary.
    """
    now = _as_ist_now(now)
    candidate = None
    for hour in _SLOT_HOURS:
        slot = now.replace(hour=hour, minute=_SLOT_MINUTE, second=0, microsecond=0)
        if slot <= now:
            candidate = slot
    if candidate is None:
        # Before 05:30, the open slot started at 23:30 the previous evening.
        candidate = (now - timedelta(days=1)).replace(
            hour=23, minute=_SLOT_MINUTE, second=0, microsecond=0
        )
    return candidate


def cycle_boundary(now: datetime) -> datetime:
    """Earliest start still accepted for the latest cycle.

    An accused pull can run for about 8 hours, so a cycle that started in the
    previous 6-hour slot may still be the latest finished cycle. Anything
    older than that slot is not this cadence's cycle.
    """
    return cycle_slot_start(now) - CYCLE_SLOT


def _as_ist(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("cycle timestamps must be timezone-aware")
    return value.astimezone(IST)


def assess_entity_rows(rows: list[dict], run_id: str, boundary: datetime) -> tuple[bool, str]:
    """True only when the four required entities share run_id and ran in order."""
    boundary = _as_ist(boundary)
    by_entity: dict[str, dict] = {}
    for row in rows:
        entity = row.get("entity")
        if entity not in REQUIRED_ENTITIES:
            continue
        if entity in by_entity:
            return False, f"V1 entity {entity} has more than one row for this cycle"
        by_entity[entity] = row

    missing = [entity for entity in REQUIRED_ENTITIES if entity not in by_entity]
    if missing:
        return False, "V1 cycle is missing " + ",".join(missing)

    for entity in REQUIRED_ENTITIES:
        row = by_entity[entity]
        if str(row.get("run_id")) != str(run_id):
            return False, f"V1 {entity} does not share cycle run_id {run_id}"
        status = row.get("status")
        if status not in DONE_STATUSES:
            return False, f"V1 {entity} is not a successful load ({status})"
        started = row.get("started_at")
        finished = row.get("finished_at")
        if started is None or finished is None:
            return False, f"V1 {entity} has no start or finish time"
        try:
            started = _as_ist(started)
            finished = _as_ist(finished)
        except ValueError as exc:
            return False, str(exc)
        if started < boundary:
            return False, f"V1 {entity} started before this 6-hour slot"
        if finished < started:
            return False, f"V1 {entity} finished before it started"
        row["_started"] = started
        row["_finished"] = finished

    fir = by_entity["fir"]
    court = by_entity["court"]
    details = by_entity["accused_details"]
    accused = by_entity["accused"]
    if court["_started"] < fir["_finished"]:
        return False, "V1 court started before FIR finished"
    if details["_started"] < fir["_finished"]:
        return False, "V1 accused_details started before FIR finished"
    if accused["_started"] < court["_finished"]:
        return False, "V1 accused started before court finished"
    if accused["_started"] < details["_finished"]:
        return False, "V1 accused started before accused_details finished"
    return True, "V1 entities completed in order"


def assess_cycle_marker(cycle: dict | None, rows: list[dict], boundary: datetime) -> tuple[bool, str]:
    """True only for a succeeded marker whose entity rows pass assess_entity_rows."""
    if not cycle:
        return False, "V1 cycle-success marker is missing"
    boundary = _as_ist(boundary)
    if cycle.get("status") != CYCLE_SUCCEEDED:
        return False, f"V1 cycle marker is {cycle.get('status')}, not succeeded"
    run_id = cycle.get("run_id")
    if not run_id:
        return False, "V1 cycle marker has no run_id"
    started = cycle.get("started_at")
    finished = cycle.get("finished_at")
    cycle_start = cycle.get("cycle_start")
    if started is None or finished is None or cycle_start is None:
        return False, "V1 cycle marker is missing started_at, finished_at, or cycle_start"
    try:
        started = _as_ist(started)
        finished = _as_ist(finished)
        cycle_start = _as_ist(cycle_start)
    except ValueError as exc:
        return False, str(exc)
    if cycle_start < boundary or started < boundary:
        return False, "V1 cycle started before this 6-hour slot"
    if finished < started:
        return False, "V1 cycle marker finished before it started"
    return assess_entity_rows(rows, str(run_id), boundary)


def pick_latest_cycle(cycles: list[dict], floor: datetime) -> dict | None:
    """Latest cycle that started on or after `floor`. Status is not filtered.

    A newer failed or running row must win over an older success so entity
    rows from two run_ids cannot be combined.
    """
    floor = _as_ist(floor)
    eligible: list[dict] = []
    for cycle in cycles:
        started = cycle.get("started_at")
        if started is None:
            continue
        try:
            started = _as_ist(started)
        except ValueError:
            continue
        if started >= floor:
            eligible.append(cycle)
    if not eligible:
        return None
    return max(eligible, key=lambda cycle: _as_ist(cycle["started_at"]))


def latest_cycle_for_slot(cycles: list[dict], slot: datetime) -> dict | None:
    """Latest cycle whose own start falls in `slot`. Status is not filtered.

    A newer failure in this slot wins over an older success in this slot.
    A success from another slot is not eligible.
    """
    slot = cycle_slot_start(slot)
    chosen = None
    chosen_started = None
    for cycle in cycles:
        anchor = cycle.get("cycle_start") or cycle.get("started_at")
        started = cycle.get("started_at")
        if anchor is None or started is None:
            continue
        try:
            anchor_ist = _as_ist(anchor)
            started_ist = _as_ist(started)
        except ValueError:
            continue
        if cycle_slot_start(anchor_ist) != slot:
            continue
        if chosen_started is None or started_ist >= chosen_started:
            chosen = cycle
            chosen_started = started_ist
    return chosen
