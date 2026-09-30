-- Move Airflow metadata tables from public → airflow (legacy before sql_alchemy_schema was set).
-- Skips anything still named cctns_* (ETL belongs in schema cctns).

CREATE SCHEMA IF NOT EXISTS airflow;

DO $$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT tablename FROM pg_tables
        WHERE schemaname = 'public'
          AND tablename NOT LIKE 'cctns%'
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_tables
            WHERE schemaname = 'airflow' AND tablename = r.tablename
        ) THEN
            EXECUTE format('ALTER TABLE public.%I SET SCHEMA airflow', r.tablename);
        END IF;
    END LOOP;
END $$;

DO $$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT sequencename FROM pg_sequences
        WHERE schemaname = 'public'
          AND sequencename NOT LIKE 'cctns%'
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_sequences
            WHERE schemaname = 'airflow' AND sequencename = r.sequencename
        ) THEN
            EXECUTE format('ALTER SEQUENCE public.%I SET SCHEMA airflow', r.sequencename);
        END IF;
    END LOOP;
END $$;
