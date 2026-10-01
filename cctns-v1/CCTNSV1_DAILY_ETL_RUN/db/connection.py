import psycopg2

from config.settings import (
    PG_DATABASE,
    PG_ETL_SCHEMA,
    PG_HOST,
    PG_PASSWORD,
    PG_PORT,
    PG_SSLMODE,
    PG_USER,
    require,
)
from db.init_schema import ensure_schema


def get_connection(*, bootstrap_schema: bool = True):
    require("PG_HOST", "PG_USER", "PG_PASSWORD")
    if bootstrap_schema:
        ensure_schema()
    return psycopg2.connect(
        host=PG_HOST,
        port=PG_PORT,
        dbname=PG_DATABASE,
        user=PG_USER,
        password=PG_PASSWORD,
        sslmode=PG_SSLMODE,
        options=f"-c search_path={PG_ETL_SCHEMA},public",
    )
