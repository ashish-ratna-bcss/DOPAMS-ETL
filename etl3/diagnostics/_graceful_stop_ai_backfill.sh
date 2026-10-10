#!/usr/bin/env bash
# SIGTERM the AI backfill so it finishes the current batch, then force if needed.
set -euo pipefail
pids=$(pgrep -f 'run_ai_backfill.py' || true)
if [ -z "$pids" ]; then
  echo "no run_ai_backfill.py"
  exit 0
fi
echo "SIGTERM $pids"
kill -TERM $pids 2>/dev/null || true
for i in 1 2 3 4 5 6 7 8 9 10 11 12; do
  sleep 5
  if ! pgrep -f 'run_ai_backfill.py' >/dev/null 2>&1; then
    echo "stopped gracefully"
    exit 0
  fi
  echo "waiting... ${i}/12"
done
echo "SIGKILL remaining"
pkill -9 -f 'run_ai_backfill.py' 2>/dev/null || true
sleep 1
pgrep -af 'run_ai_backfill.py' || echo none
