-- =============================================================================
-- CCTNS V1 Live Database Schema
-- Source: Live PostgreSQL database on dopams-new (192.168.103.106)
-- Cleaned Schema (structure-only)
-- =============================================================================
--
--

CREATE SCHEMA cctns;

CREATE FUNCTION cctns.cctns_v1_log_row_changes() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    old_j jsonb := to_jsonb(OLD);
    new_j jsonb := to_jsonb(NEW);
    pk_col text := TG_ARGV[0];
    rec_key text := new_j ->> pk_col;
    k text;
BEGIN
    FOR k IN SELECT jsonb_object_keys(new_j) LOOP
        IF k IN ('created_at', 'updated_at', 'natural_key') THEN
            CONTINUE;
        END IF;
        IF old_j -> k IS DISTINCT FROM new_j -> k THEN
            INSERT INTO cctns.cctns_v1_audit_log (table_name, record_key, field_name, old_value, new_value)
            VALUES (TG_TABLE_NAME, rec_key, k, old_j ->> k, new_j ->> k);
        END IF;
    END LOOP;
    RETURN NEW;
END;
$$;

CREATE FUNCTION cctns.trg_cctns_accused_details_natural_key() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.natural_key :=
        COALESCE(NEW.fir_reg_num, '') || '|' ||
        COALESCE(NEW.person_code, '') || '|' ||
        COALESCE(NEW.accused_name, '') || '|' ||
        COALESCE(NEW.gender, '') || '|' ||
        COALESCE(NEW.age::text, '') || '|' ||
        COALESCE(NEW.father_name, '') || '|' ||
        COALESCE(NEW.mobile_1, '') || '|' ||
        COALESCE(NEW.accused_present_address, '') || '|' ||
        COALESCE(NEW.accused_permanent_address, '') || '|' ||
        COALESCE(NEW.is_arrested, '') || '|' ||
        COALESCE(NEW.arrest_surrender_dt::text, '');
    RETURN NEW;
END;
$$;

