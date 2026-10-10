"""Timed Ollama chat ping (does not affect backfill)."""
from __future__ import annotations

import json
import time
import urllib.request

HOST = "http://10.12.1.124:11434"
MODEL = "qwen3:8b"


def main() -> None:
    body = json.dumps(
        {
            "model": MODEL,
            "stream": False,
            "format": "json",
            "messages": [{"role": "user", "content": 'Reply with JSON only: {"drugs":[]}'}],
            "options": {"temperature": 0, "num_predict": 64},
        }
    ).encode()
    req = urllib.request.Request(
        HOST + "/api/chat",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            payload = json.loads(resp.read().decode())
        dt = time.time() - t0
        content = (payload.get("message") or {}).get("content")
        preview = repr(content)[:200]
        print(f"ok elapsed_s={dt:.1f} content={preview}")
    except Exception as exc:
        print(f"fail elapsed_s={time.time()-t0:.1f} err={exc}")


if __name__ == "__main__":
    main()
