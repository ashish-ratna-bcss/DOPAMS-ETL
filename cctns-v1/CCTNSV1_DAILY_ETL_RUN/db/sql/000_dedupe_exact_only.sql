-- ============================================================================
-- Safe, exact-duplicate-only cleanup for cctns_v1. SAFE TO RUN.
--
-- Deletes ONLY rows that are byte-for-byte identical across every real
-- business column (everything except the surrogate id and created_at),
-- keeping the lowest id in each group.
--
-- Confirmed counts before running:
--   cctns_court:            134 exact-duplicate rows (source API itself
--                            returned the same record twice)
--   cctns_accused_details:  192 exact-duplicate rows (load-time issue,
--                            not present in the raw captured API response)
--   cctns_accused:        14,752 exact-duplicate rows
--   cctns_fir:                0 (already clean, not included below)
--
-- Deliberately NOT included: the narrower "shares fir_reg_num + a couple
-- fields" groups -- those contain legitimate distinct records (e.g. 18
-- different unarmed/unnamed accused sharing fir_reg_num+person_code+
-- accused_name="unknown"). Deleting on that narrower match would destroy
-- real data. Left for manual review -- see db/sql/001_schema_fix.sql header.
-- ============================================================================

BEGIN;

DELETE FROM cctns_court a
USING cctns_court b
WHERE a.court_id > b.court_id
  AND a.fir_reg_num          IS NOT DISTINCT FROM b.fir_reg_num
  AND a.fir_no                IS NOT DISTINCT FROM b.fir_no
  AND a.reg_year                IS NOT DISTINCT FROM b.reg_year
  AND a.reg_dt                    IS NOT DISTINCT FROM b.reg_dt
  AND a.unit                        IS NOT DISTINCT FROM b.unit
  AND a.ps_name                       IS NOT DISTINCT FROM b.ps_name
  AND a.section_of_law                  IS NOT DISTINCT FROM b.section_of_law
  AND a.fir_status                        IS NOT DISTINCT FROM b.fir_status
  AND a.chargesheet_dt                      IS NOT DISTINCT FROM b.chargesheet_dt
  AND a.court_name                            IS NOT DISTINCT FROM b.court_name
  AND a.court_case_num                          IS NOT DISTINCT FROM b.court_case_num
  AND a.court_disposal_dt                         IS NOT DISTINCT FROM b.court_disposal_dt
  AND a.court_disposal_type                         IS NOT DISTINCT FROM b.court_disposal_type
  AND a.court_remarks                                 IS NOT DISTINCT FROM b.court_remarks
  AND a.attach_path                                     IS NOT DISTINCT FROM b.attach_path
  AND a.dms_file_name                                     IS NOT DISTINCT FROM b.dms_file_name;

DELETE FROM cctns_accused_details a
USING cctns_accused_details b
WHERE a.accused_id > b.accused_id
  AND a.fir_reg_num           IS NOT DISTINCT FROM b.fir_reg_num
  AND a.person_code            IS NOT DISTINCT FROM b.person_code
  AND a.fir_no                   IS NOT DISTINCT FROM b.fir_no
  AND a.reg_year                   IS NOT DISTINCT FROM b.reg_year
  AND a.reg_dt                       IS NOT DISTINCT FROM b.reg_dt
  AND a.unit                           IS NOT DISTINCT FROM b.unit
  AND a.ps_name                          IS NOT DISTINCT FROM b.ps_name
  AND a.section_of_law                     IS NOT DISTINCT FROM b.section_of_law
  AND a.fir_status                           IS NOT DISTINCT FROM b.fir_status
  AND a.accused_name                           IS NOT DISTINCT FROM b.accused_name
  AND a.father_name                              IS NOT DISTINCT FROM b.father_name
  AND a.gender                                     IS NOT DISTINCT FROM b.gender
  AND a.age                                          IS NOT DISTINCT FROM b.age
  AND a.caste                                          IS NOT DISTINCT FROM b.caste
  AND a.nationality                                      IS NOT DISTINCT FROM b.nationality
  AND a.occupation                                         IS NOT DISTINCT FROM b.occupation
  AND a.mobile_1                                             IS NOT DISTINCT FROM b.mobile_1
  AND a.telephone_residence                                    IS NOT DISTINCT FROM b.telephone_residence
  AND a.is_arrested                                              IS NOT DISTINCT FROM b.is_arrested
  AND a.arrest_surrender_dt                                        IS NOT DISTINCT FROM b.arrest_surrender_dt
  AND a.accused_present_address                                      IS NOT DISTINCT FROM b.accused_present_address
  AND a.accused_permanent_address                                      IS NOT DISTINCT FROM b.accused_permanent_address;

