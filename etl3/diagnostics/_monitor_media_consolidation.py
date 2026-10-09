"""Monitor media consolidation run."""
from __future__ import annotations

import subprocess
from pathlib import Path

log = Path("/home/eagle/dopams-cctns-ai/logs/media_consolidation.log")
ps = subprocess.check_output(
    "ps aux | grep run_media_consolidation | grep -v grep || true",
    shell=True,
    text=True,
)
print("PROCESSES:")
print(ps or "(none)")
print(f"log_exists={log.exists()} bytes={log.stat().st_size if log.exists() else 0}")
if log.exists():
    print("--- TAIL ---")
    print("\n".join(log.read_text(errors="replace").splitlines()[-40:]))
