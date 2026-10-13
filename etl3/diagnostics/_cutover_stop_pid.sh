#!/usr/bin/env bash
# Terminate one verified AI backfill PID only. Do not touch PG/Ollama.
set -euo pipefail
PID="${1:?pid required}"
EXPECT_RUN="${2:-}"

if ! ps -p "$PID" >/dev/null 2>&1; then
  echo "PID $PID already gone"
  pgrep -af 'run_ai_backfill.py' || echo 'no backfill processes'
  exit 0
fi

CMD=$(ps -p "$PID" -o cmd=)
echo "target PID=$PID cmd=$CMD"
case "$CMD" in
  *run_ai_backfill.py*) ;;
  *)
    echo "REFUSE: PID $PID is not run_ai_backfill.py"
    exit 1
    ;;
esac

echo "Sending SIGTERM to $PID only"
kill -TERM "$PID"

for i in $(seq 1 36); do
  if ! ps -p "$PID" >/dev/null 2>&1; then
    echo "exited cleanly after ~$((i * 10))s"
    pgrep -af 'run_ai_backfill.py' || echo 'no backfill processes'
    echo '--- last log ---'
    tail -n 20 /home/eagle/dopams-cctns-ai/logs/ai_backfill_20261010T065046Z.log || true
    exit 0
  fi
  echo "waiting $i/36 (10s each)"
  sleep 10
done

echo "REFUSE: PID $PID still running after 6 minutes; not sending SIGKILL automatically"
ps -p "$PID" -o pid,etime,cmd
exit 2
