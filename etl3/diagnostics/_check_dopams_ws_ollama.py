#!/usr/bin/env python3
"""One-off RO check of Ollama on dopams-ws WSL. Password via env WS_PASS only."""
from __future__ import annotations

import os
import sys

import paramiko

HOST = os.environ.get("WS_HOST", "10.12.1.124")
USER = os.environ.get("WS_USER", "user")
PASS = os.environ.get("WS_PASS", "")


def run(client: paramiko.SSHClient, cmd: str, timeout: int = 90) -> None:
    print(f"\n=== CMD: {cmd}", flush=True)
    _stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    if out:
        print(out, end="" if out.endswith("\n") else "\n", flush=True)
    if err.strip():
        print("STDERR:", err[:4000], flush=True)


def main() -> int:
    if not PASS:
        print("Set WS_PASS env var", file=sys.stderr)
        return 2
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
    try:
        run(client, "whoami & hostname & ver")
        run(client, "wsl -l -v")
        # Use a single-quoted PowerShell-unfriendly remote cmd carefully via cmd.exe
        remote = (
            "wsl -e bash -lc "
            "\""
            "echo HOST=$(hostname); "
            "which ollama; ollama --version; "
            "echo '--- GPUS ---'; "
            "nvidia-smi -L; "
            "nvidia-smi --query-gpu=index,name,memory.total,utilization.gpu,utilization.memory --format=csv; "
            "echo '--- OLLAMA PROCS ---'; "
            "ps -ef | grep -i '[o]llama' || true; "
            "echo '--- SERVICE ---'; "
            "systemctl is-active ollama 2>/dev/null || service ollama status 2>/dev/null | head -20 || true; "
            "echo '--- ENV ---'; "
            "echo OLLAMA_NUM_PARALLEL=$OLLAMA_NUM_PARALLEL; "
            "echo OLLAMA_MAX_LOADED_MODELS=$OLLAMA_MAX_LOADED_MODELS; "
            "echo CUDA_VISIBLE_DEVICES=$CUDA_VISIBLE_DEVICES; "
            "env | grep -iE 'OLLAMA|CUDA|NVIDIA' | sort || true; "
            "echo '--- MODELS ---'; "
            "ollama list 2>/dev/null || true; "
            "echo '--- LISTEN ---'; "
            "ss -lntp 2>/dev/null | grep -E '11434|ollama' || netstat -lntp 2>/dev/null | grep 11434 || true; "
            "\""
        )
        run(client, remote, timeout=120)
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
