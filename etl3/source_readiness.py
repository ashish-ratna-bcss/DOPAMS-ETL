"""Decide whether one ETL-3 incremental pass may start.

V1 is ready only for the latest cycle that started in the current or previous
6-hour slot, and only when that same run_id succeeded with FIR, court,
accused details, and accused in order. An older success is not reused when a
newer cycle failed or is still running. Media is ignored.

V2 is ready only when master_etl.py is not running and LAST_RUN in the V2
env file is on or after the current slot's IST date. That value is written
only after a full successful master_etl.py run. A failed run leaves it unchanged.
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
    """LAST_RUN must move to this cycle's IST date. Older means the full run did not finish."""
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


def judge(
    *,
    now: datetime,
    commands: str,
    cycle: dict | None,
    entities: list[dict],
    v2_last_run: str | None,
    etl3_prior_started_at: datetime | None,
) -> tuple[str, str, datetime]:
    """Return action, reason, and the current 6-hour slot start."""
    slot = _RULES.cycle_slot_start(now)
    floor = _RULES.cycle_boundary(now)
    if ETL3_RUNNING_MARKER in commands:
        return "wait", "ETL-3 incremental is already running", slot
    if any(marker in commands for marker in V1_RUNNING_MARKERS):
        return "wait", "V1 data cycle is still running", slot
    if V2_RUNNING_MARKER in commands:
        return "wait", "V2 master_etl is still running", slot

    ok, reason = _RULES.assess_cycle_marker(cycle, entities, floor)
    if not ok:
        return "wait", reason, slot

    ok, reason = v2_watermark_ready(v2_last_run, slot)
    if not ok:
        return "wait", reason, slot

    finished_at = cycle["finished_at"]
    if etl3_prior_started_at is not None and etl3_prior_started_at >= finished_at:
        return "skip", "ETL-3 already succeeded for this V1 cycle", slot
    return "run", "V1 cycle marker and V2 watermark are both complete", slot
