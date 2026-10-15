-- be_read.person / be_read.accused currently select persons_unified.full_name
-- directly, with no join to person_enrichment at all -- confirmed live
-- (dopams_cctns_v2) while investigating the duplicate-full-name bug: the
-- deployed view returns the raw, unnormalized name, so even after fixing
-- names.py and reprocessing person_enrichment, any consumer of this
-- contract still sees the unnormalized value.
--
-- This is purely additive: every existing column on both views, in the
-- same order, is unchanged; only new columns are appended at the end via
-- a LEFT JOIN to person_enrichment (never INNER -- a person with no
-- enrichment row yet must not disappear from either view). A consumer
-- selecting existing columns by name is unaffected; only a bare
-- `SELECT *` sees the additional columns.
--
-- Not applied by anything in this commit; validate in a disposable
-- database first, same as every other migration here.

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
    p.current_as_of,
    e.cleaned_full_name,
    e.cleaned_given_name,
    e.cleaned_alias,
    e.relation_type      AS cleaned_relation_type,
    e.cleaned_relative_name,
    e.domicile_classification,
    e.address_resolution
FROM persons_unified p
LEFT JOIN person_enrichment e ON e.person_id = p.person_id;

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
    a.current_as_of,
    e.cleaned_full_name,
    e.cleaned_alias,
    e.relation_type      AS cleaned_relation_type,
    e.cleaned_relative_name
FROM accused_unified a
LEFT JOIN persons_unified p ON p.person_id = a.person_id
LEFT JOIN person_enrichment e ON e.person_id = a.person_id;
