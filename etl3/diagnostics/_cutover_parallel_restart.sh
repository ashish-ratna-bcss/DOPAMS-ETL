#!/usr/bin/env bash
# On dopams (/home/eagle/dopams-cctns-ai): pull ETL3_AI_PARALLEL support,
# ensure .env has PARALLEL=2 for current 2× T1000, restart backfill.
set -euo pipefail
cd /home/eagle/dopams-cctns-ai

echo "=== graceful stop ==="
bash etl3/diagnostics/_graceful_stop_ai_backfill.sh || true

echo "=== pull origin/cctns-ai ==="
git fetch origin cctns-ai
git pull --ff-only origin cctns-ai
echo "HEAD=$(git rev-parse --short HEAD)"
grep -q 'ETL3_AI_PARALLEL' etl3/config/settings.py
grep -q 'configure_ollama_parallel' etl3/enrichment/ai.py
grep -q 'ETL3_AI_PARALLEL' etl3/diagnostics/_start_ai_backfill.sh

echo "=== ensure .env PARALLEL=2 ==="
.venv/bin/python etl3/diagnostics/_ensure_ai_env.py | grep ETL3_AI_PARALLEL

echo "=== config smoke ==="
.venv/bin/python - <<'PY'
from etl3.config.settings import resolve_ai_settings
from etl3.enrichment.ai import configure_ollama_parallel
s = resolve_ai_settings()
n = configure_ollama_parallel(s["parallel"])
assert n >= 1, n
print("parallel", n, "host", s["host"], "model", s["model"], "mode", s["mode"])
PY

echo "=== start backfill ==="
bash etl3/diagnostics/_start_ai_backfill.sh
echo "=== expect log lines: client_parallel=2 and parallel=2 ==="
sleep 5
tail -n 60 logs/ai_backfill_latest.log
