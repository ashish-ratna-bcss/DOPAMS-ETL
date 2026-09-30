-- Durable ledger for date-window extract failures (accused dossier POST).
-- Status lifecycle: OPEN → RESOLVED (rows are kept, not deleted).

CREATE TABLE IF NOT EXISTS cctns.cctns_v1_failed_fetch_window (
    id              BIGSERIAL PRIMARY KEY,
    entity          TEXT NOT NULL,
    window_start    DATE NOT NULL,
    window_end      DATE NOT NULL,
    error           TEXT NOT NULL,
    attempt_count   INTEGER NOT NULL DEFAULT 1,
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    status          TEXT NOT NULL DEFAULT 'OPEN'
                    CHECK (status IN ('OPEN', 'RETRYING', 'RESOLVED')),
    run_id          UUID,
    CONSTRAINT uq_cctns_v1_failed_fetch_window_entity_range
        UNIQUE (entity, window_start, window_end)
);

CREATE INDEX IF NOT EXISTS idx_failed_fetch_window_entity_status
    ON cctns.cctns_v1_failed_fetch_window (entity, status);

CREATE INDEX IF NOT EXISTS idx_failed_fetch_window_last_seen
    ON cctns.cctns_v1_failed_fetch_window (last_seen_at DESC);
