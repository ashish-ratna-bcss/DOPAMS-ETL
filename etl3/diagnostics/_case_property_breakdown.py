import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "etl3"))
from db.connections import get_v2_source_connection

c = get_v2_source_connection()
cur = c.cursor()
cur.execute(
    """
    SELECT
      COUNT(*) AS total_pendingish,
      COUNT(*) FILTER (WHERE file_id IS NULL) AS null_file_id,
      COUNT(*) FILTER (WHERE file_id IS NOT NULL) AS has_file_id,
      COUNT(*) FILTER (WHERE COALESCE(has_field,false) IS TRUE AND file_id IS NULL) AS has_field_no_id
    FROM public.file_media_bookkeeping
    WHERE source_type='case_property' AND source_field='MEDIA'
      AND is_downloaded IS NOT TRUE
      AND COALESCE(is_empty,false) IS NOT TRUE
    """
)
print("case_property_pending_breakdown", cur.fetchone())
cur.execute(
    """
    SELECT left(COALESCE(download_error,'(null)'),80), COUNT(*)
    FROM public.file_media_bookkeeping
    WHERE source_type='case_property' AND source_field='MEDIA'
      AND is_downloaded IS NOT TRUE AND COALESCE(is_empty,false) IS NOT TRUE
    GROUP BY 1 ORDER BY 2 DESC LIMIT 10
    """
)
print("errors", cur.fetchall())
# How many chargesheet HTTP400 still; FIR missing
cur.execute(
    """
    SELECT
      SUM(CASE WHEN source_type='crime' AND source_field='FIR_COPY'
                AND is_downloaded IS NOT TRUE AND COALESCE(is_empty,false) IS NOT TRUE THEN 1 ELSE 0 END),
      SUM(CASE WHEN download_error ILIKE '%%400%%' THEN 1 ELSE 0 END)
    FROM public.file_media_bookkeeping
    """
)
print("fir_pending_nonempty / total_http400", cur.fetchone())
c.close()
