import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "etl3"))

from db.connections import get_unified_connection

tg = get_unified_connection()
cur = tg.cursor()
cur.execute(
    """
    UPDATE consolidation_run_log
    SET status='failed', finished_at=now(),
        error_message='interrupted: switch to catch-up-only media capture'
    WHERE status='running'
    """
)
cur.execute(
    "UPDATE consolidation_cursor SET status='idle' WHERE status IN ('running','failed')"
)
tg.commit()
print("cleaned")
tg.close()
