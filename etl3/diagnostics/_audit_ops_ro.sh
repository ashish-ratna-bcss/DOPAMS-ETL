#!/usr/bin/env bash
set -euo pipefail
cd /home/eagle/dopams-cctns-ai
echo '===CRONTAB eagle==='
crontab -l 2>/dev/null | head -50 || echo none
echo '===PHASE5 TAIL==='
tail -50 logs/phase5_complete_no_ai.log 2>/dev/null || true
echo '===MEDIA TAIL==='
tail -40 logs/media_consolidation.log 2>/dev/null || true
echo '===IDEMPOTENCY==='
tail -20 logs/idempotency_phase4.log 2>/dev/null || true
echo '===AI PROC==='
ps -p 19139 -o pid,etime,cmd --no-headers || echo '19139 gone'
grep -E 'ai-per-fir' logs/ai_backfill_20261010T084414Z.log | tail -8
echo '===CONNECTIONS DOC==='
PYTHONPATH=. .venv/bin/python - <<'PY'
from etl3.config import settings as s
print('EXPECTED_UNIFIED_DBNAME', s.EXPECTED_UNIFIED_DBNAME)
from etl3.db import connections
import inspect
src = inspect.getsource(connections.get_unified_connection)
print('get_unified_connection default_readonly_hint', 'readonly' in src)
# Show how V1 is opened
print('--- get_v1 ---')
print(inspect.getsource(connections.get_v1_connection)[:800])
PY
