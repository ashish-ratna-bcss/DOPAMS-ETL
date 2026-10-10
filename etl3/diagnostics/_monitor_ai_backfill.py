"""Monitor AI backfill process health, log tail, backlog, and GPU if reachable."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.db.connections import get_unified_connection
from etl3.run_ai_backfill import print_status


def main() -> None:
    ps = subprocess.check_output(
        "ps aux | grep -E '[r]un_ai_backfill.py' || true",
        shell=True,
        text=True,
    )
    print("PROCESS:")
    print(ps.strip() or "(none)")
    if ps.strip():
        env_lines = []
        for line in ps.splitlines():
            m = re.search(r"\b(\d+)\b", line)
            if not m:
                continue
            pid = m.group(1)
            env_path = Path(f"/proc/{pid}/environ")
            if env_path.exists():
                for item in env_path.read_bytes().split(b"\0"):
                    s = item.decode("utf-8", "replace")
                    if s.startswith(("ETL3_AI_BATCH", "ETL3_AI_REQUEST", "OLLAMA_THINK", "LLM_TIMEOUT")):
                        env_lines.append(s)
        if env_lines:
            print("KEY_ENV:", ", ".join(sorted(set(env_lines))))
    latest = Path("/home/eagle/dopams-cctns-ai/logs/ai_backfill_latest.log")
    if latest.exists() or latest.is_symlink():
        target = latest.resolve() if latest.is_symlink() else latest
        print(f"LOG: {target} bytes={target.stat().st_size if target.exists() else 0}")
        if target.exists():
            lines = target.read_text(errors="replace").splitlines()
            print("--- TAIL ---")
            print("\n".join(lines[-50:]))
            run_ids = [ln for ln in lines if "run_id=" in ln]
            if run_ids:
                print("RUN_ID_LINE:", run_ids[-1])
    df = subprocess.check_output("df -h / /home 2>/dev/null | head -5", shell=True, text=True)
    print("DISK:\n", df)
    try:
        import urllib.request

        with urllib.request.urlopen("http://10.12.1.124:11434/api/tags", timeout=10) as r:
            print("OLLAMA_TAGS: ok", r.status)
        with urllib.request.urlopen("http://10.12.1.124:11434/api/ps", timeout=10) as r:
            print("OLLAMA_PS:", r.read().decode()[:500])
    except Exception as exc:
        print("OLLAMA: FAIL", exc)
    try:
        gpu = subprocess.check_output(
            "ssh -o ConnectTimeout=5 -o BatchMode=yes eagle@10.12.1.124 "
            "\"nvidia-smi --query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu "
            "--format=csv\" 2>&1",
            shell=True,
            text=True,
        )
        print("GPU_REMOTE:\n", gpu)
    except Exception as exc:
        print("GPU_REMOTE: unavailable from ETL host —", exc)
    conn = get_unified_connection(readonly=True)
    try:
        print_status(conn)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT status, COALESCE(validation_status,''), COUNT(*)
            FROM ai_extraction_attempts
            GROUP BY 1,2 ORDER BY 1,2
            """
        )
        print("attempts:", json.dumps(cur.fetchall(), default=str))
        cur.execute("SELECT COUNT(*) FROM kb.drug_categories")
        print("kb_drug_categories", cur.fetchone()[0])
        cur.execute("SELECT MAX(created_at) FROM ai_extraction_attempts")
        print("latest_attempt", cur.fetchone()[0])
    finally:
        conn.close()


if __name__ == "__main__":
    main()