CREATE FUNCTION cctns.trg_cctns_accused_natural_key() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.natural_key := md5(
        COALESCE(NEW.fir_reg_num, '') || '|' ||
        COALESCE(NEW.district, '') || '|' ||
        COALESCE(NEW.ps, '') || '|' ||
        COALESCE(NEW.fir_no, '') || '|' ||
        COALESCE(NEW.reg_dt::text, '') || '|' ||
        COALESCE(NEW.year::text, '') || '|' ||
        COALESCE(NEW.fir_status, '') || '|' ||
        COALESCE(NEW.act_sec, '') || '|' ||
        COALESCE(NEW.major_head, '') || '|' ||
        COALESCE(NEW.minor_head, '') || '|' ||
        COALESCE(NEW.from_dt::text, '') || '|' ||
        COALESCE(NEW.to_dt::text, '') || '|' ||
        COALESCE(NEW.ps_recv_inform_dt::text, '') || '|' ||
        COALESCE(NEW.drug_particulars, '') || '|' ||
        COALESCE(NEW.weight_gm::text, '') || '|' ||
        COALESCE(NEW.drug_desc, '') || '|' ||
        COALESCE(NEW.drug_status, '') || '|' ||
        COALESCE(NEW.drug_type, '') || '|' ||
        COALESCE(NEW.estimated_value, '') || '|' ||
        COALESCE(NEW.area_operation, '') || '|' ||
        COALESCE(NEW.location_type, '') || '|' ||
        COALESCE(NEW.drug_place_type, '') || '|' ||
        COALESCE(NEW.paking_making_desc, '') || '|' ||
        COALESCE(NEW.packets_count::text, '') || '|' ||
        COALESCE(NEW.accused_name, '') || '|' ||
        COALESCE(NEW.age::text, '') || '|' ||
        COALESCE(NEW.father_name, '') || '|' ||
        COALESCE(NEW.accused_occupation, '') || '|' ||
        COALESCE(NEW.gender, '') || '|' ||
        COALESCE(NEW.caste, '') || '|' ||
        COALESCE(NEW.nationality, '') || '|' ||
        COALESCE(NEW.telephone_residence, '') || '|' ||
        COALESCE(NEW.alias_name, '') || '|' ||
        COALESCE(NEW.dob::text, '') || '|' ||
        COALESCE(NEW.mobile_1, '') || '|' ||
        COALESCE(NEW.email, '') || '|' ||
        COALESCE(NEW.social_media_accnt, '') || '|' ||
        COALESCE(NEW.aadhar_card, '') || '|' ||
        COALESCE(NEW.ration_card, '') || '|' ||
        COALESCE(NEW.voter_card, '') || '|' ||
        COALESCE(NEW.passport, '') || '|' ||
        COALESCE(NEW.pan_card, '') || '|' ||
        COALESCE(NEW.electricity_connection, '') || '|' ||
        COALESCE(NEW.telephone_connection, '') || '|' ||
        COALESCE(NEW.gas_connection, '') || '|' ||
        COALESCE(NEW.driving_license, '') || '|' ||
        COALESCE(NEW.other_proofs, '') || '|' ||
        COALESCE(NEW.present_address, '') || '|' ||
        COALESCE(NEW.permanent_address, '') || '|' ||
        COALESCE(NEW.arrest_surrender_dt::text, '') || '|' ||
        COALESCE(NEW.build_type, '') || '|' ||
        COALESCE(NEW.complexion_type, '') || '|' ||
        COALESCE(NEW.height_ll_feet, '') || '|' ||
        COALESCE(NEW.height_from_cm, '') || '|' ||
        COALESCE(NEW.face_type, '') || '|' ||
        COALESCE(NEW.lips_type, '') || '|' ||
        COALESCE(NEW.nose_type, '') || '|' ||
        COALESCE(NEW.cheek_type, '') || '|' ||
        COALESCE(NEW.teeth_type, '') || '|' ||
        COALESCE(NEW.beard_type, '') || '|' ||
        COALESCE(NEW.eye_type, '') || '|' ||
        COALESCE(NEW.eye_brow_thickness, '') || '|' ||
        COALESCE(NEW.eye_blind, '') || '|' ||
        COALESCE(NEW.eye_color, '') || '|' ||
        COALESCE(NEW.legs_missing, '') || '|' ||
        COALESCE(NEW.toe_extra, '') || '|' ||
        COALESCE(NEW.ears_missing, '') || '|' ||
        COALESCE(NEW.toe_missing, '') || '|' ||
        COALESCE(NEW.deaf_dumb, '') || '|' ||
        COALESCE(NEW.arms_missing, '') || '|' ||
        COALESCE(NEW.hair_color, '') || '|' ||
        COALESCE(NEW.hair_style, '') || '|' ||
        COALESCE(NEW.weight_kg, '') || '|' ||
        COALESCE(NEW.ears_type_cd, '') || '|' ||
        COALESCE(NEW.other_identify_marks, '') || '|' ||
        COALESCE(NEW.arrest_ps, '') || '|' ||
        COALESCE(NEW.modus_operandi, '') || '|' ||
        COALESCE(NEW.historysheet_y_n, '') || '|' ||
        COALESCE(NEW.photo_y_n, '') || '|' ||
        COALESCE(NEW.int_relation_type_father, '') || '|' ||
        COALESCE(NEW.int_father_name, '') || '|' ||
        COALESCE(NEW.int_father_mobile_no, '') || '|' ||
        COALESCE(NEW.int_father_occupation, '') || '|' ||
        COALESCE(NEW.int_father_address, '') || '|' ||
        COALESCE(NEW.int_relation_type_mother, '') || '|' ||
        COALESCE(NEW.int_mother_name, '') || '|' ||
        COALESCE(NEW.int_mother_mobile_no, '') || '|' ||
        COALESCE(NEW.int_mother_occupation, '') || '|' ||
        COALESCE(NEW.int_mother_address, '') || '|' ||
        COALESCE(NEW.int_relation_type_wife, '') || '|' ||
        COALESCE(NEW.int_wife_name, '') || '|' ||
        COALESCE(NEW.int_wife_mobile_no, '') || '|' ||
        COALESCE(NEW.int_wife_occupation, '') || '|' ||
        COALESCE(NEW.int_wife_address, '') || '|' ||
        COALESCE(NEW.int_relation_type_son, '') || '|' ||
        COALESCE(NEW.int_son_name, '') || '|' ||
        COALESCE(NEW.int_son_mobile_no, '') || '|' ||
        COALESCE(NEW.int_son_occupation, '') || '|' ||
        COALESCE(NEW.int_son_address, '') || '|' ||
        COALESCE(NEW.int_relation_type_daughter, '') || '|' ||
        COALESCE(NEW.int_daughter_name, '') || '|' ||
        COALESCE(NEW.int_daughter_mobile_no, '') || '|' ||
        COALESCE(NEW.int_daughter_occupation, '') || '|' ||
        COALESCE(NEW.int_daughter_address, '') || '|' ||
        COALESCE(NEW.int_relation_type_brother, '') || '|' ||
        COALESCE(NEW.int_brother_name, '') || '|' ||
        COALESCE(NEW.int_brother_mobile_no, '') || '|' ||
        COALESCE(NEW.int_brother_occupation, '') || '|' ||
        COALESCE(NEW.int_brother_address, '') || '|' ||
        COALESCE(NEW.int_relation_type_sister, '') || '|' ||
        COALESCE(NEW.int_sister_name, '') || '|' ||
        COALESCE(NEW.int_sister_mobile_no, '') || '|' ||
        COALESCE(NEW.int_sister_occupation, '') || '|' ||
        COALESCE(NEW.int_sister_address, '') || '|' ||
        COALESCE(NEW.int_relation_type_fil, '') || '|' ||
        COALESCE(NEW.int_fil_name, '') || '|' ||
        COALESCE(NEW.int_fil_mobile_no, '') || '|' ||
        COALESCE(NEW.int_fil_occupation, '') || '|' ||
        COALESCE(NEW.int_fil_address, '') || '|' ||
        COALESCE(NEW.int_relation_type_mil, '') || '|' ||
        COALESCE(NEW.int_mil_name, '') || '|' ||
        COALESCE(NEW.int_mil_mobile_no, '') || '|' ||
        COALESCE(NEW.int_mil_occupation, '') || '|' ||
        COALESCE(NEW.int_mil_address, '') || '|' ||
        COALESCE(NEW.int_relation_type_uncle, '') || '|' ||
        COALESCE(NEW.int_uncle_name, '') || '|' ||
        COALESCE(NEW.int_uncle_mobile_no, '') || '|' ||
        COALESCE(NEW.int_uncle_occupation, '') || '|' ||
        COALESCE(NEW.int_uncle_address, '') || '|' ||
        COALESCE(NEW.int_relation_type_aunt, '') || '|' ||
        COALESCE(NEW.int_aunt_name, '') || '|' ||
        COALESCE(NEW.int_aunt_mobile_no, '') || '|' ||
        COALESCE(NEW.int_aunt_occupation, '') || '|' ||
        COALESCE(NEW.int_aunt_address, '') || '|' ||
        COALESCE(NEW.int_relation_type_friend, '') || '|' ||
        COALESCE(NEW.int_friend_name, '') || '|' ||
        COALESCE(NEW.int_friend_mobile_no, '') || '|' ||
        COALESCE(NEW.int_friend_occupation, '') || '|' ||
        COALESCE(NEW.int_friend_address, '') || '|' ||
        COALESCE(NEW.fir_contents, '')
    );
    RETURN NEW;
