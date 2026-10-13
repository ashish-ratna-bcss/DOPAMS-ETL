#!/usr/bin/env bash
set -euo pipefail
cd /home/eagle/dopams-cctns-ai
PY=/home/eagle/dopams-cctns-ai/.venv/bin/python
export PYTHONPATH=/home/eagle/dopams-cctns-ai
export PYTHONUNBUFFERED=1

echo '=== unit tests ==='
$PY etl3/tests/test_per_fir_ai_scheduler.py
$PY etl3/tests/test_ollama_config.py

echo '=== status (deployed) ==='
# Ensure production AI env for status reporting
export ETL3_AI_ENABLED=1
export ETL3_AI_MODE=backfill
export ETL3_AI_LIMIT=0
export ETL3_AI_BATCH_SIZE=5
export ETL3_AI_MAX_RETRIES=2
export ETL3_AI_REQUEST_DELAY_SEC=3
export ETL3_AI_HEALTH_COOLDOWN_SEC=60
export OLLAMA_THINK=0
export LLM_TIMEOUT=180
export OLLAMA_BASE_URL=http://10.12.1.124:11434
export OLLAMA_MODEL=qwen3:8b

$PY etl3/run_ai_backfill.py --status | tee /tmp/ai_backfill_postdeploy_status.json

echo '=== resolved settings check ==='
$PY - <<'PY'
from etl3.enrichment.ai import ai_settings
from etl3.config import settings as cfg
s = ai_settings()
assert s["host"] == "http://10.12.1.124:11434", s
assert s["model"] == "qwen3:8b", s
assert s["batch_size"] == 5, s
assert s["request_delay_sec"] == 3.0, s
assert s["timeout"] == 180, s
assert s["max_retries"] == 2, s
assert s["mode"] == "backfill", s
assert s["limit"] == 0, s
assert cfg.EXPECTED_UNIFIED_DBNAME == "dopams_cctns_v2", cfg.EXPECTED_UNIFIED_DBNAME
import os
assert (os.environ.get("OLLAMA_THINK") or "0") in ("0", "false", "no", "off", "")
print("SETTINGS_OK", {k: s[k] for k in ("host","model","batch_size","request_delay_sec","timeout","max_retries","mode","limit")})
print("TARGET_OK", cfg.EXPECTED_UNIFIED_DBNAME)
PY

echo '=== kb + pg sessions (ro) ==='
$PY - <<'PY'
from etl3.db import connections
conn = connections.get_unified_connection(readonly=True)
cur = conn.cursor()
cur.execute("SELECT COUNT(*) FROM kb.drug_categories")
print("kb.drug_categories", cur.fetchone()[0])
cur.execute("""
SELECT pid, state, wait_event_type, left(query,120)
FROM pg_stat_activity
WHERE datname = current_database()
  AND pid <> pg_backend_pid()
  AND state <> 'idle'
ORDER BY pid
""")
rows = cur.fetchall()
print("active_nonidle", rows)
conn.close()
PY

echo '=== no backfill running ==='
pgrep -af 'run_ai_backfill.py' || echo none

echo VALIDATE_OK
