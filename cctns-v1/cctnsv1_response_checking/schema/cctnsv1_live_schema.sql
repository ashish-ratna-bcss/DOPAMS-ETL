-- ============================================================================
-- CCTNS V1 — Live ETL Database Schema (ground truth)
-- Source: pg_dump --schema-only of the "cctns_v1" database on dopams-new.
-- Cleaned of pg_dump boilerplate (SET session config, \restrict markers,
-- per-object "-- Name: X; Type: Y; Schema: public; Owner: -" separators).
-- Statements themselves are unmodified. See README.md for notes.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- Tables
-- ----------------------------------------------------------------------------

CREATE TABLE public.cctns_fir (
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
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


CREATE TABLE public.cctns_accused (
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
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);

CREATE SEQUENCE public.cctns_accused_accused_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.cctns_accused_accused_id_seq OWNED BY public.cctns_accused.accused_id;


CREATE TABLE public.cctns_accused_details (
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
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);

CREATE SEQUENCE public.cctns_accused_details_accused_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.cctns_accused_details_accused_id_seq OWNED BY public.cctns_accused_details.accused_id;


CREATE TABLE public.cctns_court (
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
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);

CREATE SEQUENCE public.cctns_court_court_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.cctns_court_court_id_seq OWNED BY public.cctns_court.court_id;


-- ----------------------------------------------------------------------------
-- Column defaults tied to sequences
-- ----------------------------------------------------------------------------

ALTER TABLE ONLY public.cctns_accused ALTER COLUMN accused_id SET DEFAULT nextval('public.cctns_accused_accused_id_seq'::regclass);
ALTER TABLE ONLY public.cctns_accused_details ALTER COLUMN accused_id SET DEFAULT nextval('public.cctns_accused_details_accused_id_seq'::regclass);
ALTER TABLE ONLY public.cctns_court ALTER COLUMN court_id SET DEFAULT nextval('public.cctns_court_court_id_seq'::regclass);


-- ----------------------------------------------------------------------------
-- Primary keys
-- ----------------------------------------------------------------------------

ALTER TABLE ONLY public.cctns_fir
    ADD CONSTRAINT cctns_fir_pkey PRIMARY KEY (fir_reg_num);

ALTER TABLE ONLY public.cctns_accused
    ADD CONSTRAINT cctns_accused_pkey PRIMARY KEY (accused_id);

ALTER TABLE ONLY public.cctns_accused_details
    ADD CONSTRAINT cctns_accused_details_pkey PRIMARY KEY (accused_id);

ALTER TABLE ONLY public.cctns_court
    ADD CONSTRAINT cctns_court_pkey PRIMARY KEY (court_id);


-- ----------------------------------------------------------------------------
-- Foreign keys (everything hangs off cctns_fir.fir_reg_num)
-- ----------------------------------------------------------------------------

ALTER TABLE ONLY public.cctns_accused
    ADD CONSTRAINT fk_accused_dossier_fir FOREIGN KEY (fir_reg_num) REFERENCES public.cctns_fir(fir_reg_num) ON DELETE CASCADE;

ALTER TABLE ONLY public.cctns_accused_details
    ADD CONSTRAINT fk_accused_fir FOREIGN KEY (fir_reg_num) REFERENCES public.cctns_fir(fir_reg_num) ON DELETE CASCADE;

ALTER TABLE ONLY public.cctns_court
    ADD CONSTRAINT fk_court_fir FOREIGN KEY (fir_reg_num) REFERENCES public.cctns_fir(fir_reg_num) ON DELETE CASCADE;


-- ----------------------------------------------------------------------------
-- Indexes
-- ----------------------------------------------------------------------------

CREATE INDEX idx_fir_status ON public.cctns_fir USING btree (fir_status);
CREATE INDEX idx_fir_unit_ps ON public.cctns_fir USING btree (unit, ps_name);
CREATE INDEX idx_fir_year ON public.cctns_fir USING btree (reg_year);

CREATE INDEX idx_accused_dossier_district_ps ON public.cctns_accused USING btree (district, ps);
CREATE INDEX idx_accused_dossier_fir_reg ON public.cctns_accused USING btree (fir_reg_num);
CREATE INDEX idx_accused_dossier_mobile ON public.cctns_accused USING btree (mobile_1);
CREATE INDEX idx_accused_dossier_name ON public.cctns_accused USING btree (accused_name);
CREATE INDEX idx_accused_dossier_reg_dt ON public.cctns_accused USING btree (reg_dt);

CREATE INDEX idx_accused_arrest ON public.cctns_accused_details USING btree (is_arrested);
CREATE INDEX idx_accused_fir_reg ON public.cctns_accused_details USING btree (fir_reg_num);
CREATE INDEX idx_accused_mobile ON public.cctns_accused_details USING btree (mobile_1);
CREATE INDEX idx_accused_name ON public.cctns_accused_details USING btree (accused_name);
CREATE INDEX idx_accused_person_code ON public.cctns_accused_details USING btree (person_code);

CREATE INDEX idx_court_case_num ON public.cctns_court USING btree (court_case_num);
CREATE INDEX idx_court_disposal ON public.cctns_court USING btree (court_disposal_type);
CREATE INDEX idx_court_fir_reg ON public.cctns_court USING btree (fir_reg_num);
