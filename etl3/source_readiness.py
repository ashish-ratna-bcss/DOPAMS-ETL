"""Decide whether one ETL-3 incremental pass may start.

V1 and V2 each keep their own fixed 6-hour clock: 00:00, 06:00, 12:00, and
18:00 IST. ETL-3 runs for slot N only when the V1 cycle for N and the V2 cycle
for N have both succeeded. It starts on the next checker pass after the later
of those two finishes. There is no extra delay.

A success from slot N-1 cannot satisfy slot N. A newer failure in a slot
hides an older success in that same slot. Media is ignored. The incremental
loader itself is unchanged.
"""
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
V1_RUNNING_MARKERS = (
    "cctns_v1_daily_cycle",
    "cctns_v1_daily_sync_fir_court_accused_details",
    "cctns_v1_daily_sync_accused_dossier",
    "pipeline_run.py",
)
V2_RUNNING_MARKER = "master_etl.py"
ETL3_RUNNING_MARKER = "run_phase5_incremental.py"
# Written by V2 only after a full successful master_etl.py run.
V2_SUCCESS_TOKEN = "LAST_RUN persisted:"


def _cycle_rules():
    path = (
        Path(__file__).resolve().parent.parent
        / "cctns-v1"
        / "CCTNSV1_DAILY_ETL_RUN"
        / "db"
        / "cycle_rules.py"
    )
    spec = importlib.util.spec_from_file_location("cctns_v1_cycle_rules", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load V1 cycle rules from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_RULES = _cycle_rules()


def cycle_boundary(now: datetime) -> datetime:
    return _RULES.cycle_boundary(now)


def parse_v2_last_run(raw: str | None) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip().strip('"').strip("'")
    if not text:
        return None
    return text[:10]


def v2_watermark_ready(last_run: str | None, boundary: datetime) -> tuple[bool, str]:
    """LAST_RUN must be on or after this slot's IST date.

    The date alone does not identify the slot. Callers also require a
    successful V2 run whose start falls in that same slot.
    """
    parsed = parse_v2_last_run(last_run)
    if parsed is None:
        return False, "V2 LAST_RUN watermark is missing"
    try:
        watermark = datetime.strptime(parsed, "%Y-%m-%d").date()
    except ValueError:
        return False, f"V2 LAST_RUN watermark is not a date ({parsed})"
    expected = boundary.astimezone(IST).date()
    if watermark < expected:
        return False, (
            f"V2 watermark {watermark.isoformat()} is before this cycle date "
            f"{expected.isoformat()}"
        )
    return True, f"V2 watermark {watermark.isoformat()} covers this cycle"


def discover_v2_runs(log_roots: list[Path]) -> list[dict]:
    """Read V2 run directories. Does not change V2.

    Each directory name is the run start in IST (`YYYYMMDD_HHMMSS`). The run
    succeeded only when master.log contains the LAST_RUN persist line, which
    V2 writes after a full successful pass and not after a failed or partial one.
    """
    runs: list[dict] = []
    seen: set[str] = set()
    for root in log_roots:
        if not root.is_dir():
            continue
        for entry in root.iterdir():
            if not entry.is_dir():
                continue
            name = entry.name[:15]
            try:
                started = datetime.strptime(name, "%Y%m%d_%H%M%S").replace(tzinfo=IST)
            except ValueError:
                continue
            log_path = entry / "master.log"
            if not log_path.is_file():
                continue
            key = started.isoformat()
            if key in seen:
                continue
            seen.add(key)
            succeeded = False
            with log_path.open(encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    if V2_SUCCESS_TOKEN in line:
                        succeeded = True
                        break
            runs.append({"started_at": started, "succeeded": succeeded})
    return runs


def _as_ist(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=IST)
    return value.astimezone(IST)


def _latest_v2_for_slot(runs: list[dict], slot: datetime) -> dict | None:
    slot = _RULES.cycle_slot_start(slot)
    chosen = None
    chosen_started = None
    for run in runs:
        started = run.get("started_at")
        if started is None:
            continue
        started = _as_ist(started)
        if _RULES.cycle_slot_start(started) != slot:
            continue
        if chosen_started is None or started >= chosen_started:
            chosen = run
            chosen_started = started
    return chosen


def _v1_process_running(commands: str) -> bool:
    return any(marker in commands for marker in V1_RUNNING_MARKERS)


def _evaluate_slot(
    slot: datetime,
    *,
    commands: str,
    cycles: list[dict],
    entities_by_run_id: dict,
    v2_runs: list[dict],
    v2_last_run: str | None,
    etl3_successes: list[datetime],
) -> tuple[str, str]:
    """One slot: run, skip, wait, blocked, or absent."""
    v1 = _RULES.latest_cycle_for_slot(cycles, slot)
    v2 = _latest_v2_for_slot(v2_runs, slot)
    v1_running = _v1_process_running(commands)
    v2_running = V2_RUNNING_MARKER in commands

    if v1 is None and v2 is None:
        return "absent", "this slot has no V1 or V2 cycle yet"

    if v1 is not None and v1.get("status") == "running":
        return "wait", "V1 data cycle is still running"
    if v1 is not None and v1.get("status") != _RULES.CYCLE_SUCCEEDED:
        return "blocked", f"V1 cycle marker is {v1.get('status')}, not succeeded"
    if v1_running and v1 is None:
        return "wait", "V1 data cycle is still running"

    if v2 is not None and not v2.get("succeeded"):
        if v2_running:
            return "wait", "V2 master_etl is still running"
        return "blocked", "V2 cycle for this slot did not succeed"
    if v2 is None and v2_running:
        return "wait", "V2 master_etl is still running"
    if v1 is None:
        return "wait", "V1 cycle for this slot is missing"
    if v2 is None:
        return "wait", "V2 cycle for this slot has not completed"

    rows = entities_by_run_id.get(str(v1.get("run_id")), [])
    ok, reason = _RULES.assess_cycle_marker(v1, rows, slot)
    if not ok:
        return "wait", reason
    ok, reason = v2_watermark_ready(v2_last_run, slot)
    if not ok:
        return "wait", reason

    finished = _as_ist(v1["finished_at"])
    for started in etl3_successes:
        if _as_ist(started) >= finished:
            return "skip", "ETL-3 already succeeded for this V1 cycle"
    return "run", "V1 and V2 cycles for this slot both succeeded"


def judge(
    *,
    now: datetime,
    commands: str,
    cycles: list[dict],
    entities_by_run_id: dict,
    v2_runs: list[dict],
    v2_last_run: str | None,
    etl3_successes: list[datetime],
) -> tuple[str, str, datetime]:
    """Return action, reason, and the slot that decision belongs to.

    The previous slot is considered first so a late pair is consolidated
    before the next slot. A failed or missing previous slot does not block
    a later pair, and it is not reused as that later pair.
    """
    current = _RULES.cycle_slot_start(now)
    if ETL3_RUNNING_MARKER in commands:
        return "wait", "ETL-3 incremental is already running", current

    previous = current - _RULES.CYCLE_SLOT
    blocked = None
    skipped = None
    for slot in (previous, current):
        action, reason = _evaluate_slot(
            slot,
            commands=commands,
            cycles=cycles,
            entities_by_run_id=entities_by_run_id,
            v2_runs=v2_runs,
            v2_last_run=v2_last_run,
            etl3_successes=etl3_successes,
        )
        if action == "run":
            return "run", reason, slot
        if action == "wait":
            return "wait", reason, slot
        if action == "blocked":
            # Do not run this slot, and do not satisfy a later slot with it.
            blocked = (reason, slot)
        elif action == "skip":
            skipped = (reason, slot)
    if _v1_process_running(commands):
        return "wait", "V1 data cycle is still running", current
    if V2_RUNNING_MARKER in commands:
        return "wait", "V2 master_etl is still running", current
    if blocked is not None:
        return "wait", blocked[0], blocked[1]
    if skipped is not None:
        return "skip", skipped[0], skipped[1]
    return "wait", "no matched V1 and V2 cycle is ready", current
