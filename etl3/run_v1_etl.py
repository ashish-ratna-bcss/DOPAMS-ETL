"""Invoke CCTNS V1 ETL from this repository (writes only to cctns_v1).

Does not start Airflow, PM2, or touch /home/eagle/dopams-cctns.
Reuses the V1 pipeline implementation under cctns-v1/.

Usage (repo root):
    python etl3/run_v1_etl.py --help
    python etl3/run_v1_etl.py --check
    python etl3/run_v1_etl.py --run-cycle   # explicit one-shot; not scheduled here
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
V1_ROOT = REPO / "cctns-v1" / "CCTNSV1_DAILY_ETL_RUN"


def main():
    parser = argparse.ArgumentParser(description="CCTNS V1 ETL entrypoint (cctns-ai checkout)")
    parser.add_argument("--check", action="store_true", help="Verify V1 tree and env only")
    parser.add_argument(
        "--run-cycle",
        action="store_true",
        help="Run one V1 daily cycle via pipeline_run (explicit; not a schedule enable)",
    )
    args = parser.parse_args()

    if not V1_ROOT.is_dir():
        raise SystemExit(f"V1 ETL tree missing: {V1_ROOT}")
    env_path = V1_ROOT / ".env"
    if not env_path.is_file():
        raise SystemExit(f"V1 .env missing: {env_path}")

    print(f"V1_ROOT={V1_ROOT}")
    print(f"V1_ENV={env_path}")
    print("TARGET_DB=cctns_v1 (V1 pipeline only)")
    print("NOTE: Does not write to dopams_cctns_v2 or cctns-v2")

    if args.check and not args.run_cycle:
        print("[OK] V1 entrypoint ready (check only; no ETL started)")
        return

    if not args.run_cycle:
        parser.print_help()
        print("\nRefusing to start V1 ETL without --run-cycle (safety).")
        raise SystemExit(2)

    # Prefer the existing cycle orchestrator if present.
    candidates = [
        V1_ROOT / "db" / "orchestrate_cycle.py",
        V1_ROOT / "dags" / "pipeline_run.py",
    ]
    script = next((p for p in candidates if p.is_file()), None)
    if script is None:
        raise SystemExit("No V1 cycle runner found under cctns-v1/")

    print(f"Starting V1 cycle via {script} ...")
    proc = subprocess.run(
        [sys.executable, "-u", str(script)],
        cwd=str(V1_ROOT),
    )
    raise SystemExit(proc.returncode)


if __name__ == "__main__":
    main()
