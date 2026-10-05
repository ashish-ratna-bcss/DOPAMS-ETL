-- Phase 7 read contract for the DOPAMS backend.
--
-- These views project dopams_cctns current state. They do not copy V1 or V2.
-- They do not merge identity candidates. They do not fill FSL from the source.
-- Writes are rejected: source-derived CCTNS fields stay owned by ETL-3.
--
-- The production backend still queries public.firs_mv and public.accuseds_mv.
-- Those views inner-join hierarchy and, for accused, start from brief_facts.
-- Pointing that SQL at this database would drop crimes with no ps_code and
-- would hide accused rows that have no brief-facts row. This contract uses
-- LEFT JOIN and the unified tables instead.

CREATE SCHEMA IF NOT EXISTS be_read;

CREATE OR REPLACE FUNCTION be_read.reject_write()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'be_read is read-only; ETL-3 owns source-derived CCTNS rows';
END;
$$;

CREATE OR REPLACE VIEW be_read.hierarchy AS
SELECT DISTINCT ON (payload->>'ps_code')
    payload->>'ps_code' AS ps_code,
    payload->>'ps_name' AS ps_name,
    payload->>'dist_name' AS dist_name,
    payload->>'dist_code' AS dist_code,
    payload->>'circle_code' AS circle_code,
    payload->>'circle_name' AS circle_name,
    payload->>'sdpo_code' AS sdpo_code,
    payload->>'sdpo_name' AS sdpo_name,
    payload->>'range_code' AS range_code,
    payload->>'range_name' AS range_name,
    payload->>'zone_code' AS zone_code,
    payload->>'zone_name' AS zone_name,
    source_record_id,
    source_run_id
FROM hierarchy_source
WHERE source_system = 'V2'
  AND NULLIF(payload->>'ps_code', '') IS NOT NULL
ORDER BY payload->>'ps_code', id DESC;

CREATE OR REPLACE VIEW be_read.crime AS
SELECT
    c.crime_id,
    c.source_system,
    c.source_record_id,
    c.fir_reg_num,
    c.fir_num,
    c.fir_date,
    c.ps_code,
    c.ps_name AS source_ps_name,
    c.unit_district AS source_unit_name,
    COALESCE(h.ps_name, c.ps_name) AS ps_name,
    COALESCE(h.dist_name, c.unit_district) AS district_name,
    (c.ps_code IS NULL) AS ps_code_unresolved,
    c.acts_sections,
    c.brief_facts,
    c.case_status,
    c.major_head,
    c.minor_head,
    c.io_name,
    c.io_rank,
    c.current_source_run_id,
    c.current_as_of
FROM crimes_unified c
LEFT JOIN be_read.hierarchy h ON h.ps_code = c.ps_code;

CREATE OR REPLACE VIEW be_read.person AS
SELECT
    p.person_id,
    p.source_system,
    p.source_record_id,
    p.full_name,
    p.alias,
    p.relative_name,
    p.gender,
    p.date_of_birth,
    p.age,
    p.occupation,
    p.caste,
    p.nationality,
    p.present_address_text,
    p.permanent_address_text,
    p.phone_number,
    p.email_id,
    p.current_source_run_id,
    p.current_as_of
FROM persons_unified p;

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
    a.current_as_of
FROM accused_unified a
LEFT JOIN persons_unified p ON p.person_id = a.person_id;

CREATE OR REPLACE VIEW be_read.arrest AS
SELECT
    r.arrest_id,
    r.source_system,
    r.source_record_id,
    r.accused_id,
    r.crime_id,
    r.is_arrested,
    r.arrested_date,
    r.arrest_ps,
    (r.accused_id IS NULL) AS accused_unresolved,
    r.current_source_run_id,
    r.current_as_of
FROM arrests_unified r;

CREATE OR REPLACE VIEW be_read.chargesheet AS
SELECT
    s.charge_sheet_id,
    s.source_system,
    s.source_module,
    s.source_record_id,
    s.crime_id,
    s.chargesheet_no,
    s.chargesheet_date,
    s.court_name,
    s.court_case_num,
    s.court_disposal_date,
    s.court_disposal_type,
    s.current_source_run_id,
    s.current_as_of
FROM chargesheets_unified s;

CREATE OR REPLACE VIEW be_read.fsl_historical AS
SELECT
    f.case_property_id,
    f.source_record_id,
    f.crime_id,
    f.current_source_run_id,
    f.current_as_of,
    'historical_not_merged'::text AS inclusion
FROM fsl_unified f;

CREATE OR REPLACE VIEW be_read.identity_candidate AS
SELECT
    i.person_a_id,
    i.person_b_id,
    i.match_basis,
    i.confidence_score,
    i.status
FROM identity_links i
WHERE i.status = 'candidate';

DO $$
DECLARE
    view_name text;
BEGIN
    FOREACH view_name IN ARRAY ARRAY[
        'hierarchy', 'crime', 'person', 'accused', 'arrest',
        'chargesheet', 'fsl_historical', 'identity_candidate'
    ]
    LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS be_read_reject_write ON be_read.%I', view_name);
        EXECUTE format(
            'CREATE TRIGGER be_read_reject_write INSTEAD OF INSERT OR UPDATE OR DELETE ON be_read.%I FOR EACH ROW EXECUTE FUNCTION be_read.reject_write()',
            view_name
        );
    END LOOP;
END $$;
