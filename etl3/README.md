# ETL-3 — CCTNS Unified Merger ETL

Reads `cctns_v1` and `cctns-v2`, read-only, and is the sole writer to `dopams_cctns`. Never writes to either source. Design docs: `../dopams_cctns/schema/ETL3_MERGER_IMPLEMENTATION_PLAN.md` and its companions.

## Layout

```
etl3/
├── config/        .env (gitignored) + settings.py loader
├── db/            connections.py -- the only place a DB connection is opened
├── migrations/    DDL + run_migration.py
├── sources/v1/    V1 source adapter (not yet implemented)
├── sources/v2/    V2 source adapter (not yet implemented)
├── loaders/       writes *_source rows
├── merger/        current-state computation
├── identity/       candidate person-matching
├── history/       change_log / bulk_event_exclusions
├── checkpoints/   consolidation_cursor logic
├── reconciliation/
├── monitoring/
└── tests/
```

## Setup

```
pip install -r requirements.txt
cp config/.env.example config/.env   # fill in real values, never commit this file
```

## Safety invariant

`db/connections.py` enforces, independently of the credentials' actual privilege level:

- `get_v1_source_connection()` / `get_v2_source_connection()`: Postgres-level read-only session (`default_transaction_read_only=on` + `conn.set_session(readonly=True)`), plus an assertion that `current_database()` is exactly `cctns_v1`/`cctns-v2`.
- `get_unified_connection()`: asserts `current_database()` is exactly `dopams_cctns` before allowing any query.

A mismatch on either check closes the connection and raises before a single query runs. See `tests/test_connections.py` for a smoke test, including a deliberate proof that the mismatch check fires (`python etl3/tests/test_connections.py`).

## Migrations

```
python etl3/migrations/run_migration.py 001_initial_schema.sql
```

`run_migration.py` refuses to run `001_initial_schema.sql` against a `dopams_cctns` that already has tables in it, and records every applied migration in `_migrations` so re-running is a no-op.

## Status

- Phase 0 (discovery): done — `../dopams_cctns/schema/ETL3_PHASE0_DISCOVERY.md`
- Phase 1 (schema creation): done — 29 tables + `_migrations`, 14 FKs, verified live against `dopams_cctns`
- Phase 2 onward (source adapters, merge logic, identity, history, control plane, reconciliation): not yet implemented
