-- ============================================================================
-- ETL-3 UNIFIED DOPAMS SCHEMA — DESIGN ONLY. NOT EXECUTED AGAINST ANY DATABASE.
--
-- This supersedes the original ../schema.sql for the reasons documented in
-- MERGER_REVALIDATION.md §4/§8/§13 and ETL3_MERGER_IMPLEMENTATION_PLAN.md:
--   * accused.person_id must be NULLABLE (V2 has 78 live NULL-person_id rows)
--   * accused_id from either source is NOT a stable natural key across time
--     (V1's MD5-natural-key churn; confirmed one person_code with 69 distinct
--     accused_id values) -- so no source accused_id is reused as the unified
--     PK; (source_system, source_record_id) is used instead, everywhere.
--   * Persons are NEVER auto-merged (DOB populated on <1% of rows both
--     sides) -- persons_unified is a plain union; identity_links holds
--     candidate/confirmed/rejected matches only.
--   * A SOURCE / CURRENT / HISTORY / IDENTITY / CONTROL / RECONCILIATION
--     six-family split replaces the original single-current-state design,
--     because neither source reliably signals "this is a real business
--     change" on its own (V1: natural-key churn; V2: accused/arrest
--     timestamp coupling; both: the 2026-08-24 bulk administrative event).
--
-- Apply this nowhere until the plan in ETL3_MERGER_IMPLEMENTATION_PLAN.md is
-- reviewed and approved. No source database (cctns_v1, cctns-v2) is affected
-- by this file in any way -- it describes a new, separate "dopams_unified"
-- database only.
-- ============================================================================


-- ============================================================================
-- FAMILY 1: SOURCE / OBSERVATION  (append-only, one row per source observation,
-- never UPDATEd, never DELETEd -- this is the raw evidence trail)
-- ============================================================================

-- One of these per unified business entity. Shown once in full; the pattern
-- repeats identically for accused_source, arrests_source, seizures_source,
-- chargesheets_source, properties_source, fsl_source, disposal_source,
-- interrogation_source, hierarchy_source -- only the payload shape differs,
-- so those are declared below with payload-specific columns only where they
-- diverge from this template.

CREATE TABLE crimes_source (
    id                  BIGSERIAL PRIMARY KEY,
    source_system       VARCHAR(2)  NOT NULL CHECK (source_system IN ('V1','V2')),
    source_table        VARCHAR(100) NOT NULL,        -- 'cctns_fir' | 'crimes'
    source_record_id    VARCHAR(100) NOT NULL,         -- fir_reg_num (V1) | crime_id (V2)
    source_run_id       VARCHAR(100) NOT NULL,         -- V1: cctns_v1_etl_run_log.id (text) | V2: accused.etl_run_id-style uuid
    source_created_at   TIMESTAMPTZ,                   -- V1: cctns_fir.created_at | V2: crimes.date_created
    source_modified_at  TIMESTAMPTZ,                   -- V1: cctns_fir.updated_at | V2: crimes.date_modified
    source_fetched_at   TIMESTAMPTZ,                   -- V2: crimes.fetched_at directly; V1: cctns_v1_etl_run_log.started_at for that run
    payload              JSONB NOT NULL,                 -- full raw row as ETL-3 read it, for replay/audit
    consolidation_run_id  UUID NOT NULL,                  -- which ETL-3 run ingested this observation
    ingested_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_system, source_record_id, source_run_id)
);
CREATE INDEX idx_crimes_source_record ON crimes_source(source_system, source_record_id);
CREATE INDEX idx_crimes_source_run ON crimes_source(source_system, source_run_id);

CREATE TABLE persons_source (
    LIKE crimes_source INCLUDING ALL
);
-- source_record_id: V1 accused_details.person_code | V2 persons.person_id

CREATE TABLE accused_source (
    LIKE crimes_source INCLUDING ALL
);
-- source_record_id: V1 cctns_accused.accused_id (NOTE: not stable across time --
--   that instability is exactly why this is an append-only observation log and
--   not treated as a current-state key) | V2 accused.accused_id

