-- Cleaned person names from the four fix_fullname rules.
-- persons_unified.full_name stays the source string. raw_full_name on
-- this table is that same source string. The cleaned_* columns are the
-- result of the alias, relationship, and metadata cleanup.

ALTER TABLE person_enrichment
    ADD COLUMN IF NOT EXISTS raw_full_name TEXT,
    ADD COLUMN IF NOT EXISTS cleaned_full_name TEXT,
    ADD COLUMN IF NOT EXISTS cleaned_given_name TEXT,
    ADD COLUMN IF NOT EXISTS cleaned_alias VARCHAR(255),
    ADD COLUMN IF NOT EXISTS cleaned_relative_name TEXT;
