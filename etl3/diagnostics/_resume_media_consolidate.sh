#!/usr/bin/env bash
# Resume media_unified build after observation catch-up (already on media_source).
# Safe: no AI, no V1/V2 writes, no mass re-download.
set -euo pipefail
cd /home/eagle/dopams-cctns-ai
pkill -f 'run_media_consolidation.py' 2>/dev/null || true
sleep 2
/home/eagle/dopams-cctns-ai/.venv/bin/python - <<'PY'
import sys
sys.path[:0]=[".","etl3"]
from db.connections import get_unified_connection
tg=get_unified_connection(); cur=tg.cursor()
cur.execute("""UPDATE consolidation_run_log SET status='failed', finished_at=now(),
  error_message='interrupted before resume' WHERE status='running'""")
cur.execute("UPDATE consolidation_cursor SET status='idle' WHERE status IN ('running','failed')")
tg.commit(); print("lock/run cleaned"); tg.close()
PY
nohup env ETL3_AI_ENABLED=0 PYTHONUNBUFFERED=1 \
  /home/eagle/dopams-cctns-ai/.venv/bin/python etl3/run_media_consolidation.py --consolidate-only \
  > logs/media_consolidation.log 2>&1 &
echo "PID $!"
sleep 3
tail -20 logs/media_consolidation.log
