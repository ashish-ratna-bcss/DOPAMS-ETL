-- Multi-accused split: persons_unified / accused_unified remain the single
-- canonical store for individual person/accused identity -- a confidently
-- split segment becomes a REAL additional row in both tables, built and
-- reconciled by etl3/merger/accused_split.py, so it is visible to every
-- existing consumer (search, chargesheet linking, identity-link candidate
-- generation, the AI accused roster) with no parallel data model.
--
-- This revises the original draft of migration 018, which proposed a
-- separate "occurrence" table as the canonical store. That table was never
-- applied anywhere, so this file replaces its contents outright rather than
-- layering a second migration on top of an unapplied one.
--
-- Every change below is purely additive:
--   - new columns are NULLable (or DEFAULT FALSE/'active'), so every
--     existing row in persons_unified/accused_unified is unaffected and
--     every existing query against those tables returns the same rows it
--     always did;
--   - no existing column, constraint, or index is altered or dropped;
--   - the only other new object is accused_split_audit, a brand-new table.
--
-- Disabled by default -- see ETL3_MULTI_ACCUSED_SPLIT_ENABLED in
-- etl3/enrichment/runner.py. Not applied by anything in this commit; run
-- it explicitly (etl3/migrations/run_migration.py) only after separate
-- review, same as 017_person_enrichment_nationality.sql.

ALTER TABLE accused_unified
    ADD COLUMN derived_from_accused_id VARCHAR(100) REFERENCES accused_unified(accused_id),
    ADD COLUMN split_segment_index     SMALLINT,
    ADD COLUMN split_parser_version    VARCHAR(50),
    ADD COLUMN split_superseded_at     TIMESTAMPTZ;
-- split_superseded_at is set on:
--   (a) a packed "parent" row, once it has produced a high-confidence
--       split, so the existing AI roster builder (etl3/enrichment/runner.py
--       _load_accused_by_crime) stops asking the model to enrich the
--       combined pack text once its individual children exist;
--   (b) a split "child" row, if a later run's source text no longer
--       reproduces it (correction, reordering, shrink) -- it is retired,
--       never deleted, so prior AI/enrichment work on it stays auditable.
-- Neither case ever deletes or corrupts an existing row's own data.

CREATE INDEX idx_accused_unified_derived_from ON accused_unified(derived_from_accused_id)
    WHERE derived_from_accused_id IS NOT NULL;
CREATE INDEX idx_accused_unified_split_active ON accused_unified(derived_from_accused_id)
    WHERE derived_from_accused_id IS NOT NULL AND split_superseded_at IS NULL;

ALTER TABLE persons_unified
    ADD COLUMN derived_from_person_id VARCHAR(100) REFERENCES persons_unified(person_id),
    ADD COLUMN split_segment_index    SMALLINT;
-- No persons_unified "superseded" column: a person record is never itself
-- retired, only the case-specific accused appearance that pointed at it.

CREATE INDEX idx_persons_unified_derived_from ON persons_unified(derived_from_person_id)
    WHERE derived_from_person_id IS NOT NULL;

-- Pure audit/evidence trail: WHY a split row exists, not WHAT it contains.
-- Content (cleaned name, alias, relationship, resolved address, AI-filled
-- fields) lives in the real persons_unified/accused_unified rows and the
-- existing, unmodified person_enrichment/accused_enrichment tables, via
-- the existing enrichment pipeline -- never duplicated here.
CREATE TABLE accused_split_audit (
    split_audit_id       VARCHAR(100) PRIMARY KEY,
    parent_accused_id     VARCHAR(100) NOT NULL REFERENCES accused_unified(accused_id),
    created_accused_id     VARCHAR(100) REFERENCES accused_unified(accused_id),
    created_person_id      VARCHAR(100) REFERENCES persons_unified(person_id),
    segment_index         SMALLINT NOT NULL,
    label_number          SMALLINT,
    raw_pack_text          TEXT NOT NULL,
    raw_segment_text       TEXT NOT NULL,
    parser_version         VARCHAR(50) NOT NULL,
    parse_method          VARCHAR(50) NOT NULL,
    pack_hash             TEXT NOT NULL,
    status                VARCHAR(20) NOT NULL DEFAULT 'active'
                          CHECK (status IN ('active', 'superseded')),
    input_hash             TEXT NOT NULL,
    enrichment_run_id        UUID,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    computed_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_accused_split_audit_active_slot
    ON accused_split_audit (parent_accused_id, segment_index) WHERE status = 'active';
CREATE INDEX idx_accused_split_audit_parent ON accused_split_audit(parent_accused_id);
