-- Per-row insert/update actions (unchanged rows are not logged here).

CREATE TABLE IF NOT EXISTS cctns.cctns_v1_etl_row_action (
    id          BIGSERIAL PRIMARY KEY,
    run_id      UUID NOT NULL,
    entity      TEXT NOT NULL,
    table_name  TEXT NOT NULL,
    record_key  TEXT NOT NULL,
    action      TEXT NOT NULL CHECK (action IN ('insert', 'update')),
    action_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_etl_row_action_run ON cctns.cctns_v1_etl_row_action (run_id);
CREATE INDEX IF NOT EXISTS idx_etl_row_action_entity_at ON cctns.cctns_v1_etl_row_action (entity, action_at DESC);

ALTER TABLE cctns.cctns_v1_etl_run_log
    ADD COLUMN IF NOT EXISTS rows_batch_dupes_removed INTEGER DEFAULT 0;

ALTER TABLE cctns.cctns_v1_etl_run_log
    ADD COLUMN IF NOT EXISTS rows_orphan_fir_skipped INTEGER DEFAULT 0;
