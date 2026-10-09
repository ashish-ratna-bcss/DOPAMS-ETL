"""pg_dump safety backup of dopams_cctns_v2 before remediation."""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "etl3"))

from config import settings

TARGET = "dopams_cctns_v2"


def main():
    if settings.EXPECTED_UNIFIED_DBNAME != TARGET:
        raise SystemExit(f"refusing backup of {settings.EXPECTED_UNIFIED_DBNAME}")
    cfg = settings.UNIFIED_DB
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path.home() / "backups" / "dopams_cctns_v2"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"dopams_cctns_v2_pre_gap_remediation_{stamp}.dump"
    env = os.environ.copy()
    env["PGPASSWORD"] = cfg["password"]
    cmd = [
        "pg_dump", "-h", cfg["host"], "-p", str(cfg["port"]), "-U", cfg["user"],
        "-d", TARGET, "-Fc", "-f", str(out_path),
    ]
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stderr[:2000])
        raise SystemExit(proc.returncode)
    size = out_path.stat().st_size
    if size < 1024:
        raise SystemExit(f"backup too small: {size}")
    # verify readable header
    proc2 = subprocess.run(
        ["pg_restore", "-l", str(out_path)],
        capture_output=True, text=True,
    )
    ok = proc2.returncode == 0 and len(proc2.stdout.splitlines()) > 10
    print(f"BACKUP_OK path={out_path} bytes={size} listable={ok}")
    if not ok:
        raise SystemExit("backup not verifiable via pg_restore -l")


if __name__ == "__main__":
    main()