CREATE TABLE arrests_source (
    LIKE crimes_source INCLUDING ALL
);
-- V1: fields embedded in cctns_accused_details, synthesized source_record_id
--   = accused_id | V2: arrests.id

CREATE TABLE chargesheets_source (
    LIKE crimes_source INCLUDING ALL
);
-- V1: cctns_court.court_id | V2: chargesheets.id (+ a second feed from
--   charge_sheet_updates.id for the lifecycle-update observations, kept
--   as source_table='charge_sheet_updates' rows in the same table)

CREATE TABLE seizures_source (
    LIKE crimes_source INCLUDING ALL
);
-- V1: synthesized from cctns_accused.accused_id (one seizure per dossier row)
--   | V2: mo_seizures.mo_seizure_id

CREATE TABLE properties_source (
    LIKE crimes_source INCLUDING ALL
);
-- V2 only: properties.property_id -- no V1 equivalent column group

CREATE TABLE fsl_source (
    LIKE crimes_source INCLUDING ALL
);
-- V2 only: fsl_case_property.case_property_id

CREATE TABLE disposal_source (
    LIKE crimes_source INCLUDING ALL
);
-- V2 only: disposal.id

CREATE TABLE interrogation_source (
    LIKE crimes_source INCLUDING ALL
);
-- V2 only: interrogation_reports.interrogation_report_id -- V1's 60 INT_*
--   columns are folded into accused_source's payload JSONB instead (they are
--   per-accused-row fields in V1, not a separate entity)

CREATE TABLE hierarchy_source (
    LIKE crimes_source INCLUDING ALL
);
-- V2 only: hierarchy.ps_code -- reference data, no V1 equivalent


-- ============================================================================
-- FAMILY 2: CURRENT CANONICAL STATE  (one row per unified entity, computed by
-- ETL-3's current-state calculation step -- never written to directly by a
-- naive upsert; always derived by comparing source observations)
-- ============================================================================

CREATE TABLE crimes_unified (
    crime_id             VARCHAR(100) PRIMARY KEY,      -- V1: fir_reg_num | V2: crime_id (no collision risk -- confirmed disjoint formats and 0 overlap)
    source_system        VARCHAR(2) NOT NULL CHECK (source_system IN ('V1','V2')),
    source_record_id     VARCHAR(100) NOT NULL,
    fir_reg_num           VARCHAR(30),
    fir_num               VARCHAR(50),
    fir_date              TIMESTAMPTZ,
    occurrence_year        SMALLINT,
    from_date             TIMESTAMPTZ,
    to_date               TIMESTAMPTZ,
    unit_district          VARCHAR(100),
    ps_name               VARCHAR(150),
    ps_code               VARCHAR(20),
    acts_sections          TEXT,
    brief_facts            TEXT,
    case_status            VARCHAR(100),
    major_head             VARCHAR(150),
    minor_head             VARCHAR(255),
    io_name                VARCHAR(255),
    io_rank                VARCHAR(100),
    additional_json_data     JSONB,
    current_source_run_id    VARCHAR(100) NOT NULL,         -- which source_run_id this current state reflects
    current_as_of           TIMESTAMPTZ NOT NULL,           -- source_modified_at/created_at of the observation that won
    computed_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_system, source_record_id)
);
CREATE INDEX idx_crimes_unified_ps_code ON crimes_unified(ps_code);
CREATE INDEX idx_crimes_unified_case_status ON crimes_unified(case_status);

