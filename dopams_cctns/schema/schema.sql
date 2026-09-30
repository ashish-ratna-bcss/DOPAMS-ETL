-- ============================================================================
-- CCTNS Unified Schema v2 — merges the REAL live cctns_v1 and cctns-v2
-- databases (see ../../cctnsv1/schema/ and ../../cctnsv2/schema/ for the
-- ground-truth dumps this is built from). Replaces the first version of
-- this file, which was written before those live schemas were pulled.
--
-- Design changes from v1 of this file:
--   * V2's live schema proved that arrays/JSONB beat child tables for
--     repeating structured data (chargesheet acts/accused, interrogation
--     family/associate/drug/financial/telecom details) -- this version
--     follows that pattern instead of normalizing everything into child
--     tables.
--   * V1 has ZERO normalization (60 flat INT_* interrogation columns, two
--     independent unlinked accused tables). Those flat V1 columns are
--     folded into the SAME array columns V2 already uses, not into a new
--     child-table design.
--   * V1 IDs are bigint/varchar sequences; V2 IDs are Mongo ObjectId
--     strings. VARCHAR(50) ids + source_system/source_record_id (as in v1
--     of this file) remain the mechanism for holding both without loss and
--     tracing every row back to its origin.
--   * V2's own ETL-operations tables (etl_bookkeeping, file_media_bookkeeping,
--     geo_reference, geo_countries, hierarchy) are NOT duplicated here --
--     those are V2 ETL infrastructure, not business data to merge with V1.
--     They stay in cctnsv2's own database only. See README.md.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- 1. Crimes / FIR
-- V1 source: cctns_fir (fir_reg_num PK, unit/ps_name as plain text -- V1 has
--   no organisational hierarchy codes at all)
-- V2 source: crimes (crime_id PK, ps_code FK to hierarchy, additional_json_data
--   catch-all for fields with no dedicated column, e.g. GD entry, IO_MOBILE,
--   OCCURRENCE_DATE, PLACE_OF_OFFENCE, COMPLAINANT_ID)
-- ----------------------------------------------------------------------------
CREATE TABLE crimes (
    crime_id             VARCHAR(50) PRIMARY KEY,      -- V1: fir_reg_num | V2: crime_id
    source_system        VARCHAR(2) NOT NULL CHECK (source_system IN ('V1', 'V2')),
    source_record_id     VARCHAR(50) NOT NULL,
    fir_reg_num           VARCHAR(30),
    fir_num               VARCHAR(50),                  -- V1: fir_no | V2: fir_num
    fir_date              TIMESTAMPTZ,                   -- V1: reg_dt | V2: fir_date
    fir_type              VARCHAR(50),                   -- V2 only: Regular/Suo-moto
    occurrence_year        SMALLINT,                      -- V1: reg_year/year
    from_date             TIMESTAMPTZ,
    to_date               TIMESTAMPTZ,
    unit_district          VARCHAR(100),                  -- V1: unit (district/commissionerate name, free text)
    ps_name               VARCHAR(150),                  -- V1: ps_name (free text) | V2: via ps_code -> hierarchy.ps_name
    ps_code               VARCHAR(20),                   -- V2 only: FK into cctnsv2's own hierarchy table (not duplicated here)
    acts_sections          TEXT,                          -- V1: act_sec/section_of_law | V2: acts_sections
    brief_facts            TEXT,                          -- V1: fir_contents | V2: brief_facts
    case_status            VARCHAR(100),                  -- V1: fir_status | V2: case_status
    major_head             VARCHAR(150),
    minor_head             VARCHAR(255),
    crime_type             VARCHAR(100),                  -- V2 only
    io_name                VARCHAR(255),                  -- V2 only: Investigating Officer
    io_rank                VARCHAR(100),
    class_classification     VARCHAR(50),                   -- V2 only
    fir_copy_file_id        VARCHAR(100),                  -- V1: attach_path/dms_file_name | V2: fir_copy
    additional_json_data     JSONB,                         -- V2's catch-all for unmapped API fields; empty for V1 rows
    date_created            TIMESTAMPTZ,
    date_modified            TIMESTAMPTZ,
    UNIQUE (source_system, source_record_id)
);

