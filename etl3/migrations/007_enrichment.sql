-- ETL-3 enrichment tables. Applied only to dopams_cctns.
-- Raw *_source payloads are not updated. Canonical *_unified CCTNS columns
-- are not overwritten. Derived values live here, with a provenance method
-- and an input hash so replay does not duplicate change_log rows.

CREATE TABLE crime_enrichment (
    crime_id                  VARCHAR(100) PRIMARY KEY REFERENCES crimes_unified(crime_id),
    source_system             VARCHAR(2) NOT NULL CHECK (source_system IN ('V1','V2')),
    class_classification      VARCHAR(50),
    classification_method     VARCHAR(40) NOT NULL,
    case_status_raw           VARCHAR(100),
    case_status_normalized    VARCHAR(100),
    case_status_method        VARCHAR(40) NOT NULL,
    input_hash                TEXT NOT NULL,
    enrichment_run_id         UUID,
    computed_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE person_enrichment (
    person_id                 VARCHAR(100) PRIMARY KEY REFERENCES persons_unified(person_id),
    source_system             VARCHAR(2) NOT NULL CHECK (source_system IN ('V1','V2')),
    domicile_classification   VARCHAR(50),
    domicile_method           VARCHAR(40) NOT NULL,
    surname                   VARCHAR(255),
    relation_type             VARCHAR(50),
    gender_source             VARCHAR(30),
    input_hash                TEXT NOT NULL,
    enrichment_run_id         UUID,
    computed_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE arrest_enrichment (
    arrest_id                 VARCHAR(100) PRIMARY KEY REFERENCES arrests_unified(arrest_id),
    source_system             VARCHAR(2) NOT NULL CHECK (source_system IN ('V1','V2')),
    is_41a_crpc               BOOLEAN,
    is_41a_explain_submitted  BOOLEAN,
    date_of_issue_41a         DATE,
    accused_type              VARCHAR(100),
    arrest_flag_method        VARCHAR(40) NOT NULL,
    input_hash                TEXT NOT NULL,
    enrichment_run_id         UUID,
    computed_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE accused_enrichment (
    accused_id                VARCHAR(100) PRIMARY KEY REFERENCES accused_unified(accused_id),
    source_system             VARCHAR(2) NOT NULL CHECK (source_system IN ('V1','V2')),
    accused_category          VARCHAR(100),
    role_in_crime             TEXT,
    accused_type              VARCHAR(40),
    accused_type_method       VARCHAR(40) NOT NULL,
    input_hash                TEXT NOT NULL,
    enrichment_run_id         UUID,
    computed_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE chargesheet_enrichment (
    charge_sheet_id           VARCHAR(100) PRIMARY KEY REFERENCES chargesheets_unified(charge_sheet_id),
    source_system             VARCHAR(2) NOT NULL CHECK (source_system IN ('V1','V2')),
    taken_on_file_date        TIMESTAMPTZ,
    taken_on_file_case_type   TEXT,
    taken_on_file_court_case_no TEXT,
    accused_requested_for_nbw JSONB,
    input_hash                TEXT NOT NULL,
    enrichment_run_id         UUID,
    computed_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE hierarchy_enrichment (
    ps_code                   VARCHAR(20) PRIMARY KEY REFERENCES hierarchy_unified(ps_code),
    sub_zone_code             VARCHAR(50),
    sub_zone_name             VARCHAR(150),
    adg_code                  VARCHAR(50),
    adg_name                  VARCHAR(150),
    input_hash                TEXT NOT NULL,
    enrichment_run_id         UUID,
    computed_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE property_enrichment (
    property_id               VARCHAR(100) PRIMARY KEY REFERENCES properties_unified(property_id),
    property_status           TEXT,
    nature                    TEXT,
    place_of_recovery         TEXT,
    category                  TEXT,
    estimate_value            NUMERIC(15,2),
    recovered_value           NUMERIC(15,2),
    input_hash                TEXT NOT NULL,
    enrichment_run_id         UUID,
    computed_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- FSL stays out of fsl_unified (that table is intentionally not consolidated).
-- These rows are a projection of fsl_source, not a new FSL merge.
CREATE TABLE fsl_enrichment (
    case_property_id          VARCHAR(100) PRIMARY KEY,
    crime_id                  VARCHAR(100),
    mo_id                     TEXT,
    status                    TEXT,
    fsl_no                    TEXT,
    opinion                   TEXT,
    report_received           TEXT,
    date_disposal             TIMESTAMPTZ,
    details_disposal          TEXT,
    place_disposal            TEXT,
    input_hash                TEXT NOT NULL,
    enrichment_run_id         UUID,
    computed_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE disposal_enrichment (
    disposal_id               VARCHAR(100) PRIMARY KEY REFERENCES disposal_unified(disposal_id),
    disposal_type             TEXT,
    disposal                  TEXT,
    case_status               TEXT,
    disposed_at               TIMESTAMPTZ,
    input_hash                TEXT NOT NULL,
    enrichment_run_id         UUID,
    computed_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE drug_extractions (
    extraction_id             VARCHAR(150) PRIMARY KEY,
    crime_id                  VARCHAR(100) NOT NULL REFERENCES crimes_unified(crime_id),
    source_system             VARCHAR(2) NOT NULL CHECK (source_system IN ('V1','V2')),
    provenance                VARCHAR(40) NOT NULL,
    source_record_id          VARCHAR(100),
    raw_drug_name             TEXT,
    primary_drug_name         TEXT,
    drug_form                 VARCHAR(50),
    drug_category             VARCHAR(50),
    raw_quantity              NUMERIC(18,6),
    raw_unit                  VARCHAR(50),
    weight_g                  NUMERIC(18,6),
    weight_kg                 NUMERIC(18,6),
    volume_ml                 NUMERIC(18,6),
    volume_l                  NUMERIC(18,6),
    count_total               NUMERIC(18,6),
    seizure_worth             NUMERIC(18,2),
    is_commercial             BOOLEAN,
    confidence_score          NUMERIC(6,4),
    kb_match_tier             VARCHAR(30),
    input_hash                TEXT NOT NULL,
    enrichment_run_id         UUID,
    computed_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_drug_extractions_crime ON drug_extractions(crime_id);
CREATE INDEX idx_drug_extractions_provenance ON drug_extractions(provenance);

CREATE TABLE ai_extraction_attempts (
    id                        BIGSERIAL PRIMARY KEY,
    crime_id                  VARCHAR(100) NOT NULL,
    input_hash                TEXT NOT NULL,
    model                     TEXT,
    status                    VARCHAR(20) NOT NULL CHECK (status IN ('success','invalid','timeout','error','empty')),
    attempt_count             INT NOT NULL,
    error_message             TEXT,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (crime_id, input_hash, status)
);

CREATE TABLE enrichment_run_log (
    run_id                    UUID PRIMARY KEY,
    started_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at               TIMESTAMPTZ,
    status                    VARCHAR(20) NOT NULL,
    stats                     JSONB
);