CREATE TABLE persons_unified (
    person_id             VARCHAR(100) PRIMARY KEY,
    source_system          VARCHAR(2) NOT NULL CHECK (source_system IN ('V1','V2')),
    source_record_id       VARCHAR(100) NOT NULL,
    full_name              VARCHAR(500),
    alias                  VARCHAR(255),
    relative_name           VARCHAR(255),
    gender                 VARCHAR(20),
    date_of_birth            DATE,                          -- populated on <1% of rows both sides -- never relied on for linking, kept for display only
    age                    SMALLINT,
    occupation              VARCHAR(255),
    caste                  VARCHAR(100),
    nationality             VARCHAR(100),
    present_address_text     TEXT,
    permanent_address_text    TEXT,
    phone_number            VARCHAR(20),
    email_id               VARCHAR(255),
    current_source_run_id     VARCHAR(100) NOT NULL,
    current_as_of            TIMESTAMPTZ NOT NULL,
    computed_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_system, source_record_id)
    -- Deliberately: NO merge of V1/V2 rows into one persons_unified row.
    -- Every source person observation gets its own persons_unified row.
    -- Cross-source identity lives ONLY in identity_links (Family 4).
);
CREATE INDEX idx_persons_unified_full_name ON persons_unified(full_name);
CREATE INDEX idx_persons_unified_phone ON persons_unified(phone_number);

CREATE TABLE accused_unified (
    accused_id            VARCHAR(100) PRIMARY KEY,      -- (source_system || ':' || source_record_id), NOT the raw source accused_id reused as a global key
    source_system          VARCHAR(2) NOT NULL CHECK (source_system IN ('V1','V2')),
    source_record_id       VARCHAR(100) NOT NULL,
    crime_id               VARCHAR(100) NOT NULL REFERENCES crimes_unified(crime_id),
    person_id              VARCHAR(100) REFERENCES persons_unified(person_id),  -- NULLABLE -- 78 live V2 rows have no resolvable person_id; do not reject, do not synthesize
    accused_code            VARCHAR(20),
    accused_status           VARCHAR(100),
    is_arrested             BOOLEAN,
    arrested_date            TIMESTAMPTZ,
    is_absconding            BOOLEAN,
    is_apprehended           BOOLEAN,
    is_41a_crpc              BOOLEAN,
    is_ccl                  BOOLEAN,
    modus_operandi            TEXT,
    unlinked_person_flag       BOOLEAN NOT NULL DEFAULT FALSE,  -- TRUE when person_id IS NULL, for cheap filtering without a NULL check in every query
    current_source_run_id       VARCHAR(100) NOT NULL,
    current_as_of              TIMESTAMPTZ NOT NULL,
    computed_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_system, source_record_id)
    -- Deliberately NOT UNIQUE(crime_id, person_id) -- the original schema.sql's
    -- constraint of that shape is what broke against live V2 NULL-person_id
    -- rows (MERGER_REVALIDATION.md §4/§9). A real duplicate-accused-per-crime
    -- question is answered by reconciliation (Family 6), not a hard constraint,
    -- because "is this really a duplicate" requires resolved-identity context
    -- a bare constraint cannot have.
);
CREATE INDEX idx_accused_unified_crime ON accused_unified(crime_id);
CREATE INDEX idx_accused_unified_person ON accused_unified(person_id);
CREATE INDEX idx_accused_unified_unlinked ON accused_unified(unlinked_person_flag) WHERE unlinked_person_flag;

CREATE TABLE arrests_unified (
    arrest_id             VARCHAR(100) PRIMARY KEY,
    source_system          VARCHAR(2) NOT NULL CHECK (source_system IN ('V1','V2')),
    source_record_id       VARCHAR(100) NOT NULL,
    accused_id             VARCHAR(100) NOT NULL REFERENCES accused_unified(accused_id),
    crime_id               VARCHAR(100) NOT NULL REFERENCES crimes_unified(crime_id),
    is_arrested             BOOLEAN,
    arrested_date            TIMESTAMPTZ,
    arrest_ps              VARCHAR(150),
    current_source_run_id       VARCHAR(100) NOT NULL,
    current_as_of              TIMESTAMPTZ NOT NULL,
    computed_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_system, source_record_id)
);

