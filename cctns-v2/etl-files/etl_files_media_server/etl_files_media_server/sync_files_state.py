#!/usr/bin/env python3
"""
DOPAMAS ETL - Files State Synchronizer

This script audits MinIO and synchronizes the 'files' table.
It identifies records marked as is_downloaded=TRUE where the object is
actually missing from the bucket and updates the database accordingly.
"""

import os
import sys
import logging
import psycopg2
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# Add parent directory to path for config imports
CURRENT_DIR = Path(__file__).resolve().parent
PARENT_DIR = CURRENT_DIR.parent
if str(PARENT_DIR) not in sys.path:
    sys.path.insert(0, str(PARENT_DIR))

from config import DB_CONFIG
from etl_files_media_server.object_store import get_object_store

# Logging setup
logger = logging.getLogger("sync-files-state")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logger.addHandler(console_handler)
    logger.propagate = False

# Constants
FILES_TABLE = os.getenv("FILES_TABLE", "files")
WORKER_COUNT = int(os.getenv("SYNC_WORKER_COUNT", "32"))

_SUBDIR_MAPPING = {
    ("crime", "FIR_COPY"): "crimes",
    ("person", "IDENTITY_DETAILS"): "person/identitydetails",
    ("person", "MEDIA"): "person/media",
    ("property", "MEDIA"): "property",
    ("interrogation", "MEDIA"): "interrogations/media",
    ("interrogation", "INTERROGATION_REPORT"): "interrogations/interrogationreport",
    ("interrogation", "DOPAMS_DATA"): "interrogations/dopamsdata",
}


def is_file_missing(file_id, source_type, source_field):
    """Checks whether this file_id's object exists in MinIO.

    A per-file ListObjectsV2(Prefix=..., MaxKeys=1) call -- not a cached
    full-directory listing -- on purpose: list_objects_v2 only returns up to
    1000 keys per call, so caching a whole prefix's contents (as the old
    os.listdir()-based version did for a local directory) would silently
    truncate and misreport files as missing once any one subdir passed
    1000 objects.
    """
    sub_dir = _SUBDIR_MAPPING.get((source_type, source_field))
    if not sub_dir:
        return True  # Missing

    prefix = f"{sub_dir}/{file_id}."
    found = get_object_store().find_by_prefix(prefix)
    return found is None  # True = missing

def sync():
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        
        logger.info(f"🔍 Fetching records marked as downloaded from {FILES_TABLE}...")
        cur.execute(f"SELECT file_id, source_type, source_field FROM {FILES_TABLE} WHERE is_downloaded IS TRUE")
        rows = cur.fetchall()
        
        total = len(rows)
        if total == 0:
            logger.info("✅ No downloaded files to verify.")
            return

        logger.info(f"📊 Found {total} records to verify in MinIO. Scanning with {WORKER_COUNT} workers...")

        # Initialize the (lazy singleton) object store once, in this thread,
        # before fanning out -- fails fast on missing credentials instead of
        # inside 32 workers, and avoids a benign init race between them.
        get_object_store()

        missing_ids = []
        processed = 0

        with ThreadPoolExecutor(max_workers=WORKER_COUNT) as executor:
            # Map futures to file_id
            futures = {
                executor.submit(is_file_missing, row[0], row[1], row[2]): row[0]
                for row in rows
            }
            
            for future in as_completed(futures):
                file_id = futures[future]
                processed += 1
                
                if processed % 1000 == 0:
                    logger.info(f"   ... verified {processed}/{total} files ...")
                
                try:
                    is_missing = future.result()
                    if is_missing:
                        missing_ids.append(file_id)
                except Exception as e:
                    logger.error(f"Error checking file_id {file_id}: {e}")
        
        missing_count = len(missing_ids)
        
        if missing_count > 0:
            logger.warning(f"❌ Found {missing_count} missing objects in MinIO. Updating database in batch...")
            
            # Batch update the database for maximum performance
            cur.execute(f"""
                UPDATE {FILES_TABLE} 
                SET is_downloaded = FALSE, 
                    download_error = 'Sync: Object missing in MinIO during audit'
                WHERE file_id = ANY(%s::uuid[])
            """, (missing_ids,))
            
            conn.commit()
            logger.info(f"📝 Database updated successfully for {missing_count} missing files.")
        else:
            logger.info("✅ All objects are present in MinIO.")
            
        logger.info("=" * 50)
        logger.info(f"✅ Audit Complete")
        logger.info(f"   - Total checked: {total}")
        logger.info(f"   - Missing in MinIO: {missing_count}")
        logger.info("=" * 50)
        
    except Exception as e:
        logger.error(f"💥 Error during sync: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

if __name__ == "__main__":
    sync()
