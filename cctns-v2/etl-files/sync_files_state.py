#!/usr/bin/env python3
"""
Sync Script: Database vs MinIO
Checks for files marked as downloaded in DB but missing from the MinIO
object store. Resets is_downloaded = FALSE for missing files to allow
re-download.

This is a standalone duplicate of etl_files_media_server/sync_files_state.py
(imported directly by main.py's CLI entrypoint via a sys.path insert, not via
the package's relative import) -- kept in sync with it by hand, matching this
codebase's existing pattern of duplicating map_destination_subdir() across
main.py / update_file_urls_with_extensions.py / this file.
"""

import os
import logging
import psycopg2
from pathlib import Path
import sys

# Ensure the repo root (where env_utils.py lives) is on sys.path so this
# module can be imported regardless of the working directory.
_REPO_ROOT = Path(__file__).resolve().parents[1]  # sync_files_state.py -> etl-files/ -> repo root
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Ensure the etl_files_media_server package (which holds object_store.py) is
# importable from this standalone script too.
_PACKAGE_DIR = Path(__file__).resolve().parent / "etl_files_media_server"
if str(_PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_DIR))

from env_utils import first_env, load_repo_environment, resolve_db_config, resolve_table_name
from etl_files_media_server.object_store import get_object_store

SCRIPT_DIR = Path(__file__).resolve().parent

LOADED_ENV_FILE = load_repo_environment(extra_candidates=[SCRIPT_DIR / ".env.server", SCRIPT_DIR / ".env"])
_DB_RESOLVED = resolve_db_config()
DB_CONFIG = {
    "host": _DB_RESOLVED["host"],
    "port": _DB_RESOLVED["port"],
    "dbname": _DB_RESOLVED["dbname"],
    "user": _DB_RESOLVED["user"],
    "password": _DB_RESOLVED["password"],
}

FILES_TABLE = resolve_table_name("FILES_TABLE", "files")

# Setup logger for sync script
_sync_logger = None

def get_sync_logger():
    """Get or create logger for sync operations."""
    global _sync_logger
    if _sync_logger is None:
        _sync_logger = logging.getLogger("etl-files-sync")
        if not _sync_logger.handlers:
            _sync_logger.setLevel(logging.INFO)
            handler = logging.StreamHandler()
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            _sync_logger.addHandler(handler)
    return _sync_logger


def validate_db_config(logger=None):
    if logger is None:
        logger = get_sync_logger()
    
    required = {
        "DB_HOST": DB_CONFIG["host"],
        "DB_NAME": DB_CONFIG["dbname"],
        "DB_USER": DB_CONFIG["user"],
        "DB_PASSWORD": DB_CONFIG["password"],
        "DB_PORT": DB_CONFIG["port"],
    }

    missing = [key for key, value in required.items() if value in (None, "")]
    if not missing:
        return True

    logger.error("Missing required database configuration.")
    logger.error(f"Missing variables: {', '.join(missing)}")
    if LOADED_ENV_FILE:
        logger.error(f"Loaded env file: {LOADED_ENV_FILE}")
    else:
        logger.error("No repo env file was loaded. Falling back to ambient environment.")
    logger.error("You can also set DOPAMS_ENV_FILE=/absolute/path/to/.env.server before running.")
    return False

def map_destination_subdir(source_type, source_field):
    source_type = (source_type or "").lower()
    source_field = (source_field or "").upper()

    mapping = {
        ("crime", "FIR_COPY"): "crimes",
        ("crime", "MEDIA"): "crimes",
        ("person", "IDENTITY_DETAILS"): os.path.join("person", "identitydetails"),
        ("person", "MEDIA"): os.path.join("person", "media"),
        ("property", "MEDIA"): "property",
        ("interrogation", "MEDIA"): os.path.join("interrogations", "media"),
        ("interrogation", "INTERROGATION_REPORT"): os.path.join("interrogations", "interrogationreport"),
        ("interrogation", "DOPAMS_DATA"): os.path.join("interrogations", "dopamsdata"),
        ("mo_seizures", "MO_MEDIA"): "mo_seizures",
        ("chargesheets", "UPLOADCHARGESHEET"): "chargesheets",
        ("case_property", "MEDIA"): "fsl_case_property"
    }
    return mapping.get((source_type, source_field))

def run_sync(logger=None):
    """
    Sync database file status with filesystem.
    Checks for files marked as downloaded but missing on disk.
    Resets is_downloaded = FALSE for missing files to allow re-download.
    """
    if logger is None:
        logger = get_sync_logger()

    if not validate_db_config(logger):
        return False

    logger.info(f"Connecting to database {DB_CONFIG['dbname']} at {DB_CONFIG['host']}...")
    if LOADED_ENV_FILE:
        logger.info(f"Loaded environment from {LOADED_ENV_FILE}")

    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()
    except Exception as e:
        logger.error(f"Error connecting to database: {e}")
        return False

    try:
        store = get_object_store()  # fail fast if MinIO credentials aren't configured
    except Exception as e:
        logger.error(f"Error initializing MinIO object store: {e}")
        cursor.close()
        conn.close()
        return False

    logger.info(f"MinIO bucket: {store.bucket}")

    # Fetch all files marked as downloaded
    sql = f"""
        SELECT id, source_type, source_field, file_id, file_url
        FROM {FILES_TABLE}
        WHERE is_downloaded = TRUE AND file_id IS NOT NULL
    """
    cursor.execute(sql)
    rows = cursor.fetchall()

    logger.info(f"Found {len(rows)} records marked as is_downloaded = TRUE")

    missing_count = 0
    fixed_count = 0

    for row in rows:
        db_id, source_type, source_field, file_id, file_url = row

        subdir = map_destination_subdir(source_type, source_field)
        if not subdir:
            continue

        prefix = f"{subdir}/{file_id}."
        found = store.find_by_prefix(prefix)

        if found is None:
            missing_count += 1
            # logger.debug(f"Missing in MinIO: {prefix}*")

            # Reset status in DB
            update_sql = f"""
                UPDATE {FILES_TABLE}
                SET is_downloaded = FALSE,
                    download_error = 'Sync: Object missing in MinIO at expected prefix'
                WHERE id = %s
            """
            cursor.execute(update_sql, (db_id,))
            fixed_count += 1

            if fixed_count % 100 == 0:
                conn.commit()
                logger.info(f"Fixed {fixed_count} records...")

    conn.commit()
    logger.info("")
    logger.info("=" * 80)
    logger.info("📊 FILES SYNC VALIDATION SUMMARY")
    logger.info("=" * 80)
    logger.info(f"Total records checked: {len(rows)}")
    logger.info(f"Missing from MinIO: {missing_count}")
    logger.info(f"DB records reset for re-download: {fixed_count}")
    logger.info("=" * 80)

    cursor.close()
    conn.close()

    return True

if __name__ == "__main__":
    run_sync()
