#!/usr/bin/env bash
# Observe new per-FIR backfill for interleaving evidence.
set -euo pipefail
cd /home/eagle/dopams-cctns-ai
LOG=${1:-logs/ai_backfill_latest.log}
PID=${2:-}

echo "LOG=$LOG"
if [ -n "$PID" ]; then
  ps -p "$PID" -o pid,etime,cmd || { echo 'PID missing'; exit 1; }
fi
pgrep -af 'run_ai_backfill.py' || true

echo '=== waiting for per-fir banner / run_id ==='
for i in $(seq 1 60); do
  if grep -q 'ai-per-fir' "$LOG" 2>/dev/null; then
    echo "banner seen at iter $i"
    break
  fi
  sleep 5
done

echo '=== head / key lines ==='
grep -E 'schedule=|run_id=|ai-per-fir|ollama|drug processed|accused processed|per-FIR|===== ' "$LOG" | head -80

echo '=== recent tail ==='
tail -n 40 "$LOG"

# Extract run_id if present
python3 - <<PY
import re
from pathlib import Path
text = Path("$LOG").read_text(errors="replace")
m = re.search(r"run_id=([0-9a-f-]{36})", text)
print("RUN_ID", m.group(1) if m else "PENDING")
PY
