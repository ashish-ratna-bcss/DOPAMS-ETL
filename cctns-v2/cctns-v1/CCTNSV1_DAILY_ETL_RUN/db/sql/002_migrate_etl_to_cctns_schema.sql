-- One-time-safe: move ETL tables from public → cctns if they were created before schema split.
-- Idempotent — skips when tables are already in cctns.

CREATE SCHEMA IF NOT EXISTS cctns;
CREATE SCHEMA IF NOT EXISTS airflow;

DO $$
DECLARE
    t text;
    etl_tables text[] := ARRAY[
        'cctns_fir', 'cctns_accused', 'cctns_accused_details', 'cctns_court',
        'cctns_v1_audit_log', 'cctns_v1_etl_run_log'
    ];
BEGIN
    FOREACH t IN ARRAY etl_tables LOOP
        IF EXISTS (
            SELECT 1 FROM pg_tables
            WHERE schemaname = 'public' AND tablename = t
        ) AND NOT EXISTS (
            SELECT 1 FROM pg_tables
            WHERE schemaname = 'cctns' AND tablename = t
        ) THEN
            EXECUTE format('ALTER TABLE public.%I SET SCHEMA cctns', t);
        END IF;
    END LOOP;
END $$;

DO $$
DECLARE
    s text;
    etl_seqs text[] := ARRAY[
        'cctns_accused_accused_id_seq',
        'cctns_accused_details_accused_id_seq',
        'cctns_court_court_id_seq'
    ];
BEGIN
    FOREACH s IN ARRAY etl_seqs LOOP
        IF EXISTS (
            SELECT 1 FROM pg_sequences
            WHERE schemaname = 'public' AND sequencename = s
        ) AND NOT EXISTS (
            SELECT 1 FROM pg_sequences
            WHERE schemaname = 'cctns' AND sequencename = s
        ) THEN
            EXECUTE format('ALTER SEQUENCE public.%I SET SCHEMA cctns', s);
        END IF;
    END LOOP;
END $$;

-- Function/trigger may still be in public from older init_etl_support.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public' AND p.proname = 'cctns_v1_log_row_changes'
    ) THEN
        ALTER FUNCTION public.cctns_v1_log_row_changes() SET SCHEMA cctns;
    END IF;
EXCEPTION
    WHEN undefined_function THEN NULL;
END $$;
