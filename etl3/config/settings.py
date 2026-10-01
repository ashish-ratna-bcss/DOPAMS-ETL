"""
ETL-3 configuration loader.

Loads etl3/config/.env (never committed -- see .gitignore) and resolves the
two source .env paths it points at. Nothing here connects to anything; it
only loads values. db/connections.py is where connections actually happen,
and where the safety checks live.
"""
from pathlib import Path

from dotenv import dotenv_values

CONFIG_DIR = Path(__file__).resolve().parent
ETL3_ROOT = CONFIG_DIR.parent

_env = dotenv_values(CONFIG_DIR / ".env")

# Destination (read/write) -- the ONLY database ETL-3 ever writes to.
UNIFIED_DB = {
    "host": _env["UNIFIED_PG_HOST"],
    "port": _env["UNIFIED_PG_PORT"],
    "dbname": _env["UNIFIED_PG_DATABASE"],
    "user": _env["UNIFIED_PG_USER"],
    "password": _env["UNIFIED_PG_PASSWORD"],
}
UNIFIED_SCHEMA = _env.get("UNIFIED_PG_SCHEMA", "public")

# Hardcoded independent of the .env file, on purpose: even if UNIFIED_PG_DATABASE
# in the .env were ever mistyped or pointed somewhere else, the connection
# layer in db/connections.py checks the ACTUAL current_database() against this
# constant, not against whatever the .env happened to say.
EXPECTED_UNIFIED_DBNAME = "dopams_cctns"

# Sources (read-only) -- paths to the existing V1/V2 ETL .env files, resolved
# relative to the etl3/ directory.
V1_SOURCE_ENV_PATH = (ETL3_ROOT / _env["V1_SOURCE_ENV_PATH"]).resolve()
V2_SOURCE_ENV_PATH = (ETL3_ROOT / _env["V2_SOURCE_ENV_PATH"]).resolve()

EXPECTED_V1_DBNAME = "cctns_v1"
EXPECTED_V2_DBNAME = "cctns-v2"
