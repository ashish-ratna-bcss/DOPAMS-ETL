-- V1 station matching stores a hierarchy unit code beside the raw V1
-- station name and district. ps_name and unit_district stay the source
-- values. unit_code is the hierarchy dist_code when a rule can name the
-- unit. It stays null when no rule applies.

ALTER TABLE crimes_unified
    ADD COLUMN IF NOT EXISTS unit_code VARCHAR(20);

CREATE INDEX IF NOT EXISTS idx_crimes_unified_unit_code ON crimes_unified(unit_code);

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
    c.current_as_of,
    c.unit_code,
    c.additional_json_data->'ps_resolution'->>'basis' AS ps_match_basis,
    c.additional_json_data->'ps_resolution'->>'raw_ps_name' AS raw_ps_name,
    c.additional_json_data->'ps_resolution'->>'raw_district' AS raw_district
FROM crimes_unified c
LEFT JOIN be_read.hierarchy h ON h.ps_code = c.ps_code;