CREATE INDEX idx_crimes_ps_code ON crimes(ps_code);
CREATE INDEX idx_crimes_fir_reg_num ON crimes(fir_reg_num);
CREATE INDEX idx_crimes_case_status ON crimes(case_status);


-- ----------------------------------------------------------------------------
-- 2. Persons
-- V1 source: person fields embedded directly in cctns_accused / cctns_accused_details
--   (V1 has no standalone person table -- one row per accused-per-crime, so
--   the same real person appears multiple times if accused in multiple FIRs;
--   dedup across those rows, and across V2, is an ETL-time decision, not
--   something this schema resolves automatically)
-- V2 source: persons (person_id PK, true 1:1 per CCTNS PERSON_ID)
-- ----------------------------------------------------------------------------
CREATE TABLE persons (
    person_id                VARCHAR(50) PRIMARY KEY,     -- V1: synthesize from accused_id/person_code | V2: person_id
    source_system             VARCHAR(2) NOT NULL CHECK (source_system IN ('V1', 'V2')),
    source_record_id          VARCHAR(50) NOT NULL,
    full_name                 VARCHAR(500),                 -- V1: accused_name | V2: full_name
    raw_full_name               VARCHAR(500),                 -- V2 only: unparsed source name
    surname                   VARCHAR(255),                 -- V2 only
    alias                     VARCHAR(255),                 -- V1: alias_name | V2: alias
    relation_type              VARCHAR(50),                  -- relation the relative_name below holds (usually "Father")
    relative_name              VARCHAR(255),                 -- V1: father_name | V2: relative_name
    gender                    VARCHAR(20),
    date_of_birth               DATE,                         -- V1: dob | V2: date_of_birth
    age                       SMALLINT,
    is_died                   BOOLEAN DEFAULT FALSE,        -- V2 only
    occupation                VARCHAR(255),                 -- V1: accused_occupation | V2: occupation
    education_qualification     VARCHAR(255),                 -- V2 only
    caste                     VARCHAR(100),
    sub_caste                 VARCHAR(100),                 -- V2 only
    religion                  VARCHAR(100),                 -- V2 only
    nationality                VARCHAR(100),
    designation                VARCHAR(255),                 -- V2 only
    place_of_work               VARCHAR(500),                 -- V2 only
    present_address_text        TEXT,                         -- V1: present_address (free text)
    present_house_no            VARCHAR(255),                 -- V2 structured present_* fields
    present_street_road_no       VARCHAR(255),
    present_ward_colony          VARCHAR(255),
    present_landmark_milestone    VARCHAR(255),
    present_locality_village     VARCHAR(255),
    present_area_mandal          VARCHAR(255),
    present_district            VARCHAR(255),
    present_state_ut            VARCHAR(255),
    present_pin_code            VARCHAR(20),
    permanent_address_text      TEXT,                         -- V1: permanent_address (free text)
    permanent_house_no          VARCHAR(255),                 -- V2 structured permanent_* fields
    permanent_district          VARCHAR(255),
    permanent_state_ut          VARCHAR(255),
    permanent_pin_code          VARCHAR(20),
    phone_number               VARCHAR(20),                  -- V1: mobile_1 | V2: phone_number
    telephone_residence         VARCHAR(20),                  -- V1 only
    social_media_account         VARCHAR(255),                 -- V1 only
    email_id                  VARCHAR(255),
    domicile_classification      VARCHAR(50),                  -- V2 only: derived post-ingestion
    gender_confidence           NUMERIC(4,3),                  -- V2 only: derived
    gender_source               VARCHAR(20),                   -- V2 only: derived
    geo_resolution_source        TEXT,                         -- V2 only: derived (address enrichment)
    geo_resolution_confidence     REAL,                         -- V2 only: derived
    date_created               TIMESTAMPTZ,
    date_modified               TIMESTAMPTZ,
    UNIQUE (source_system, source_record_id)
);

CREATE INDEX idx_persons_full_name ON persons(full_name);
CREATE INDEX idx_persons_phone_number ON persons(phone_number);
CREATE INDEX idx_persons_present_district ON persons(present_district);


