-- Derived enrichment rows must not block a unified rebuild.
-- ON DELETE CASCADE removes the derived row when its parent unified row
-- is deleted. The next enrichment pass recreates it from source observations.
-- This does not touch V1, V2, or the canonical CCTNS columns.

ALTER TABLE crime_enrichment DROP CONSTRAINT crime_enrichment_crime_id_fkey;
ALTER TABLE crime_enrichment
    ADD CONSTRAINT crime_enrichment_crime_id_fkey
    FOREIGN KEY (crime_id) REFERENCES crimes_unified(crime_id) ON DELETE CASCADE;

ALTER TABLE person_enrichment DROP CONSTRAINT person_enrichment_person_id_fkey;
ALTER TABLE person_enrichment
    ADD CONSTRAINT person_enrichment_person_id_fkey
    FOREIGN KEY (person_id) REFERENCES persons_unified(person_id) ON DELETE CASCADE;

ALTER TABLE arrest_enrichment DROP CONSTRAINT arrest_enrichment_arrest_id_fkey;
ALTER TABLE arrest_enrichment
    ADD CONSTRAINT arrest_enrichment_arrest_id_fkey
    FOREIGN KEY (arrest_id) REFERENCES arrests_unified(arrest_id) ON DELETE CASCADE;

ALTER TABLE accused_enrichment DROP CONSTRAINT accused_enrichment_accused_id_fkey;
ALTER TABLE accused_enrichment
    ADD CONSTRAINT accused_enrichment_accused_id_fkey
    FOREIGN KEY (accused_id) REFERENCES accused_unified(accused_id) ON DELETE CASCADE;

ALTER TABLE chargesheet_enrichment DROP CONSTRAINT chargesheet_enrichment_charge_sheet_id_fkey;
ALTER TABLE chargesheet_enrichment
    ADD CONSTRAINT chargesheet_enrichment_charge_sheet_id_fkey
    FOREIGN KEY (charge_sheet_id) REFERENCES chargesheets_unified(charge_sheet_id) ON DELETE CASCADE;

ALTER TABLE hierarchy_enrichment DROP CONSTRAINT hierarchy_enrichment_ps_code_fkey;
ALTER TABLE hierarchy_enrichment
    ADD CONSTRAINT hierarchy_enrichment_ps_code_fkey
    FOREIGN KEY (ps_code) REFERENCES hierarchy_unified(ps_code) ON DELETE CASCADE;

ALTER TABLE property_enrichment DROP CONSTRAINT property_enrichment_property_id_fkey;
ALTER TABLE property_enrichment
    ADD CONSTRAINT property_enrichment_property_id_fkey
    FOREIGN KEY (property_id) REFERENCES properties_unified(property_id) ON DELETE CASCADE;

ALTER TABLE disposal_enrichment DROP CONSTRAINT disposal_enrichment_disposal_id_fkey;
ALTER TABLE disposal_enrichment
    ADD CONSTRAINT disposal_enrichment_disposal_id_fkey
    FOREIGN KEY (disposal_id) REFERENCES disposal_unified(disposal_id) ON DELETE CASCADE;

ALTER TABLE drug_extractions DROP CONSTRAINT drug_extractions_crime_id_fkey;
ALTER TABLE drug_extractions
    ADD CONSTRAINT drug_extractions_crime_id_fkey
    FOREIGN KEY (crime_id) REFERENCES crimes_unified(crime_id) ON DELETE CASCADE;