DELETE FROM cctns_accused a
USING cctns_accused b
WHERE a.accused_id > b.accused_id
  AND a.district IS NOT DISTINCT FROM b.district
  AND a.ps IS NOT DISTINCT FROM b.ps
  AND a.fir_no IS NOT DISTINCT FROM b.fir_no
  AND a.reg_dt IS NOT DISTINCT FROM b.reg_dt
  AND a.year IS NOT DISTINCT FROM b.year
  AND a.fir_reg_num IS NOT DISTINCT FROM b.fir_reg_num
  AND a.fir_status IS NOT DISTINCT FROM b.fir_status
  AND a.act_sec IS NOT DISTINCT FROM b.act_sec
  AND a.major_head IS NOT DISTINCT FROM b.major_head
  AND a.minor_head IS NOT DISTINCT FROM b.minor_head
  AND a.from_dt IS NOT DISTINCT FROM b.from_dt
  AND a.to_dt IS NOT DISTINCT FROM b.to_dt
  AND a.ps_recv_inform_dt IS NOT DISTINCT FROM b.ps_recv_inform_dt
  AND a.drug_particulars IS NOT DISTINCT FROM b.drug_particulars
  AND a.weight_gm IS NOT DISTINCT FROM b.weight_gm
  AND a.drug_desc IS NOT DISTINCT FROM b.drug_desc
  AND a.drug_status IS NOT DISTINCT FROM b.drug_status
  AND a.drug_type IS NOT DISTINCT FROM b.drug_type
  AND a.estimated_value IS NOT DISTINCT FROM b.estimated_value
  AND a.area_operation IS NOT DISTINCT FROM b.area_operation
  AND a.location_type IS NOT DISTINCT FROM b.location_type
  AND a.drug_place_type IS NOT DISTINCT FROM b.drug_place_type
  AND a.paking_making_desc IS NOT DISTINCT FROM b.paking_making_desc
  AND a.packets_count IS NOT DISTINCT FROM b.packets_count
  AND a.accused_name IS NOT DISTINCT FROM b.accused_name
  AND a.age IS NOT DISTINCT FROM b.age
  AND a.father_name IS NOT DISTINCT FROM b.father_name
  AND a.accused_occupation IS NOT DISTINCT FROM b.accused_occupation
  AND a.gender IS NOT DISTINCT FROM b.gender
  AND a.caste IS NOT DISTINCT FROM b.caste
  AND a.nationality IS NOT DISTINCT FROM b.nationality
  AND a.telephone_residence IS NOT DISTINCT FROM b.telephone_residence
  AND a.alias_name IS NOT DISTINCT FROM b.alias_name
  AND a.dob IS NOT DISTINCT FROM b.dob
  AND a.mobile_1 IS NOT DISTINCT FROM b.mobile_1
  AND a.email IS NOT DISTINCT FROM b.email
  AND a.social_media_accnt IS NOT DISTINCT FROM b.social_media_accnt
  AND a.aadhar_card IS NOT DISTINCT FROM b.aadhar_card
  AND a.ration_card IS NOT DISTINCT FROM b.ration_card
  AND a.voter_card IS NOT DISTINCT FROM b.voter_card
  AND a.passport IS NOT DISTINCT FROM b.passport
  AND a.pan_card IS NOT DISTINCT FROM b.pan_card
  AND a.electricity_connection IS NOT DISTINCT FROM b.electricity_connection
  AND a.telephone_connection IS NOT DISTINCT FROM b.telephone_connection
  AND a.gas_connection IS NOT DISTINCT FROM b.gas_connection
  AND a.driving_license IS NOT DISTINCT FROM b.driving_license
  AND a.other_proofs IS NOT DISTINCT FROM b.other_proofs
  AND a.present_address IS NOT DISTINCT FROM b.present_address
  AND a.permanent_address IS NOT DISTINCT FROM b.permanent_address
  AND a.arrest_surrender_dt IS NOT DISTINCT FROM b.arrest_surrender_dt
  AND a.fir_contents IS NOT DISTINCT FROM b.fir_contents;

COMMIT;
