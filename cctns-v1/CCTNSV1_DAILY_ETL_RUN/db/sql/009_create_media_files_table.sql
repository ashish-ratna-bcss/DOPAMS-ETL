-- Creates tracking table for CCTNS V1 media attachments in schema cctns.

CREATE TABLE IF NOT EXISTS cctns.cctns_media_files (
    media_id bigserial PRIMARY KEY,
    entity_type character varying(20) NOT NULL,
    fir_reg_num character varying(50) NOT NULL,
    attach_path character varying(255) NOT NULL,
    dms_file_name character varying(255) NOT NULL,
    local_path text,
    file_size_bytes bigint,
    status character varying(30) NOT NULL DEFAULT 'PENDING',
    error_message text,
    downloaded_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_cctns_media UNIQUE (entity_type, attach_path, dms_file_name)
);

CREATE INDEX IF NOT EXISTS idx_media_status ON cctns.cctns_media_files USING btree (status);
CREATE INDEX IF NOT EXISTS idx_media_fir_reg ON cctns.cctns_media_files USING btree (fir_reg_num);
CREATE INDEX IF NOT EXISTS idx_media_entity_type ON cctns.cctns_media_files USING btree (entity_type);
