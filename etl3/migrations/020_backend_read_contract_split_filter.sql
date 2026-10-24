-- be_read.accused currently has no awareness of multi-accused splitting
-- (migration 018): once that feature is ever activated, a superseded
-- packed parent and its active split children would all appear to any
-- consumer of this view as separate, undifferentiated rows for the same
-- real accused appearance.
--
-- This filters be_read.accused to split_superseded_at IS NULL -- the same
-- convention etl3.enrichment.runner._split_child_records() and
-- _load_accused_by_crime() already use for the AI/enrichment roster. An
-- ordinary (never split) accused row always has split_superseded_at NULL,
-- so this is a no-op for every row as the data exists today; it only takes
-- effect once migration 018 is applied AND the split feature actually
-- writes a superseded row. Two new columns (derived_from_accused_id,
-- split_segment_index) are appended so a consumer can tell a split child
-- from an ordinary row without a second query -- purely additive, same
-- convention migration 019 already established for this view.
--
-- Depends on migration 018 (adds accused_unified.split_superseded_at /
-- derived_from_accused_id / split_segment_index). Applying this before 018
-- fails loudly (UndefinedColumn), not silently -- same convention the
-- split feature itself follows.
--
-- be_read.person is intentionally unchanged: persons_unified rows are
-- never marked superseded (only an accused_unified appearance is); a split
-- parent's own person_id and persons_unified row are untouched by the
-- split.

CREATE OR REPLACE VIEW be_read.accused AS
SELECT
    a.accused_id,
    a.source_system,
    a.source_record_id,
    a.crime_id,
    a.person_id,
    a.unlinked_person_flag,
    a.accused_code,
    a.accused_status,
    a.is_ccl,
    p.full_name,
    p.relative_name,
    p.phone_number,
    p.gender,
    a.current_source_run_id,
    a.current_as_of,
    a.derived_from_accused_id,
    a.split_segment_index
FROM accused_unified a
LEFT JOIN persons_unified p ON p.person_id = a.person_id
WHERE a.split_superseded_at IS NULL;
