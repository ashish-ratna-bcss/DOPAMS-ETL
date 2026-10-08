"""Run ETL-3 only on dopams_cctns_v2: Phase3 → Phase4 → enrichment.

Does not start V1/V2 dump ETLs. Reads V1/V2 read-only; writes only the
configured unified DB (dopams_cctns_v2).
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STEPS = (
    "etl3/run_phase3_initial_load.py",
    "etl3/run_phase4_consolidation.py",
    "etl3/run_enrichment.py",
)


def main():
    for step in STEPS:
        print(f"\n===== {step} =====\n", flush=True)
        proc = subprocess.run([sys.executable, "-u", str(ROOT / step)], cwd=str(ROOT))
        if proc.returncode != 0:
            raise SystemExit(f"{step} failed with code {proc.returncode}")
    print("\n[OK] ETL-3 fresh pipeline finished (phase3 + phase4 + enrichment)")


if __name__ == "__main__":
    main()
