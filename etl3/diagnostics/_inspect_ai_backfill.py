"""Inspect active AI backfill process, env, log, Ollama, and backlog."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.db.connections import get_unified_connection
from etl3.run_ai_backfill import print_status


def _sh(cmd: str) -> str:
    return subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.STDOUT)


def main() -> None:
    print("BRANCH:", _sh("cd /home/eagle/dopams-cctns-ai && git rev-parse --abbrev-ref HEAD && git log -1 --oneline").strip())
    ps = _sh("ps aux | grep run_ai_backfill.py | grep -v grep || true").strip()
    print("PROCESS:\n", ps or "(none)")
    pids = _sh("pgrep -f 'run_ai_backfill.py' || true").strip().split()
    for pid in pids:
        if not pid.isdigit():
            continue
        print(f"PID {pid}")
        print(_sh(f"ps -p {pid} -o pid,etime,stat,%cpu,rss,cmd").strip())
        env_path = Path(f"/proc/{pid}/environ")
        if env_path.exists():
            env = env_path.read_bytes().split(b"\0")
            keys = []
            for item in env:
                if not item:
                    continue
                s = item.decode("utf-8", "replace")
                if s.startswith(("OLLAMA_", "ETL3_AI_", "LLM_")):
                    keys.append(s)
            print("PROC_ENV:")
            for k in sorted(keys):
                print(" ", k)
    latest = Path("/home/eagle/dopams-cctns-ai/logs/ai_backfill_latest.log")
    if latest.exists() or latest.is_symlink():
        target = latest.resolve()
        print(f"LOG {target} bytes={target.stat().st_size}")
        print("--- TAIL ---")
        print("\n".join(target.read_text(errors="replace").splitlines()[-50:]))
    # Ollama
    try:
        with urllib.request.urlopen("http://10.12.1.124:11434/api/ps", timeout=10) as r:
            print("OLLAMA_PS:", r.read().decode()[:800])
    except Exception as exc:
        print("OLLAMA_PS_FAIL:", exc)
    # GPU via ssh if possible
    try:
        gpu = _sh(
            "ssh -o ConnectTimeout=5 -o BatchMode=yes -o StrictHostKeyChecking=accept-new "
            "eagle@10.12.1.124 "
            "'nvidia-smi --query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu "
            "--format=csv 2>/dev/null || echo NO_NVIDIA_SMI' 2>&1"
        )
        print("GPU_REMOTE:\n", gpu)
    except Exception as exc:
        print("GPU_REMOTE_FAIL:", exc)
    # Also try from ETL host itself
    try:
        local = _sh("nvidia-smi --query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu --format=csv 2>&1 || true")
        print("GPU_LOCAL:\n", local)
    except Exception as exc:
        print("GPU_LOCAL_FAIL:", exc)
    conn = get_unified_connection(readonly=True)
    try:
        print_status(conn)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM kb.drug_categories")
        print("kb_drug_categories", cur.fetchone()[0])
        cur.execute(
            """
            SELECT status, COALESCE(validation_status,''), COUNT(*)
            FROM ai_extraction_attempts GROUP BY 1,2 ORDER BY 1,2
            """
        )
        print("attempts", cur.fetchall())
        cur.execute("SELECT COUNT(*) FROM drug_extractions WHERE provenance='etl3_ai'")
        print("ai_drugs", cur.fetchone()[0])
        cur.execute("SELECT MAX(created_at) FROM ai_extraction_attempts")
        print("latest_attempt", cur.fetchone()[0])
    finally:
        conn.close()


if __name__ == "__main__":
    main()
