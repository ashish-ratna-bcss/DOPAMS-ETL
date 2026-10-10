#!/usr/bin/env bash
# Start historical AI backfill under nohup. Survives SSH disconnect.
set -euo pipefail
cd /home/eagle/dopams-cctns-ai
mkdir -p logs
# Refuse consolidation/media concurrent writers on the same target.
if pgrep -f 'run_media_consolidation.py|run_phase4_consolidation.py|run_phase5_incremental.py|run_daily_pipeline.py' >/dev/null 2>&1; then
  echo "REFUSE: another ETL-3 writer is running"
  pgrep -af 'run_media_consolidation.py|run_phase4_consolidation.py|run_phase5_incremental.py|run_daily_pipeline.py' || true
  exit 1
fi
# Stop a stuck prior AI backfill (e.g. qwen3 think stall) before relaunch.
if pgrep -f 'run_ai_backfill.py' >/dev/null 2>&1; then
  echo "Stopping prior run_ai_backfill.py ..."
  pkill -f 'run_ai_backfill.py' 2>/dev/null || true
  sleep 2
  pkill -9 -f 'run_ai_backfill.py' 2>/dev/null || true
  sleep 2
fi
if pgrep -f 'run_ai_backfill.py' >/dev/null 2>&1; then
  echo "REFUSE: prior run_ai_backfill.py did not exit"
  pgrep -af 'run_ai_backfill.py' || true
  exit 1
fi
LOG="logs/ai_backfill_$(date -u +%Y%m%dT%H%M%SZ).log"
ln -sfn "$(basename "$LOG")" logs/ai_backfill_latest.log
nohup env \
  ETL3_AI_ENABLED=1 \
  ETL3_AI_MODE=backfill \
  ETL3_AI_LIMIT=0 \
  ETL3_AI_BATCH_SIZE=5 \
  ETL3_AI_MAX_RETRIES=2 \
  ETL3_AI_REQUEST_DELAY_SEC=3 \
  ETL3_AI_HEALTH_COOLDOWN_SEC=60 \
  OLLAMA_THINK=0 \
  LLM_TIMEOUT=180 \
  OLLAMA_BASE_URL=http://10.12.1.124:11434 \
  OLLAMA_MODEL=qwen3:8b \
  PYTHONUNBUFFERED=1 \
  /home/eagle/dopams-cctns-ai/.venv/bin/python etl3/run_ai_backfill.py \
  >"$LOG" 2>&1 &
echo "PID $!"
echo "LOG $LOG"
sleep 3
tail -40 "$LOG"
