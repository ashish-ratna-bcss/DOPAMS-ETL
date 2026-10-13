#!/usr/bin/env python3
"""Verify Ollama parallel=2 on dopams-ws and optionally dual-request smoke test."""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

import paramiko

HOST = os.environ.get("WS_HOST", "10.12.1.124")
USER = os.environ.get("WS_USER", "user")
PASS = os.environ.get("WS_PASS", "")
OLLAMA = os.environ.get("OLLAMA_URL", "http://10.12.1.124:11434")


def wsl(client, script: str, timeout=120) -> str:
    sftp = client.open_sftp()
    path = f"C:/Users/{USER}/_ollama_verify.sh"
    with sftp.file(path, "w") as f:
        f.write(script.replace("\r\n", "\n"))
    sftp.chmod(path, 0o700)
    sftp.close()
    wsl_path = f"/mnt/c/Users/{USER}/_ollama_verify.sh"
    cmd = f'wsl -e bash -lc "sed -i \'s/\\r$//\' {wsl_path}; bash {wsl_path}"'
    _i, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    rc = stdout.channel.recv_exit_status()
    print(out, flush=True)
    if err.strip():
        print("STDERR:", err[:2000], flush=True)
    print(f"[rc={rc}]", flush=True)
    return out


def chat_once(label: str, results: dict) -> None:
    body = json.dumps(
        {
            "model": "qwen3:8b",
            "stream": False,
            "format": "json",
            "think": False,
            "messages": [
                {
                    "role": "user",
                    "content": (
                        'Reply with JSON only: {"ok":true,"n":'
                        + str(hash(label) % 100)
                        + "}"
                    ),
                }
            ],
            "options": {"temperature": 0, "num_predict": 32},
        }
    ).encode()
    req = urllib.request.Request(
        f"{OLLAMA.rstrip('/')}/api/chat",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            payload = json.loads(resp.read().decode())
        dt = time.monotonic() - t0
        content = ((payload.get("message") or {}).get("content") or "")[:120]
        results[label] = {"ok": True, "sec": round(dt, 2), "content": content}
    except Exception as exc:
        results[label] = {
            "ok": False,
            "sec": round(time.monotonic() - t0, 2),
            "error": f"{type(exc).__name__}: {exc}",
        }


def main() -> int:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        HOST, username=USER, password=PASS, timeout=30,
        allow_agent=False, look_for_keys=False,
    )
    try:
        # Write sudo pass then dump ollama environ
        script = f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' '{PASS}' > /tmp/.ws_sudo_pass
chmod 600 /tmp/.ws_sudo_pass
echo "=== override.conf ==="
cat /etc/systemd/system/ollama.service.d/override.conf
echo "=== ollama serve environ (sudo) ==="
PID=$(pgrep -n -f 'ollama serve' || true)
echo PID=$PID
if [ -n "$PID" ]; then
  sudo -S tr '\\0' '\\n' < /proc/$PID/environ < /tmp/.ws_sudo_pass | grep -iE 'OLLAMA|CUDA' | sort || true
fi
rm -f /tmp/.ws_sudo_pass
nvidia-smi --query-gpu=index,name,memory.used,utilization.gpu --format=csv
"""
        print("=== LIVE CONFIG ===", flush=True)
        wsl(client, script)
    finally:
        client.close()

    print("\n=== DUAL REQUEST SMOKE (from this machine to :11434) ===", flush=True)
    results: dict = {}
    t0 = time.monotonic()
    threads = [
        threading.Thread(target=chat_once, args=("A", results)),
        threading.Thread(target=chat_once, args=("B", results)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = round(time.monotonic() - t0, 2)
    print(json.dumps({"wall_sec": wall, "results": results}, indent=2), flush=True)
    # If truly parallel, wall ~= max(A,B); if serial, wall ~= A+B
    if all(r.get("ok") for r in results.values()):
        a, b = results["A"]["sec"], results["B"]["sec"]
        print(
            f"Interpretation: wall={wall}s vs sum={a+b:.1f}s max={max(a,b):.1f}s "
            f"({'PARALLEL-ish' if wall < (a + b) * 0.75 else 'SERIAL-ish'})",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