END;
$$;

CREATE FUNCTION cctns.trg_cctns_court_natural_key() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.natural_key :=
        COALESCE(NEW.fir_reg_num, '') || '|' ||
        COALESCE(NEW.chargesheet_dt::text, '') || '|' ||
        COALESCE(NEW.court_disposal_dt::text, '') || '|' ||
        COALESCE(NEW.court_case_num, '') || '|' ||
        COALESCE(NEW.court_name, '') || '|' ||
        COALESCE(NEW.court_disposal_type, '') || '|' ||
        COALESCE(NEW.court_remarks, '');
    RETURN NEW;
END;
$$;

SET default_tablespace = '';

SET default_table_access_method = heap;

CREATE TABLE cctns.cctns_accused (
    accused_id bigint NOT NULL,
    district text,
    ps text,
    fir_no text,
    reg_dt timestamp without time zone,
    year integer,
    fir_reg_num character varying(20) NOT NULL,
    fir_status text,
    act_sec text,
    major_head text,
    minor_head text,
    from_dt timestamp without time zone,
    to_dt timestamp without time zone,
    ps_recv_inform_dt timestamp without time zone,
    drug_particulars text,
    weight_gm integer,
    drug_desc text,
    drug_status text,
    drug_type text,
    estimated_value text,
    area_operation text,
    location_type text,
    drug_place_type text,
    paking_making_desc text,
    packets_count integer,
    accused_name text,
    age integer,
    father_name text,
    accused_occupation text,
    gender text,
    caste text,
    nationality text,
    telephone_residence text,
    alias_name text,
    dob timestamp without time zone,
    mobile_1 text,
    email text,
    social_media_accnt text,
    aadhar_card text,
    ration_card text,
    voter_card text,
    passport text,
    pan_card text,
    electricity_connection text,
    telephone_connection text,
    gas_connection text,
    driving_license text,
    other_proofs text,
    present_address text,
    permanent_address text,
    arrest_surrender_dt timestamp without time zone,
    build_type text,
    complexion_type text,
    height_ll_feet text,
    height_from_cm text,
    face_type text,
    lips_type text,
    nose_type text,
    cheek_type text,
    teeth_type text,
    beard_type text,
    eye_type text,
    eye_brow_thickness text,
    eye_blind text,
    eye_color text,
    legs_missing text,
    toe_extra text,
    ears_missing text,
    toe_missing text,
    deaf_dumb text,
    arms_missing text,
    hair_color text,
    hair_style text,
    weight_kg text,
    ears_type_cd text,
    other_identify_marks text,
    arrest_ps text,
    modus_operandi text,
    historysheet_y_n text,
    photo_y_n text,
    int_relation_type_father text,
    int_father_name text,
    int_father_mobile_no text,
    int_father_occupation text,
    int_father_address text,
    int_relation_type_mother text,
    int_mother_name text,
    int_mother_mobile_no text,
    int_mother_occupation text,
    int_mother_address text,
    int_relation_type_wife text,
    int_wife_name text,
    int_wife_mobile_no text,
    int_wife_occupation text,
    int_wife_address text,
    int_relation_type_son text,
    int_son_name text,
    int_son_mobile_no text,
    int_son_occupation text,
    int_son_address text,
    int_relation_type_daughter text,
    int_daughter_name text,
    int_daughter_mobile_no text,
    int_daughter_occupation text,
    int_daughter_address text,
    int_relation_type_brother text,
    int_brother_name text,
    int_brother_mobile_no text,
    int_brother_occupation text,
    int_brother_address text,
    int_relation_type_sister text,
    int_sister_name text,
    int_sister_mobile_no text,
    int_sister_occupation text,
    int_sister_address text,
    int_relation_type_fil text,
    int_fil_name text,
    int_fil_mobile_no text,
    int_fil_occupation text,
    int_fil_address text,
    int_relation_type_mil text,
    int_mil_name text,
    int_mil_mobile_no text,
    int_mil_occupation text,
    int_mil_address text,
    int_relation_type_uncle text,
    int_uncle_name text,
    int_uncle_mobile_no text,
    int_uncle_occupation text,
    int_uncle_address text,
    int_relation_type_aunt text,
    int_aunt_name text,
    int_aunt_mobile_no text,
    int_aunt_occupation text,
    int_aunt_address text,
    int_relation_type_friend text,
    int_friend_name text,
    int_friend_mobile_no text,
    int_friend_occupation text,
    int_friend_address text,
    fir_contents text,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    natural_key text
);

