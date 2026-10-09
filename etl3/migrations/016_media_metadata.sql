-- ETL-3 media metadata consolidation.
-- Reads V1 cctns.cctns_media_files and V2 public.file_media_bookkeeping
-- into observation + unified tables. Does not copy binary files.
-- Does not alter KB schemas. Does not write to V1/V2.

CREATE TABLE IF NOT EXISTS media_source (
    LIKE crimes_source INCLUDING ALL
);
-- source_table: 'cctns_media_files' (V1) | 'file_media_bookkeeping' (V2)
-- source_record_id: V1 media_id text | V2 bookkeeping id uuid text

CREATE INDEX IF NOT EXISTS idx_media_source_record
    ON media_source (source_system, source_record_id);
CREATE INDEX IF NOT EXISTS idx_media_source_run
    ON media_source (source_system, source_run_id);

CREATE TABLE IF NOT EXISTS media_unified (
    media_id                 VARCHAR(120) PRIMARY KEY,
    -- Collision-safe: 'V1:cctns_media_files:{media_id}' | 'V2:file_media_bookkeeping:{id}'
    source_system            VARCHAR(2) NOT NULL CHECK (source_system IN ('V1','V2')),
    source_module            VARCHAR(100) NOT NULL,
    source_record_id         VARCHAR(100) NOT NULL,
    attachment_category      VARCHAR(100),
    -- V1: FIR | COURT ; V2: '{source_type}/{source_field}'
    parent_crime_id          VARCHAR(100),
    -- V1: fir_reg_num ; V2: crime_id when source_type=crime, else NULL until linked
    parent_entity_type       VARCHAR(50),
    -- crime | person | accused | chargesheet | interrogation | property | case_property | mo_seizure | fir | court
    parent_entity_id         VARCHAR(100),
    original_attach_path     TEXT,
    original_file_name       TEXT,
    source_file_id           VARCHAR(100),
    -- V2 file_id (nullable when unresolved); V1 unused
    source_local_path        TEXT,
    -- Absolute path exactly as stored in the source bookkeeping (may be host-specific)
    resolved_relative_path   TEXT,
    -- Path relative to the configured media root when safely resolvable; NULL otherwise
    media_root_key           VARCHAR(40),
    -- V1_MEDIA | V2_SHARED | NULL when no authorized root applies
    file_size_bytes          BIGINT,
    mime_type                VARCHAR(120),
    source_download_status   VARCHAR(50),
    -- Raw source status / flags preserved for audit
    availability_status      VARCHAR(60) NOT NULL,
    -- VERIFIED_ACCESSIBLE | BOOKKEEPING_DOWNLOADED_INACCESSIBLE | MISSING_AT_SOURCE
    -- | EMPTY_AT_SOURCE | PENDING | DOWNLOAD_FAILED | INVALID_PATH | UNRESOLVED_FILE_ID
    availability_detail      TEXT,
    downloaded_at            TIMESTAMPTZ,
    current_source_run_id    VARCHAR(100) NOT NULL,
    current_as_of            TIMESTAMPTZ,
    computed_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_system, source_module, source_record_id)
);

CREATE INDEX IF NOT EXISTS idx_media_unified_crime
    ON media_unified (parent_crime_id);
CREATE INDEX IF NOT EXISTS idx_media_unified_parent
    ON media_unified (parent_entity_type, parent_entity_id);
CREATE INDEX IF NOT EXISTS idx_media_unified_availability
    ON media_unified (availability_status);
CREATE INDEX IF NOT EXISTS idx_media_unified_category
    ON media_unified (attachment_category);

-- Backend read contract: media metadata only (no binary serving here).
CREATE OR REPLACE VIEW be_read.media AS
SELECT
    m.media_id,
    m.source_system,
    m.source_module,
    m.source_record_id,
    m.attachment_category,
    m.parent_crime_id,
    m.parent_entity_type,
    m.parent_entity_id,
    m.original_attach_path,
    m.original_file_name,
    m.source_file_id,
    m.source_local_path,
    m.resolved_relative_path,
    m.media_root_key,
    m.file_size_bytes,
    m.mime_type,
    m.source_download_status,
    m.availability_status,
    m.availability_detail,
    m.downloaded_at,
    m.current_source_run_id,
    m.current_as_of,
    (m.availability_status = 'VERIFIED_ACCESSIBLE') AS is_accessible
FROM media_unified m;

DO $$
BEGIN
    EXECUTE 'DROP TRIGGER IF EXISTS be_read_reject_write ON be_read.media';
    EXECUTE 'CREATE TRIGGER be_read_reject_write INSTEAD OF INSERT OR UPDATE OR DELETE ON be_read.media FOR EACH ROW EXECUTE FUNCTION be_read.reject_write()';
EXCEPTION
    WHEN undefined_function THEN
        -- be_read.reject_write from 006 may be absent on a partial DB; skip trigger
        NULL;
    WHEN undefined_table THEN
        NULL;
END $$;
