#!/usr/bin/env python3
"""Configure Ollama on dopams-ws WSL for 2 parallel workers (2x T1000).

Env:
  WS_HOST (default 10.12.1.124)
  WS_USER (default user)
  WS_PASS (required)
  WS_APPLY=1 to write systemd drop-in and restart; otherwise inspect only.
"""
from __future__ import annotations

import os
import sys
import time

import paramiko

HOST = os.environ.get("WS_HOST", "10.12.1.124")
USER = os.environ.get("WS_USER", "user")
PASS = os.environ.get("WS_PASS", "")
APPLY = os.environ.get("WS_APPLY", "0") == "1"

INSPECT_SH = r"""#!/usr/bin/env bash
set -euo pipefail
echo "=== host ==="
hostname
echo "=== ollama version ==="
ollama --version
echo "=== gpus ==="
nvidia-smi -L
nvidia-smi --query-gpu=index,name,memory.total,memory.used,utilization.gpu --format=csv
echo "=== systemd unit ==="
systemctl cat ollama 2>/dev/null | head -100 || true
echo "=== drop-ins ==="
ls -la /etc/systemd/system/ollama.service.d 2>/dev/null || echo '(none)'
for f in /etc/systemd/system/ollama.service.d/*.conf; do
  [ -f "$f" ] || continue
  echo "FILE=$f"
  cat "$f"
done
echo "=== process env ==="
PID=$(pgrep -n -f 'ollama serve' || true)
echo "PID=$PID"
if [ -n "${PID}" ] && [ -r "/proc/$PID/environ" ]; then
  tr '\0' '\n' < "/proc/$PID/environ" | grep -iE 'OLLAMA|CUDA' | sort || true
fi
echo "=== listen ==="
ss -lntp 2>/dev/null | grep 11434 || true
"""

APPLY_SH = r"""#!/usr/bin/env bash
set -euo pipefail
PASS_FILE=/tmp/.ws_sudo_pass
# Password injected by caller into this file with mode 600, then removed.
SUDO="sudo -n"
if ! sudo -n true 2>/dev/null; then
  if [ -f "$PASS_FILE" ]; then
    SUDO="sudo -S"
  else
    echo "Need passwordless sudo or PASS_FILE"; exit 1
  fi
fi

run_sudo() {
  if [ "$SUDO" = "sudo -S" ]; then
    "$SUDO" "$@" < "$PASS_FILE"
  else
    "$SUDO" "$@"
  fi
}

OVERRIDE_DIR=/etc/systemd/system/ollama.service.d
OVERRIDE=$OVERRIDE_DIR/parallel.conf
echo "Writing $OVERRIDE"
run_sudo mkdir -p "$OVERRIDE_DIR"
cat <<'EOF' | run_sudo tee "$OVERRIDE" >/dev/null
[Service]
# Two concurrent chat completions — one per NVIDIA T1000 8GB.
# qwen3:8b (~5.2GB) fits a single card, so parallel=2 can use both GPUs.
Environment="OLLAMA_NUM_PARALLEL=2"
Environment="OLLAMA_MAX_LOADED_MODELS=2"
Environment="OLLAMA_HOST=0.0.0.0:11434"
EOF

run_sudo systemctl daemon-reload
run_sudo systemctl restart ollama
sleep 3
systemctl is-active ollama
PID=$(pgrep -n -f 'ollama serve' || true)
echo "PID=$PID"
if [ -n "${PID}" ] && [ -r "/proc/$PID/environ" ]; then
  echo "=== process env after restart ==="
  tr '\0' '\n' < "/proc/$PID/environ" | grep -iE 'OLLAMA|CUDA' | sort || true
fi
echo "=== api tags ==="
curl -sf http://127.0.0.1:11434/api/tags | head -c 500; echo
echo "=== gpus ==="
nvidia-smi --query-gpu=index,name,memory.used,utilization.gpu --format=csv
rm -f "$PASS_FILE"
echo APPLY_OK
"""


def ssh_connect() -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        HOST,
        username=USER,
        password=PASS,
        timeout=30,
        allow_agent=False,
        look_for_keys=False,
    )
    return client


def wsl_bash(client: paramiko.SSHClient, script: str, timeout: int = 180) -> str:
    """Upload script to Windows home, run via WSL with LF endings."""
    sftp = client.open_sftp()
    remote_win = f"C:/Users/{USER}/_ollama_cfg_tmp.sh"
    # Also place password file if applying
    with sftp.file(remote_win, "w") as f:
        f.write(script.replace("\r\n", "\n"))
    sftp.chmod(remote_win, 0o700)
    sftp.close()
    # Translate to /mnt/c/Users/...
    wsl_path = f"/mnt/c/Users/{USER}/_ollama_cfg_tmp.sh"
    cmd = f'wsl -e bash -lc "sed -i \'s/\\r$//\' {wsl_path}; bash {wsl_path}"'
    print(f"\n=== run via WSL ===", flush=True)
    _stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    rc = stdout.channel.recv_exit_status()
    print(out, end="" if out.endswith("\n") else "\n", flush=True)
    if err.strip():
        print("STDERR:", err[:4000], flush=True)
    print(f"[rc={rc}]", flush=True)
    return out


def main() -> int:
    if not PASS:
        print("Set WS_PASS", file=sys.stderr)
        return 2
    client = ssh_connect()
    try:
        print("=== INSPECT ===", flush=True)
        wsl_bash(client, INSPECT_SH, timeout=120)
        if not APPLY:
            print("\nDry-run complete. Re-run with WS_APPLY=1 to enable parallel=2.", flush=True)
            return 0
        # Write sudo password file inside WSL via a tiny prelude script
        prelude = (
            "#!/usr/bin/env bash\n"
            "umask 077\n"
            f"printf '%s\\n' '{PASS}' > /tmp/.ws_sudo_pass\n"
            "chmod 600 /tmp/.ws_sudo_pass\n"
        )
        wsl_bash(client, prelude + "\n" + APPLY_SH, timeout=180)
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