CREATE TABLE chargesheets_unified (
    charge_sheet_id         VARCHAR(100) PRIMARY KEY,  -- '{source_system}:{source_module}:{raw id}', not the raw id
    source_system          VARCHAR(2) NOT NULL CHECK (source_system IN ('V1','V2')),
    source_module          VARCHAR(100),              -- court | chargesheets | charge_sheet_updates
    source_record_id       VARCHAR(100) NOT NULL,
    crime_id               VARCHAR(100) NOT NULL REFERENCES crimes_unified(crime_id),
    chargesheet_no           VARCHAR(50),
    chargesheet_date          TIMESTAMPTZ,
    court_name              VARCHAR(255),
    court_case_num           VARCHAR(100),
    court_disposal_date        TIMESTAMPTZ,
    court_disposal_type        VARCHAR(100),
    court_remarks            TEXT,
    acts_sections             TEXT[],
    accused_person_ids         TEXT[],
    current_source_run_id       VARCHAR(100) NOT NULL,
    current_as_of              TIMESTAMPTZ NOT NULL,
    computed_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_system, source_module, source_record_id)
);
CREATE INDEX idx_chargesheets_unified_crime ON chargesheets_unified(crime_id);

CREATE TABLE seizures_unified (
    seizure_id             VARCHAR(100) PRIMARY KEY,
    source_system          VARCHAR(2) NOT NULL CHECK (source_system IN ('V1','V2')),
    source_record_id       VARCHAR(100) NOT NULL,
    crime_id               VARCHAR(100) NOT NULL REFERENCES crimes_unified(crime_id),
    drug_type              VARCHAR(100),
    description            TEXT,
    quantity               NUMERIC(12,3),
    quantity_unit            VARCHAR(10),
    status                 VARCHAR(50),
    pos_address1            TEXT,
    pos_latitude            DOUBLE PRECISION,              -- V2 only
    pos_longitude            DOUBLE PRECISION,              -- V2 only
    current_source_run_id       VARCHAR(100) NOT NULL,
    current_as_of              TIMESTAMPTZ NOT NULL,
    computed_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_system, source_record_id)
);
CREATE INDEX idx_seizures_unified_crime ON seizures_unified(crime_id);

CREATE TABLE properties_unified (
    property_id            VARCHAR(100) PRIMARY KEY,      -- V2 only
    source_record_id       VARCHAR(100) NOT NULL,
    crime_id               VARCHAR(100) REFERENCES crimes_unified(crime_id),
    current_source_run_id       VARCHAR(100) NOT NULL,
    current_as_of              TIMESTAMPTZ NOT NULL,
    computed_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_record_id)
);

CREATE TABLE fsl_unified (
    case_property_id         VARCHAR(100) PRIMARY KEY,      -- V2 only
    source_record_id         VARCHAR(100) NOT NULL,
    crime_id                 VARCHAR(100) REFERENCES crimes_unified(crime_id),
    current_source_run_id       VARCHAR(100) NOT NULL,
    current_as_of              TIMESTAMPTZ NOT NULL,
    computed_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_record_id)
);

CREATE TABLE disposal_unified (
    disposal_id             VARCHAR(100) PRIMARY KEY,      -- V2 only
    source_record_id         VARCHAR(100) NOT NULL,
    crime_id                 VARCHAR(100) REFERENCES crimes_unified(crime_id),
    current_source_run_id       VARCHAR(100) NOT NULL,
    current_as_of              TIMESTAMPTZ NOT NULL,
    computed_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_record_id)
);

CREATE TABLE interrogation_unified (
    interrogation_report_id     VARCHAR(100) PRIMARY KEY,      -- V2 only
    source_record_id         VARCHAR(100) NOT NULL,
    crime_id                 VARCHAR(100) REFERENCES crimes_unified(crime_id),
    person_id                VARCHAR(100) REFERENCES persons_unified(person_id),  -- NULLABLE -- 11 live V2 rows point at a missing person
    current_source_run_id       VARCHAR(100) NOT NULL,
    current_as_of              TIMESTAMPTZ NOT NULL,
    computed_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_record_id)
);

