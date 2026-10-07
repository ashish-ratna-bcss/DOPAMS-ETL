"""Run ETL-3 enrichment against dopams_cctns.

The geography lookup reads V2 geo tables through the read-only source
connection. Nothing is written to V1 or V2.

Usage: python etl3/run_enrichment.py
"""
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from etl3.db import connections
from etl3.enrichment.runner import run_enrichment


def main():
    conn = connections.get_unified_connection()
    run_id = str(uuid.uuid4())
    try:
        stats = run_enrichment(conn, run_id)
        conn.commit()
        print(f"enrichment_run_id {run_id}")
        print(stats)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
