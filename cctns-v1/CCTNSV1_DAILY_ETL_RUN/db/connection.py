import psycopg2

from config.settings import PG_HOST, PG_PORT, PG_DATABASE, PG_USER, PG_PASSWORD, require
from db.init_schema import ensure_schema


def get_connection(*, bootstrap_schema: bool = True):
    require("PG_USER", "PG_PASSWORD")
    if bootstrap_schema:
        ensure_schema()
    return psycopg2.connect(
        host=PG_HOST, port=PG_PORT, dbname=PG_DATABASE,
        user=PG_USER, password=PG_PASSWORD,
    )
