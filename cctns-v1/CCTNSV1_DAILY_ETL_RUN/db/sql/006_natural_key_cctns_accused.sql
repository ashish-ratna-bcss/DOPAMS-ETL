-- Accused dossier (date-range POST) — same upsert pattern as 004.
-- Key includes from_dt/to_dt because one FIR can have multiple dossier rows per window.

ALTER TABLE cctns.cctns_accused
    ADD COLUMN IF NOT EXISTS natural_key TEXT;

CREATE OR REPLACE FUNCTION cctns.trg_cctns_accused_natural_key() RETURNS trigger AS $$
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
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_cctns_accused_natural_key ON cctns.cctns_accused;
CREATE TRIGGER trg_cctns_accused_natural_key
    BEFORE INSERT OR UPDATE ON cctns.cctns_accused
    FOR EACH ROW EXECUTE FUNCTION cctns.trg_cctns_accused_natural_key();

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_cctns_accused_natural_key'
    ) THEN
        ALTER TABLE cctns.cctns_accused
            ADD CONSTRAINT uq_cctns_accused_natural_key UNIQUE (natural_key);
    END IF;
END $$;

DROP TRIGGER IF EXISTS trg_audit_accused ON cctns.cctns_accused;
CREATE TRIGGER trg_audit_accused
    AFTER UPDATE ON cctns.cctns_accused
    FOR EACH ROW EXECUTE FUNCTION cctns.cctns_v1_log_row_changes('natural_key');