-- ----------------------------------------------------------------------------
-- 3. Identity proofs
-- V1 source: cctns_accused flat Y/N-ish text columns (aadhar_card, ration_card,
--   voter_card, passport, pan_card, electricity_connection, telephone_connection,
--   gas_connection, driving_license, other_proofs)
-- V2 source: no dedicated table in the live schema -- IDENTITY_DETAILS from the
--   API lands in file_media_bookkeeping (source_field='IDENTITY_DETAILS') in
--   cctnsv2's own database, not duplicated here
-- ----------------------------------------------------------------------------
CREATE TABLE identity_details (
    identity_id     SERIAL PRIMARY KEY,
    person_id        VARCHAR(50) NOT NULL REFERENCES persons(person_id) ON DELETE CASCADE,
    identity_type     VARCHAR(30) NOT NULL CHECK (identity_type IN (
                      'AADHAR_CARD', 'RATION_CARD', 'VOTER_CARD', 'PASSPORT', 'PAN_CARD',
                      'ELECTRICITY_CONNECTION', 'TELEPHONE_CONNECTION', 'GAS_CONNECTION',
                      'DRIVING_LICENSE', 'OTHER_PROOF')),
    identity_value     TEXT
);


-- ----------------------------------------------------------------------------
-- 4. Physical features & deformities
-- V1 source: cctns_accused (build_type, complexion_type, height_ll_feet,
--   height_from_cm, face_type, lips_type, nose_type, cheek_type, teeth_type,
--   beard_type, eye_type, eye_brow_thickness, eye_color, hair_color, hair_style,
--   ears_type_cd, other_identify_marks + deformity flags eye_blind, legs_missing,
--   toe_extra, ears_missing, toe_missing, deaf_dumb, arms_missing)
-- V2 source: accused (beard, build, color, ear, eyes, face, hair, height,
--   leucoderma, mole, mustache, nose, teeth)
-- ----------------------------------------------------------------------------
CREATE TABLE physical_features (
    person_id            VARCHAR(50) PRIMARY KEY REFERENCES persons(person_id) ON DELETE CASCADE,
    height               VARCHAR(50),                   -- V1: height_ll_feet/height_from_cm | V2: height
    build                VARCHAR(100),                  -- V1: build_type | V2: build
    complexion            VARCHAR(100),                  -- V1: complexion_type | V2: color
    face                 VARCHAR(100),                  -- V1: face_type/lips_type/cheek_type combined | V2: face
    nose                 VARCHAR(100),                  -- V1: nose_type | V2: nose
    teeth                VARCHAR(100),                  -- V1: teeth_type | V2: teeth
    beard                VARCHAR(100),                  -- V1: beard_type | V2: beard
    mustache              VARCHAR(100),                  -- V2 only
    eyes                 VARCHAR(100),                  -- V1: eye_type/eye_color | V2: eyes
    eye_brow_thickness      VARCHAR(50),                   -- V1 only
    ear                  VARCHAR(100),                  -- V1: ears_type_cd | V2: ear
    hair                 VARCHAR(100),                  -- V1: hair_color | V2: hair
    hair_style             VARCHAR(50),                   -- V1 only
    mole                 VARCHAR(100),                  -- V2 only
    leucoderma            VARCHAR(100),                  -- V2 only
    identification_marks    TEXT                           -- V1: other_identify_marks
);

CREATE TABLE physical_deformities (
    deformity_id     SERIAL PRIMARY KEY,
    person_id         VARCHAR(50) NOT NULL REFERENCES persons(person_id) ON DELETE CASCADE,
    deformity_type     VARCHAR(30) NOT NULL CHECK (deformity_type IN (
                       'EYE_BLIND', 'EARS_MISSING', 'DEAF_DUMB', 'ARMS_MISSING',
                       'LEGS_MISSING', 'TOE_EXTRA', 'TOE_MISSING'))
);


