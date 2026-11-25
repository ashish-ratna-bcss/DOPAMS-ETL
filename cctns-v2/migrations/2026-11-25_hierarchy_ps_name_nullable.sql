-- Migration: allow public.hierarchy.ps_name to be NULL
--
-- Root cause: the CCTNS V2 /master-data/hierarchy endpoint returns
-- organisational units that are not police stations (e.g. "IT Cell",
-- "Special Branch", "CCRB", "CRSB", "Taskforce", "Traffic" admin offices).
-- These units have no PS_NAME in the source payload -- they sit above or
-- beside the PS layer in the hierarchy, not as one. The API still assigns
-- them their own PS_CODE (used as hierarchy.ps_code, the table's primary
-- key), since cases can be registered directly under such a unit without
-- going through a police station.
--
-- hierarchy.ps_name was NOT NULL, so every one of these non-station rows
-- raised a psycopg2.IntegrityError on insert (see
-- etl-hierarchy/etl_hierarchy.py: insert_hierarchy()'s IntegrityError
-- handler, which logs the row as "skipped_integrity_error" and calls
-- self.window_guard.fail_current()). etl_window_guard.py then refuses to
-- advance the hierarchy checkpoint past the first window containing such a
-- row -- the entire V2 master ETL stalls on that window indefinitely,
-- independent of and indistinguishable from a true upstream outage.
--
-- This migration only removes the NOT NULL constraint. It does not:
--   - touch ps_code (primary key, still required and populated for every
--     row, station or not)
--   - alter, rename, or drop any column
--   - change any existing police-station row's data
--   - add a default or synthesize a placeholder name
-- No other table has a foreign key into hierarchy (verified live: zero
-- rows from pg_constraint where confrelid = 'public.hierarchy'::regclass),
-- so no downstream constraint is affected by a NULL ps_name appearing.
--
-- Safe to rerun. Reversible: a future
--   ALTER TABLE public.hierarchy ALTER COLUMN ps_name SET NOT NULL;
-- would only fail if a NULL ps_name row still exists at that time, which
-- is the expected, intentional state for non-station units going forward.

ALTER TABLE public.hierarchy
    ALTER COLUMN ps_name DROP NOT NULL;

COMMENT ON COLUMN public.hierarchy.ps_name IS
    'Police station name. NULL for non-station administrative units '
    '(e.g. IT Cell, Special Branch, CCRB, Taskforce) that have no PS '
    'beneath them in the hierarchy -- cases can be registered directly '
    'under such a unit via hierarchy.ps_code.';

-- Verification query
-- SELECT is_nullable FROM information_schema.columns
-- WHERE table_name = 'hierarchy' AND column_name = 'ps_name';

-- Audit query after the next hierarchy ETL run:
-- SELECT ps_code, sdpo_name, dist_name
-- FROM public.hierarchy
-- WHERE ps_name IS NULL
-- ORDER BY ps_code;