CREATE SEQUENCE cctns.cctns_accused_accused_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE cctns.cctns_accused_accused_id_seq OWNED BY cctns.cctns_accused.accused_id;

CREATE TABLE cctns.cctns_accused_details (
    accused_id bigint NOT NULL,
    fir_reg_num character varying(20) NOT NULL,
    person_code character varying(50),
    fir_no character varying(50),
    reg_year integer,
    reg_dt timestamp without time zone,
    unit character varying(100),
    ps_name character varying(100),
    section_of_law text,
    fir_status character varying(100),
    accused_name character varying(255),
    father_name character varying(255),
    gender character varying(50),
    age integer,
    caste character varying(100),
    nationality character varying(100),
    occupation character varying(100),
    mobile_1 character varying(50),
    telephone_residence character varying(50),
    is_arrested character varying(10),
    arrest_surrender_dt timestamp without time zone,
    accused_present_address text,
    accused_permanent_address text,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    natural_key text
);

CREATE SEQUENCE cctns.cctns_accused_details_accused_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE cctns.cctns_accused_details_accused_id_seq OWNED BY cctns.cctns_accused_details.accused_id;

CREATE TABLE cctns.cctns_court (
    court_id bigint NOT NULL,
    fir_reg_num character varying(20) NOT NULL,
    fir_no character varying(50),
    reg_year integer,
    reg_dt timestamp without time zone,
    unit character varying(100),
    ps_name character varying(100),
    section_of_law text,
    fir_status character varying(100),
    chargesheet_dt timestamp without time zone,
    court_name character varying(255),
    court_case_num character varying(100),
    court_disposal_dt timestamp without time zone,
    court_disposal_type character varying(100),
    court_remarks text,
    attach_path character varying(255),
    dms_file_name character varying(100),
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    natural_key text
);

