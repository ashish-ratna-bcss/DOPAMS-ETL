"""Invoke CCTNS V2 ETL from this repository (writes only to cctns-v2).

Does not start Airflow or touch /home/eagle/dopams-cctns.
Reuses cctns-v2/etl_master/master_etl.py.

Usage (repo root):
    python etl3/run_v2_etl.py --check
    python etl3/run_v2_etl.py --run   # explicit one-shot; not scheduled here
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
V2_ROOT = REPO / "cctns-v2"
MASTER = V2_ROOT / "etl_master" / "master_etl.py"


def main():
    parser = argparse.ArgumentParser(description="CCTNS V2 ETL entrypoint (cctns-ai checkout)")
    parser.add_argument("--check", action="store_true", help="Verify V2 tree and env only")
    parser.add_argument(
        "--run",
        action="store_true",
        help="Run master_etl.py once (explicit; not a schedule enable)",
    )
    args = parser.parse_args()

    if not MASTER.is_file():
        raise SystemExit(f"V2 master ETL missing: {MASTER}")
    env_path = V2_ROOT / ".env"
    if not env_path.is_file():
        raise SystemExit(f"V2 .env missing: {env_path}")

    print(f"V2_ROOT={V2_ROOT}")
    print(f"V2_ENV={env_path}")
    print(f"V2_MASTER={MASTER}")
    print("TARGET_DB=cctns-v2 (V2 pipeline only)")
    print("NOTE: Does not write to dopams_cctns_v2 or cctns_v1")

    if args.check and not args.run:
        print("[OK] V2 entrypoint ready (check only; no ETL started)")
        return

    if not args.run:
        parser.print_help()
        print("\nRefusing to start V2 ETL without --run (safety).")
        raise SystemExit(2)

    print(f"Starting V2 master ETL via {MASTER} ...")
    proc = subprocess.run(
        [sys.executable, "-u", str(MASTER)],
        cwd=str(V2_ROOT),
    )
    raise SystemExit(proc.returncode)


if __name__ == "__main__":
    main()
