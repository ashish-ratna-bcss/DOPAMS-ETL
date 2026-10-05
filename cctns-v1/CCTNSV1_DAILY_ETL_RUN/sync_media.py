"""
CCTNS V1 Media Attachments ETL.
Discovers attachments from cctns_fir and cctns_court, registers them in
cctns.cctns_media_files, downloads documents via Alfresco API, and updates status.
"""
from __future__ import annotations

import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from apis.alfresco import download_media_file
from config import settings
from db.connection import get_connection
from db.init_schema import ensure_schema

logger = logging.getLogger("cctns_v1_etl.media")


@dataclass
class MediaItem:
    media_id: int
    entity_type: str
    fir_reg_num: str
    attach_path: str
    dms_file_name: str


def discover_and_register_media(conn) -> int:
    """
    Scans cctns_fir and cctns_court for attachment fields, inserting any new
    files into cctns.cctns_media_files as 'PENDING'.
    Returns number of newly discovered rows.
    """
    insert_sql = """
    INSERT INTO cctns.cctns_media_files (entity_type, fir_reg_num, attach_path, dms_file_name, status)
    SELECT entity_type, fir_reg_num, attach_path, dms_file_name, 'PENDING'
    FROM (
        SELECT 'FIR' AS entity_type, fir_reg_num, TRIM(attach_path) AS attach_path, TRIM(dms_file_name) AS dms_file_name
        FROM cctns.cctns_fir
        WHERE attach_path IS NOT NULL AND TRIM(attach_path) != ''
          AND dms_file_name IS NOT NULL AND TRIM(dms_file_name) != ''
        UNION
        SELECT 'COURT' AS entity_type, fir_reg_num, TRIM(attach_path) AS attach_path, TRIM(dms_file_name) AS dms_file_name
        FROM cctns.cctns_court
        WHERE attach_path IS NOT NULL AND TRIM(attach_path) != ''
          AND dms_file_name IS NOT NULL AND TRIM(dms_file_name) != ''
    ) AS src
    ON CONFLICT (entity_type, attach_path, dms_file_name) DO NOTHING;
    """
    with conn.cursor() as cur:
        cur.execute(insert_sql)
        rowcount = cur.rowcount
    conn.commit()
    return rowcount


def get_pending_media_items(conn, limit: Optional[int] = None) -> List[MediaItem]:
    """
    Fetches media items currently in PENDING or FAILED status.
    """
    query = """
    SELECT media_id, entity_type, fir_reg_num, attach_path, dms_file_name
    FROM cctns.cctns_media_files
    WHERE status IN ('PENDING', 'FAILED')
    ORDER BY media_id ASC
    """
    if limit and limit > 0:
        query += f" LIMIT {int(limit)}"

    items: List[MediaItem] = []
    with conn.cursor() as cur:
        cur.execute(query)
        for row in cur.fetchall():
            items.append(
                MediaItem(
                    media_id=row[0],
                    entity_type=row[1],
                    fir_reg_num=row[2],
                    attach_path=row[3],
                    dms_file_name=row[4],
                )
            )
    return items


def _process_single_item(item: MediaItem, base_dir: str) -> Dict[str, Any]:
    """
    Downloads/caches a single media item.
    """
    result = download_media_file(
        attach_path=item.attach_path,
        dms_file_name=item.dms_file_name,
        base_dir=base_dir,
    )
    return {
        "item": item,
        "result": result,
    }


def update_media_status(conn, item: MediaItem, result: Dict[str, Any]) -> None:
    """
    Updates the tracking record in cctns.cctns_media_files.
    """
    if result["ok"]:
        sql = """
        UPDATE cctns.cctns_media_files
        SET status = %s,
            local_path = %s,
            file_size_bytes = %s,
            error_message = NULL,
            downloaded_at = CASE WHEN downloaded_at IS NULL THEN CURRENT_TIMESTAMP ELSE downloaded_at END,
            updated_at = CURRENT_TIMESTAMP
        WHERE media_id = %s;
        """
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    result["status"],
                    result.get("local_path"),
                    result.get("file_size", 0),
                    item.media_id,
                ),
            )
    else:
        sql = """
        UPDATE cctns.cctns_media_files
        SET status = %s,
            error_message = %s,
            updated_at = CURRENT_TIMESTAMP
        WHERE media_id = %s;
        """
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    result["status"],
                    result.get("error_message"),
                    item.media_id,
                ),
            )
    conn.commit()


def run_media_sync(
    limit: Optional[int] = None,
    concurrency: Optional[int] = None,
    base_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Full pipeline run for media synchronization:
    1. Ensures schema & tracking table exist
    2. Scans FIR and Court tables for new attachments
    3. Downloads pending documents with thread pool
    4. Records results and returns statistics summary
    """
    ensure_schema()
    conn = get_connection()

    try:
        newly_registered = discover_and_register_media(conn)
        logger.info("Media discovery: %d new attachment(s) registered", newly_registered)

        pending_items = get_pending_media_items(conn, limit=limit)
        total_pending = len(pending_items)

        if total_pending == 0:
            logger.info("No pending media downloads found. All attachments are up to date.")
            return {
                "total_discovered": newly_registered,
                "total_processed": 0,
                "downloaded": 0,
                "cached": 0,
                "failed": 0,
                "not_found": 0,
            }

        workers = concurrency or settings.MEDIA_DOWNLOAD_CONCURRENCY
        target_dir = base_dir or settings.MEDIA_BASE_DIR

        logger.info(
            "Starting media download for %d item(s) using %d worker(s) to %s",
            total_pending,
            workers,
            target_dir,
        )

        counts = {
            "DOWNLOADED": 0,
            "CACHED": 0,
            "FAILED": 0,
            "NOT_FOUND": 0,
        }

        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_item = {
                executor.submit(_process_single_item, item, target_dir): item
                for item in pending_items
            }

            for idx, future in enumerate(as_completed(future_to_item), 1):
                res = future.result()
                item = res["item"]
                result = res["result"]

                status = result["status"]
                counts[status] = counts.get(status, 0) + 1

                # Update row status in database
                update_media_status(conn, item, result)

                if idx % 50 == 0 or idx == total_pending:
                    logger.info(
                        "Progress: [%d/%d] - Downloaded: %d, Cached: %d, Failed: %d, 404: %d",
                        idx,
                        total_pending,
                        counts["DOWNLOADED"],
                        counts["CACHED"],
                        counts["FAILED"],
                        counts["NOT_FOUND"],
                    )

        summary = {
            "total_discovered": newly_registered,
            "total_processed": total_pending,
            "downloaded": counts["DOWNLOADED"],
            "cached": counts["CACHED"],
            "failed": counts["FAILED"],
            "not_found": counts["NOT_FOUND"],
        }
        logger.info("Media sync completed: %s", summary)
        return summary

    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] (%(name)s) %(message)s",
    )
    res = run_media_sync()
    print(f"\nFinal Media Sync Summary: {res}")