CREATE SEQUENCE cctns.cctns_court_court_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE cctns.cctns_court_court_id_seq OWNED BY cctns.cctns_court.court_id;

CREATE TABLE cctns.cctns_fir (
    fir_reg_num character varying(20) NOT NULL,
    fir_no character varying(50),
    reg_year integer,
    reg_dt timestamp without time zone,
    unit character varying(100),
    ps_name character varying(100),
    section_of_law text,
    fir_status character varying(100),
    fir_contents text,
    attach_path character varying(255),
    dms_file_name character varying(100),
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE cctns.cctns_v1_audit_log (
    id bigint NOT NULL,
    table_name text NOT NULL,
    record_key text NOT NULL,
    field_name text NOT NULL,
    old_value text,
    new_value text,
    changed_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE SEQUENCE cctns.cctns_v1_audit_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE cctns.cctns_v1_audit_log_id_seq OWNED BY cctns.cctns_v1_audit_log.id;

CREATE TABLE cctns.cctns_v1_etl_row_action (
    id bigint NOT NULL,
    run_id uuid NOT NULL,
    entity text NOT NULL,
    table_name text NOT NULL,
    record_key text NOT NULL,
    action text NOT NULL,
    action_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT cctns_v1_etl_row_action_action_check CHECK ((action = ANY (ARRAY['insert'::text, 'update'::text])))
);

CREATE SEQUENCE cctns.cctns_v1_etl_row_action_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE cctns.cctns_v1_etl_row_action_id_seq OWNED BY cctns.cctns_v1_etl_row_action.id;

CREATE TABLE cctns.cctns_v1_etl_run_log (
    id bigint NOT NULL,
    run_id uuid NOT NULL,
    entity text NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    status text DEFAULT 'running'::text NOT NULL,
    rows_fetched integer DEFAULT 0,
    rows_inserted integer DEFAULT 0,
    rows_updated integer DEFAULT 0,
    rows_unchanged integer DEFAULT 0,
    failed_windows jsonb,
    error_message text,
    rows_batch_dupes_removed integer DEFAULT 0,
    rows_orphan_fir_skipped integer DEFAULT 0
);

CREATE SEQUENCE cctns.cctns_v1_etl_run_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE cctns.cctns_v1_etl_run_log_id_seq OWNED BY cctns.cctns_v1_etl_run_log.id;

ALTER TABLE ONLY cctns.cctns_accused ALTER COLUMN accused_id SET DEFAULT nextval('cctns.cctns_accused_accused_id_seq'::regclass);

ALTER TABLE ONLY cctns.cctns_accused_details ALTER COLUMN accused_id SET DEFAULT nextval('cctns.cctns_accused_details_accused_id_seq'::regclass);

ALTER TABLE ONLY cctns.cctns_court ALTER COLUMN court_id SET DEFAULT nextval('cctns.cctns_court_court_id_seq'::regclass);

ALTER TABLE ONLY cctns.cctns_v1_audit_log ALTER COLUMN id SET DEFAULT nextval('cctns.cctns_v1_audit_log_id_seq'::regclass);

ALTER TABLE ONLY cctns.cctns_v1_etl_row_action ALTER COLUMN id SET DEFAULT nextval('cctns.cctns_v1_etl_row_action_id_seq'::regclass);

ALTER TABLE ONLY cctns.cctns_v1_etl_run_log ALTER COLUMN id SET DEFAULT nextval('cctns.cctns_v1_etl_run_log_id_seq'::regclass);

ALTER TABLE ONLY cctns.cctns_accused_details
    ADD CONSTRAINT cctns_accused_details_pkey PRIMARY KEY (accused_id);

ALTER TABLE ONLY cctns.cctns_accused
    ADD CONSTRAINT cctns_accused_pkey PRIMARY KEY (accused_id);

ALTER TABLE ONLY cctns.cctns_court
    ADD CONSTRAINT cctns_court_pkey PRIMARY KEY (court_id);

ALTER TABLE ONLY cctns.cctns_fir
    ADD CONSTRAINT cctns_fir_pkey PRIMARY KEY (fir_reg_num);

ALTER TABLE ONLY cctns.cctns_v1_audit_log
    ADD CONSTRAINT cctns_v1_audit_log_pkey PRIMARY KEY (id);

ALTER TABLE ONLY cctns.cctns_v1_etl_row_action
    ADD CONSTRAINT cctns_v1_etl_row_action_pkey PRIMARY KEY (id);

ALTER TABLE ONLY cctns.cctns_v1_etl_run_log
    ADD CONSTRAINT cctns_v1_etl_run_log_pkey PRIMARY KEY (id);

ALTER TABLE ONLY cctns.cctns_accused_details
    ADD CONSTRAINT uq_cctns_accused_details_natural_key UNIQUE (natural_key);

ALTER TABLE ONLY cctns.cctns_accused
    ADD CONSTRAINT uq_cctns_accused_natural_key UNIQUE (natural_key);

ALTER TABLE ONLY cctns.cctns_court
    ADD CONSTRAINT uq_cctns_court_natural_key UNIQUE (natural_key);

CREATE INDEX idx_accused_arrest ON cctns.cctns_accused_details USING btree (is_arrested);

CREATE INDEX idx_accused_dossier_district_ps ON cctns.cctns_accused USING btree (district, ps);

CREATE INDEX idx_accused_dossier_fir_reg ON cctns.cctns_accused USING btree (fir_reg_num);

CREATE INDEX idx_accused_dossier_mobile ON cctns.cctns_accused USING btree (mobile_1);

CREATE INDEX idx_accused_dossier_name ON cctns.cctns_accused USING btree (accused_name);

CREATE INDEX idx_accused_dossier_reg_dt ON cctns.cctns_accused USING btree (reg_dt);

CREATE INDEX idx_accused_fir_reg ON cctns.cctns_accused_details USING btree (fir_reg_num);

CREATE INDEX idx_accused_mobile ON cctns.cctns_accused_details USING btree (mobile_1);

CREATE INDEX idx_accused_name ON cctns.cctns_accused_details USING btree (accused_name);

CREATE INDEX idx_accused_person_code ON cctns.cctns_accused_details USING btree (person_code);

CREATE INDEX idx_audit_changed_at ON cctns.cctns_v1_audit_log USING btree (changed_at);

CREATE INDEX idx_audit_table_record ON cctns.cctns_v1_audit_log USING btree (table_name, record_key);

CREATE INDEX idx_court_case_num ON cctns.cctns_court USING btree (court_case_num);

CREATE INDEX idx_court_disposal ON cctns.cctns_court USING btree (court_disposal_type);

CREATE INDEX idx_court_fir_reg ON cctns.cctns_court USING btree (fir_reg_num);

CREATE INDEX idx_etl_row_action_entity_at ON cctns.cctns_v1_etl_row_action USING btree (entity, action_at DESC);

CREATE INDEX idx_etl_row_action_run ON cctns.cctns_v1_etl_row_action USING btree (run_id);

CREATE INDEX idx_etl_run_log_entity_started ON cctns.cctns_v1_etl_run_log USING btree (entity, started_at DESC);

CREATE INDEX idx_fir_status ON cctns.cctns_fir USING btree (fir_status);

CREATE INDEX idx_fir_unit_ps ON cctns.cctns_fir USING btree (unit, ps_name);

CREATE INDEX idx_fir_year ON cctns.cctns_fir USING btree (reg_year);

CREATE TRIGGER trg_audit_accused AFTER UPDATE ON cctns.cctns_accused FOR EACH ROW EXECUTE FUNCTION cctns.cctns_v1_log_row_changes('natural_key');

CREATE TRIGGER trg_audit_accused_details AFTER UPDATE ON cctns.cctns_accused_details FOR EACH ROW EXECUTE FUNCTION cctns.cctns_v1_log_row_changes('natural_key');

CREATE TRIGGER trg_audit_court AFTER UPDATE ON cctns.cctns_court FOR EACH ROW EXECUTE FUNCTION cctns.cctns_v1_log_row_changes('natural_key');

CREATE TRIGGER trg_audit_fir AFTER UPDATE ON cctns.cctns_fir FOR EACH ROW EXECUTE FUNCTION cctns.cctns_v1_log_row_changes('fir_reg_num');

CREATE TRIGGER trg_cctns_accused_details_natural_key BEFORE INSERT OR UPDATE ON cctns.cctns_accused_details FOR EACH ROW EXECUTE FUNCTION cctns.trg_cctns_accused_details_natural_key();

CREATE TRIGGER trg_cctns_accused_natural_key BEFORE INSERT OR UPDATE ON cctns.cctns_accused FOR EACH ROW EXECUTE FUNCTION cctns.trg_cctns_accused_natural_key();

CREATE TRIGGER trg_cctns_court_natural_key BEFORE INSERT OR UPDATE ON cctns.cctns_court FOR EACH ROW EXECUTE FUNCTION cctns.trg_cctns_court_natural_key();

ALTER TABLE ONLY cctns.cctns_accused
    ADD CONSTRAINT fk_accused_dossier_fir FOREIGN KEY (fir_reg_num) REFERENCES cctns.cctns_fir(fir_reg_num) ON DELETE CASCADE;

ALTER TABLE ONLY cctns.cctns_accused_details
    ADD CONSTRAINT fk_accused_fir FOREIGN KEY (fir_reg_num) REFERENCES cctns.cctns_fir(fir_reg_num) ON DELETE CASCADE;

ALTER TABLE ONLY cctns.cctns_court
    ADD CONSTRAINT fk_court_fir FOREIGN KEY (fir_reg_num) REFERENCES cctns.cctns_fir(fir_reg_num) ON DELETE CASCADE;