-- ----------------------------------------------------------------------------
-- 5. Family & associates
-- Both systems' "12 relations x 5 fields" style data unified into ONE set of
-- parallel array columns, following V2's own family_history_*/associate_*
-- array pattern (interrogation_reports table). V1's 12 fixed INT_* column
-- groups (father/mother/wife/son/daughter/brother/sister/FIL/MIL/uncle/
-- aunt/friend) get loaded as up-to-12 array positions per person, exactly
-- like V2's already-variable-length arrays.
-- ----------------------------------------------------------------------------
CREATE TABLE family_associates (
    person_id                       VARCHAR(50) PRIMARY KEY REFERENCES persons(person_id) ON DELETE CASCADE,
    family_relation_types              TEXT[],     -- e.g. {Father,Mother,Wife,...}
    family_names                     TEXT[],
    family_mobile_numbers               TEXT[],
    family_occupations                 TEXT[],
    family_addresses                   TEXT[],
    family_criminal_background           BOOLEAN[],  -- V2 only
    family_is_alive                    BOOLEAN[],  -- V2 only
    associate_person_ids                TEXT[],     -- V2 only
    associate_gangs                    TEXT[],     -- V2 only
    associate_relations                 TEXT[]      -- V2 only
);


-- ----------------------------------------------------------------------------
-- 6. Accused (crime <-> person junction, with accused/arrest attributes)
-- V1 source: cctns_accused (act_sec/weight/drug fields live here too, but
--   those belong to seizures -- see mo_seizures below) + cctns_accused_details
--   (is_arrested, arrest_surrender_dt, person_code)
-- V2 source: accused (accused_code, seq_num, accused_status, is_ccl) + arrests
--   (is_arrested, arrested_date, is_41a_crpc, is_absconding, ...)
-- ----------------------------------------------------------------------------
CREATE TABLE accused (
    accused_id              VARCHAR(50) PRIMARY KEY,     -- V1: accused_id (bigint as text) | V2: accused_id
    crime_id                 VARCHAR(50) NOT NULL REFERENCES crimes(crime_id) ON DELETE CASCADE,
    person_id                 VARCHAR(50) NOT NULL REFERENCES persons(person_id) ON DELETE CASCADE,
    accused_code              VARCHAR(20),                  -- V2 only: A1, A2...
    seq_num                   VARCHAR(50),                  -- V2 only
    accused_type               VARCHAR(50) DEFAULT 'Accused',
    accused_status             VARCHAR(100),                 -- V2 only
    is_ccl                    BOOLEAN DEFAULT FALSE,        -- V2 only
    modus_operandi              TEXT,                         -- V1: modus_operandi | V2: mo_seizures/interrogation_reports
    previous_offences_confessed   TEXT,                         -- V1: historysheet_y_n | V2: interrogation_reports
    is_arrested               BOOLEAN,                      -- V1: is_arrested | V2: arrests.is_arrested
    arrested_date               TIMESTAMPTZ,                   -- V1: arrest_surrender_dt | V2: arrests.arrested_date
    arrest_ps                 VARCHAR(150),                  -- V1 only
    is_41a_crpc                BOOLEAN,                      -- V2 only
    is_apprehended             BOOLEAN,                      -- V2 only
    is_absconding              BOOLEAN,                      -- V2 only
    is_died                   BOOLEAN,                      -- V2 only
    date_created               TIMESTAMPTZ,
    date_modified               TIMESTAMPTZ,
    UNIQUE (crime_id, person_id)
);

CREATE INDEX idx_accused_crime_id ON accused(crime_id);
CREATE INDEX idx_accused_person_id ON accused(person_id);


-- ----------------------------------------------------------------------------
-- 7. Seizures (drugs/material objects)
-- V1 source: cctns_accused flat drug columns (drug_particulars, weight_gm,
--   weight_kg, drug_desc, drug_status, drug_type, estimated_value,
--   area_operation, location_type, drug_place_type, paking_making_desc,
--   packets_count) -- one seizure per crime in V1's flat model
-- V2 source: mo_seizures (one row per MO_ID, multiple per crime, with GPS)
-- ----------------------------------------------------------------------------
CREATE TABLE mo_seizures (
    mo_seizure_id       VARCHAR(50) PRIMARY KEY,      -- V1: synthesize from accused_id | V2: mo_seizure_id
    crime_id              VARCHAR(50) NOT NULL REFERENCES crimes(crime_id) ON DELETE CASCADE,
    mo_id                 VARCHAR(50),                    -- V2 only
    drug_type             VARCHAR(100),                   -- V1: drug_type | V2: sub_type
    description           TEXT,                          -- V1: drug_desc/drug_particulars/paking_making_desc/packets_count | V2: description
    quantity              NUMERIC(12,3),                  -- V1: weight_gm/weight_kg unified | V2: parsed from description
    quantity_unit           VARCHAR(10),
    purchase_amount_inr      NUMERIC(14,2),                  -- V1: estimated_value | V2: (from interrogation_reports.drug_purchase_amounts_inr)
    status                VARCHAR(50),                    -- V1: drug_status
    seized_from            VARCHAR(150),                   -- V2 only
    seized_at              TIMESTAMPTZ,                    -- V2 only
    seized_by              TEXT,                          -- V2 only
    strength_of_evidence     TEXT,                          -- V2 only
    pos_address1            TEXT,                          -- V1: area_operation | V2: pos_address1
    pos_description          TEXT,                          -- V1: location_type/drug_place_type | V2: pos_description
    pos_latitude            DOUBLE PRECISION,                -- V2 only: GPS
    pos_longitude           DOUBLE PRECISION,                -- V2 only: GPS
    date_created            TIMESTAMPTZ,
    date_modified            TIMESTAMPTZ
);

