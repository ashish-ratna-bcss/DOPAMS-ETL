"""Add media_unified.computed_at if missing (UnifiedBatchWriter contract)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl3.db.connections import get_unified_connection


def main() -> None:
    c = get_unified_connection()
    cur = c.cursor()
    cur.execute(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='public' AND table_name='media_unified'
          AND column_name='computed_at'
        """
    )
    if cur.fetchone():
        print("computed_at already present")
    else:
        cur.execute(
            """
            ALTER TABLE media_unified
            ADD COLUMN computed_at TIMESTAMPTZ NOT NULL DEFAULT now()
            """
        )
        c.commit()
        print("added media_unified.computed_at")
    c.close()


if __name__ == "__main__":
    main()
