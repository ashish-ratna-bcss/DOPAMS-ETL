"""Stage 2: in-batch duplicate removal and parent FIR relationship checks."""
import logging

from config.settings import PG_ETL_SCHEMA
from db.natural_key import fir_reg_num, record_key

logger = logging.getLogger("cctns_v1_etl.validate")

_ENTITIES_REQUIRING_FIR = frozenset({"court", "accused_details", "accused"})


def dedupe_batch(entity: str, records: list) -> tuple[list, int]:
    """Keep first row per upsert key; drop later identical keys in the same API response."""
    seen: set[str] = set()
    unique: list = []
    removed = 0
    for rec in records:
        key = record_key(entity, rec)
        if not key:
            unique.append(rec)
            continue
        if key in seen:
            removed += 1
            continue
        seen.add(key)
        unique.append(rec)
    return unique, removed


def filter_orphan_fir(entity: str, records: list, conn) -> tuple[list, int]:
    """Skip child rows whose fir_reg_num is not in cctns_fir (FK would fail)."""
    if entity not in _ENTITIES_REQUIRING_FIR:
        return records, 0
    with conn.cursor() as cur:
        cur.execute(f"SELECT fir_reg_num FROM {PG_ETL_SCHEMA}.cctns_fir")
        valid_fir = {row[0] for row in cur.fetchall()}
    kept: list = []
    skipped = 0
    for rec in records:
        frn = fir_reg_num(rec)
        if frn and frn not in valid_fir:
            skipped += 1
            continue
        kept.append(rec)
    return kept, skipped
