-- One row per V1 daily cycle. status='succeeded' is the cycle-success marker.
-- Written only after FIR, court, accused_details, and accused share run_id
-- and finish in order. Media is not part of this marker.

CREATE TABLE IF NOT EXISTS cctns.cctns_v1_etl_cycle (
    run_id          UUID PRIMARY KEY,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    status          TEXT NOT NULL,
    cycle_start     TIMESTAMPTZ NOT NULL,
    error_message   TEXT,
    CONSTRAINT cctns_v1_etl_cycle_status_check
        CHECK (status IN ('running', 'succeeded', 'failed', 'incomplete'))
);

CREATE INDEX IF NOT EXISTS idx_cctns_v1_etl_cycle_started
    ON cctns.cctns_v1_etl_cycle (started_at DESC);

CREATE INDEX IF NOT EXISTS idx_etl_run_log_run_id
    ON cctns.cctns_v1_etl_run_log (run_id);