CREATE TABLE hierarchy_unified (
    ps_code                VARCHAR(20) PRIMARY KEY,       -- V2 only, reference data
    source_record_id       VARCHAR(100) NOT NULL,
    current_source_run_id       VARCHAR(100) NOT NULL,
    current_as_of              TIMESTAMPTZ NOT NULL,
    computed_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_record_id)
);


-- ============================================================================
-- FAMILY 3: HISTORY
-- ============================================================================

CREATE TABLE bulk_event_exclusions (
    event_id        SERIAL PRIMARY KEY,
    source_system     VARCHAR(2) NOT NULL CHECK (source_system IN ('V1','V2')),
    range_start      TIMESTAMPTZ NOT NULL,
    range_end        TIMESTAMPTZ NOT NULL,
    description     TEXT NOT NULL,
    registered_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Seed row (documented, not executed):
-- INSERT INTO bulk_event_exclusions (source_system, range_start, range_end, description)
-- VALUES ('V2', '2026-08-24 00:00:00+00', '2026-08-24 23:59:59+00',
--         'Administrative/maintenance bulk timestamp update -- 86.9% of crimes '
--         'and 86.6% of accused touched in one day, 92% of that in a single UTC '
--         'hour. Not a genuine batch of business-state changes. Excluded from '
--         'change_log inference for this window.');

CREATE TABLE change_log (
    id                  BIGSERIAL PRIMARY KEY,
    entity               VARCHAR(50) NOT NULL,            -- 'crime' | 'person' | 'accused' | 'arrest' | ...
    unified_id            VARCHAR(100) NOT NULL,           -- FK to the relevant *_unified table, not enforced here (entity varies)
    field                VARCHAR(100) NOT NULL,
    old_value             TEXT,
    new_value             TEXT,
    observed_at            TIMESTAMPTZ NOT NULL,            -- source_modified_at/created_at of the observation that introduced this change
    source_system         VARCHAR(2) NOT NULL CHECK (source_system IN ('V1','V2')),
    source_run_id          VARCHAR(100) NOT NULL,
    excluded_bulk_event_id  INT REFERENCES bulk_event_exclusions(event_id),  -- NULL unless this change fell inside a known bulk window
    change_classification   VARCHAR(30) NOT NULL DEFAULT 'business_change'
                          CHECK (change_classification IN (
                              'business_change', 'administrative_bulk_excluded',
                              'etl_reobservation_no_diff', 'initial_observation')),
    logged_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_change_log_entity ON change_log(entity, unified_id);
CREATE INDEX idx_change_log_observed_at ON change_log(observed_at);


-- ============================================================================
-- FAMILY 4: IDENTITY
-- ============================================================================

CREATE TABLE identity_links (
    id                 BIGSERIAL PRIMARY KEY,
    person_a_id          VARCHAR(100) NOT NULL REFERENCES persons_unified(person_id),
    person_b_id          VARCHAR(100) NOT NULL REFERENCES persons_unified(person_id),
    match_basis          TEXT NOT NULL,                  -- e.g. 'name_token+phone', 'name_token+father_name'
    confidence_score       NUMERIC(5,4) NOT NULL,
    status               VARCHAR(20) NOT NULL DEFAULT 'candidate'
                        CHECK (status IN ('candidate', 'confirmed', 'rejected')),
    reviewed_by            VARCHAR(100),                   -- NULL until a human reviews it -- status can only leave 'candidate' via this path
    reviewed_at            TIMESTAMPTZ,
    generated_by_run_id      UUID NOT NULL,
    generated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (person_a_id < person_b_id),                   -- canonical ordering, prevents (A,B) and (B,A) duplicates
    UNIQUE (person_a_id, person_b_id)
);
CREATE INDEX idx_identity_links_status ON identity_links(status);
-- No trigger, no application code path, no default anywhere sets status to
-- 'confirmed' automatically. That transition is a human action only.


-- ============================================================================
-- FAMILY 5: CONTROL PLANE
-- ============================================================================

CREATE TABLE consolidation_cursor (
    source_system              VARCHAR(2) NOT NULL CHECK (source_system IN ('V1','V2')),
    source_module               VARCHAR(100) NOT NULL,         -- V1: entity ('fir'/'accused'/'accused_details'/'court') | V2: module_name
    last_processed_source_run_id  VARCHAR(100),                   -- NOT fetched_at -- see ETL3_MERGER_IMPLEMENTATION_PLAN.md §6
    last_processed_at             TIMESTAMPTZ,                    -- when ETL-3 itself ran, for staleness alerting -- distinct from the above
    status                     VARCHAR(20) NOT NULL DEFAULT 'idle'
                              CHECK (status IN ('idle', 'running', 'failed')),
    PRIMARY KEY (source_system, source_module)
);

CREATE TABLE consolidation_run_log (
    run_id            UUID PRIMARY KEY,
    started_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at         TIMESTAMPTZ,
    status            VARCHAR(30),                      -- 'running' | 'success' | 'partial_failure' | 'failed'
    sources_processed    JSONB,                            -- {(source_system,source_module): source_run_id, ...} processed this run
    rows_observed        INT,
    rows_changed         INT,                              -- change_log rows written
    error_message        TEXT
);


-- ============================================================================
-- FAMILY 6: RECONCILIATION / GAP TRACKING
-- ============================================================================

CREATE TABLE source_gap_ledger (
    id                SERIAL PRIMARY KEY,
    source_system       VARCHAR(2) NOT NULL CHECK (source_system IN ('V1','V2')),
    gap_type           VARCHAR(50) NOT NULL,             -- 'ora_06502_window' | 'fk_retry_capped' | 'address_unresolved' | 'unlinked_accused'
    gap_key            TEXT NOT NULL,                     -- V1: window_start/window_end | V2: record_key or table+id
    first_seen_at        TIMESTAMPTZ NOT NULL,
    status             VARCHAR(20) NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','RESOLVED')),
    source_evidence_table  TEXT,                           -- e.g. 'cctns_v1_failed_fetch_window', 'etl_bookkeeping'
    copied_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_system, gap_type, gap_key)
);
-- Seeded (not executed here) from, verbatim, not re-derived:
--   V1 cctns_v1_failed_fetch_window: 180 rows, all status=OPEN
--   V2 etl_bookkeeping kind='fk_retry': chargesheets (90), updated_chargesheet (168),
--     fsl_case_property (894 unresolved of 2,887 attempts)
--   V2 etl_bookkeeping kind='failure', module_name='etl-address': 1,627 rows
--   V2 accused rows with NULL person_id: 78 rows, gap_type='unlinked_accused'

CREATE TABLE reconciliation_run_log (
    id                 BIGSERIAL PRIMARY KEY,
    run_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_system        VARCHAR(2) NOT NULL CHECK (source_system IN ('V1','V2')),
    source_table         VARCHAR(100) NOT NULL,
    source_count         INT NOT NULL,
    observed_count        INT NOT NULL,                    -- rows in the corresponding *_source table for this source
    unified_count         INT NOT NULL,                    -- rows in the corresponding *_unified table
    delta               INT NOT NULL,
    status              VARCHAR(40) NOT NULL              -- EXPECTED | UNRESOLVED | MISMATCH | INTENTIONALLY_EXCLUDED | KNOWN_SOURCE_LIMITATION | DATA_QUALITY | UNRESOLVED_RELATIONSHIP | ETL_DEFECT
);


-- ============================================================================
-- Explicitly out of scope, same reasoning as the original schema.sql:
--   V2's own etl_bookkeeping, etl_run_state, geo_reference, geo_countries,
--   file_media_bookkeeping stay in cctns-v2 only; ETL-3 reads them read-only
--   but does not duplicate them here. V1's airflow.* schema stays in cctns_v1
--   only.
-- ============================================================================