CREATE INDEX idx_mo_seizures_crime_id ON mo_seizures(crime_id);


-- ----------------------------------------------------------------------------
-- 8. Chargesheets & court disposal
-- V1 source: cctns_court (one row per FIR: chargesheet_dt, court_name,
--   court_case_num, court_disposal_dt, court_disposal_type, court_remarks,
--   attach_path, dms_file_name)
-- V2 source: chargesheets (acts/accused as parallel arrays, per V2's own
--   design) + charge_sheet_updates (takenOnFile.* trial progress)
-- ----------------------------------------------------------------------------
CREATE TABLE chargesheets (
    charge_sheet_id         VARCHAR(50) PRIMARY KEY,      -- V1: synthesize from court_id | V2: charge_sheet_id
    crime_id                 VARCHAR(50) NOT NULL REFERENCES crimes(crime_id) ON DELETE CASCADE,
    chargesheet_no             VARCHAR(50),                    -- V1: (none, uses court_case_num) | V2: chargesheet_no
    chargesheet_no_icjs         VARCHAR(50),                    -- V2 only
    chargesheet_date            TIMESTAMPTZ,                     -- V1: chargesheet_dt | V2: chargesheet_date
    chargesheet_type            VARCHAR(50),                    -- V2 only
    court_name                VARCHAR(255),                   -- V1: court_name | V2: court_name
    court_case_num             VARCHAR(100),                   -- V1: court_case_num | V2: charge_sheet_updates.taken_on_file_court_case_no
    court_case_type             VARCHAR(20),                    -- V2 only: CC / SC NDPS
    taken_on_file_date          TIMESTAMPTZ,                     -- V2 only
    court_disposal_date          TIMESTAMPTZ,                     -- V1: court_disposal_dt
    court_disposal_type          VARCHAR(100),                   -- V1: court_disposal_type | V2: charge_sheet_updates.charge_sheet_status
    court_remarks              TEXT,                           -- V1 only
    is_ccl                    BOOLEAN DEFAULT FALSE,          -- V2 only
    is_esigned                 BOOLEAN DEFAULT FALSE,          -- V2 only
    file_id                   VARCHAR(100),                   -- V1: attach_path/dms_file_name | V2: uploadChargeSheet.fileId
    acts_descriptions           TEXT[],                         -- V2 only (parallel arrays)
    acts_sections               TEXT[],
    acts_section_descriptions     TEXT[],
    accused_person_ids           TEXT[],
    accused_charge_statuses       TEXT[],
    date_created               TIMESTAMPTZ,
    date_modified               TIMESTAMPTZ
);

CREATE INDEX idx_chargesheets_crime_id ON chargesheets(crime_id);


-- ----------------------------------------------------------------------------
-- Explicitly out of scope for this unified BUSINESS schema (stay V2-only):
--   hierarchy, file_media_bookkeeping, etl_bookkeeping, etl_run_state,
--   geo_reference, geo_countries, disposal, fsl_case_property,
--   interrogation_reports (financial/telecom/consumer/supply-chain arrays)
-- These are either V2 ETL operational tables with no V1 counterpart, or
-- V2 intelligence modules V1 never had any equivalent of. See README.md
-- for the reasoning and how to extend this schema if they're needed later.
-- ----------------------------------------------------------------------------
