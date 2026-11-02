-- be_read.accused has no path to person_enrichment at all -- any consumer
-- of this view (e.g. a crime/accused report) can never see
-- domicile_classification or the resolved present/permanent
-- district/state/country, because those live only on person_enrichment,
-- which only be_read.person (migration 019) joins.
--
-- Confirmed live: a real report row showed "Address: ALWAR, RAJASTHAN,
-- India" (from persons_unified.present_address_text, reachable via the
-- existing join to persons_unified) but Accused District/State/Country
-- and Domicile all blank, even though person_enrichment.address_resolution
-- and domicile_classification were correctly populated for that exact
-- person -- the view structurally never reached that table.
--
-- Purely additive: every existing column, in the same order, is
-- unchanged; new columns are appended via a LEFT JOIN to
-- person_enrichment (never INNER -- an accused/person with no enrichment
-- row yet must not disappear from the view), same convention migrations
-- 019/020 already established for this view. address_resolution's JSON
-- shape is {"present": {"district":.., "state":.., "country":..},
-- "permanent": {...}} (see etl3.enrichment.address.resolve_person_address);
-- #>> safely returns NULL when a slot or key is absent.

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
    a.derived_from_accused_id,
    a.split_segment_index,
    e.domicile_classification,
    e.address_resolution #>> '{present,district}'    AS present_district,
    e.address_resolution #>> '{present,state}'       AS present_state,
    e.address_resolution #>> '{present,country}'      AS present_country,
    e.address_resolution #>> '{permanent,district}'   AS permanent_district,
    e.address_resolution #>> '{permanent,state}'       AS permanent_state,
    e.address_resolution #>> '{permanent,country}'     AS permanent_country
FROM accused_unified a
LEFT JOIN persons_unified p ON p.person_id = a.person_id
LEFT JOIN person_enrichment e ON e.person_id = a.person_id
WHERE a.split_superseded_at IS NULL;
