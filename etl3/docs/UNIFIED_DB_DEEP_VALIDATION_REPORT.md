# Unified database deep validation

## Executive result

READY WITH DATA LIMITATIONS

`dopams_cctns` is internally consistent. Source counts match the unified tables under the Phase 6 rules. Random crime, accused, person, arrest, court, and chargesheet traversals return the parent row when a child is missing. No unexplained primary-key duplicate, foreign-key break, or `ETL_DEFECT` gap was found.

This does not switch the DOPAMS application. The application still needs its own integration work, listed at the end. No application file, ETL code, V1 database, or V2 database was modified. Every statement against `dopams_cctns` was a `SELECT` or `EXPLAIN ANALYZE`.

```text
CCTNS V1 ── read only ──┐
                        ├──> ETL-3 ──> dopams_cctns
CCTNS V2 ── read only ──┘
```

## Schema actually queried

| Role | Object |
|---|---|
| Crime / FIR | `public.crimes_unified`, observations in `crimes_source` |
| Accused | `public.accused_unified`, observations in `accused_source` |
| Person | `public.persons_unified`, observations in `persons_source` and V1 `arrests_source` payloads |
| Arrest | `public.arrests_unified`, observations in `arrests_source` |
| Court, chargesheet, updates | `public.chargesheets_unified` (`source_module` = `court`, `chargesheets`, `charge_sheet_updates`), observations in `chargesheets_source` |
| Police station / district | `public.hierarchy_unified` (`ps_code` only) and `be_read.hierarchy` (names from the latest V2 `hierarchy_source` payload) |
| FSL | `public.fsl_source`, `public.fsl_unified` |
| Identity | `public.identity_links` |
| History | `public.change_log`, `public.bulk_event_exclusions` |
| Gaps | `public.source_gap_ledger` |
| Read contract | `be_read.*` views |

Primary keys and the foreign keys from accused, arrest, and chargesheet to crime are present. `arrests_unified.accused_id` and `accused_unified.person_id` are nullable. Chargesheet uniqueness is `(source_system, source_module, source_record_id)`, and `charge_sheet_id` is `{source_system}:{module}:{raw_id}`.

## Baseline counts

| Population | Actual | Known baseline | Status |
|---|---:|---:|---|
| V1 `cctns.cctns_fir` | 7,305 | 7,305 | PASS |
| V2 `crimes` | 9,583 | 9,583 | PASS |
| Unified crimes | 16,888 (V1 7,305, V2 9,583) | 7,305 + 9,583 | PASS |
| V1 `cctns_accused` | 34,390 | 34,390 | PASS |
| Unified V1 accused | 17,356 | 17,356 | EXPECTED collapse |
| V2 accused source and unified | 32,992 | 32,992 | PASS |
| V1 persons unified | 20,127 | 20,127 | PASS |
| V2 persons source and unified | 32,914 | 32,914 | PASS |
| V1 `cctns_accused_details` and unified arrests | 20,198 | 20,198 | PASS |
| V2 arrests source and unified | 32,988 | 32,988 | PASS |
| V1 court | 7,534 | 7,534 | PASS |
| V2 chargesheets | 7,086 | 7,086 | PASS |
| V2 charge_sheet_updates | 6,193 | 6,193 | PASS |
| FSL source rows / distinct / unified | 2,006 / 2,006 / 2,006 | 2,006 | PASS |
| FSL observation rows including replays | 2,009 | — | EXPECTED |
| Identity links / confirmed | 1,131 / 0 | 1,131 / 0 | PASS |
| `change_log` | 1,338,633 | 1,338,633 | PASS |
| `source_gap_ledger` | 40,516 | — | PASS, see gap section |
| `bulk_event_exclusions` | 0 | 0 | PASS |

The same V1 and V2 crime counts were read again after the validation queries: 7,305 and 9,583. `CREATE TABLE` on V1 and on V2 raised `ReadOnlySqlTransaction`.

## Count reconciliation

Observed means distinct `source_record_id` in the observation table for that source module. Status uses `classify_module`.

| Domain | Source | Observed | Unified | Excluded | Gap | Status |
|---|---:|---:|---:|---:|---:|---|
| V1 fir | 7305 | 7305 | 7305 | 0 | 0 | EXPECTED |
| V1 accused | 34390 | 34390 | 17356 | 0 | 17034 | EXPECTED |
| V1 accused_details / arrests | 20198 | 20198 | 20198 | 0 | 0 | EXPECTED |
| V1 court | 7534 | 7534 | 7534 | 0 | 0 | EXPECTED |
| V2 crimes | 9583 | 9583 | 9583 | 0 | 0 | EXPECTED |
| V2 accused | 32992 | 32992 | 32992 | 0 | 0 | EXPECTED |
| V2 persons | 32914 | 32914 | 32914 | 0 | 0 | EXPECTED |
| V2 arrests | 32988 | 32988 | 32988 | 0 | 0 | EXPECTED |
| V2 chargesheets | 7086 | 7086 | 7086 | 0 | 0 | EXPECTED |
| V2 charge_sheet_updates | 6193 | 6193 | 6193 | 0 | 0 | EXPECTED |
| V2 mo_seizures | 3540 | 3540 | 3540 | 0 | 0 | EXPECTED |
| V2 properties | 7683 | 7683 | 7683 | 0 | 0 | EXPECTED |
| V2 fsl_case_property | 2006 | 2006 | 2006 | 2006 | 0 | INTENTIONALLY_EXCLUDED |
| V2 disposal | 482 | 482 | 482 | 0 | 0 | EXPECTED |
| V2 interrogation_reports | 19569 | 19569 | 19569 | 0 | 0 | EXPECTED |
| V2 hierarchy | 816 | 816 | 816 | 0 | 0 | EXPECTED |

The V1 accused gap is the logical collapse: 34,390 dossier rows are stored as observations and grouped to 17,356 current rows. None of the 34,390 source keys are missing from `accused_source`.

## Test results

### T01 Crime identity

Purpose: no null key, duplicate key, or missing provenance.

```sql
SELECT count(*) FROM crimes_unified
WHERE crime_id IS NULL OR source_system IS NULL OR source_record_id IS NULL
   OR current_source_run_id IS NULL OR source_system NOT IN ('V1','V2');
SELECT count(*) FROM (SELECT crime_id FROM crimes_unified GROUP BY 1 HAVING count(*) > 1) d;
```

Expected: 0 and 0. Actual: 0 and 0. Status: PASS.

### T02 Source distribution

```sql
SELECT source_system, count(*) FROM crimes_unified GROUP BY 1;
```

Expected: V1 7305, V2 9583. Actual: those counts. `fir_reg_num` overlap between V1 and V2 is 0. The same raw `source_record_id` overlap is 0 for crimes, accused, persons, and arrests. Status: PASS.

### T03 Date range and reproducible samples

`fir_date` runs from 1991-12-12 to 2026-10-04. Null dates: 0.

Samples use `md5(crime_id)`, which repeats exactly. Quartiles are `ntile(4)` over `fir_date, crime_id`. Two crimes from each quartile:

| Bucket | Crime | Source | Date | PS code null | Accused | Arrests | Court | Chargesheet | Updates |
|---|---|---|---|---:|---:|---:|---:|---:|---:|
| Early | `2011001050161` | V1 | 2005-11-28 | yes | 2 | 2 | 1 | 0 | 0 |
| Early | `2022051160008` | V1 | 2016-01-08 | no | 2 | 2 | 1 | 0 | 0 |
| Middle | `2025007200327` | V1 | 2020-12-16 | yes | 2 | 2 | 1 | 0 | 0 |
| Middle | `62f3eea623dc926b12b5eb28` | V2 | 2022-08-10 | no | 4 | 4 | 0 | 1 | 1 |
| Recent | `650b36bf18498da12f1b19bf` | V2 | 2023-09-20 | no | 2 | 2 | 0 | 1 | 1 |
| Recent | `6598c9d93e830a95d16066bb` | V2 | 2024-01-05 | no | 2 | 2 | 0 | 2 | 1 |
| Latest | `6866751183d650371a3e2e9a` | V2 | 2025-07-03 | no | 3 | 3 | 0 | 1 | 1 |
| Latest | `689609747aa2c91f3d5b20ba` | V2 | 2025-08-08 | no | 2 | 2 | 0 | 1 | 1 |

Oldest crime: V1 `2025052910079`, 1991-12-12, null `ps_code`. Newest: V2 `6ac22c78d14454d2a0252055`, 2026-10-04, `ps_code` present. Status: PASS.

### T04 Crimes with missing children stay visible

```sql
SELECT source_system, count(*) FROM crimes_unified c
WHERE NOT EXISTS (SELECT 1 FROM accused_unified a WHERE a.crime_id = c.crime_id)
GROUP BY 1;
```

| Shape | Count | Classification |
|---|---:|---|
| No accused | V1 723, V2 18 | EXPECTED. The source has the same 723 V1 FIRs with no dossier accused, and the same 18 V2 crimes with no accused. |
| No arrest | 10 | EXPECTED. The crime row is still present. |
| No V1 court row | V2 9,583, V1 0 | EXPECTED. Court is a V1 module. Every V1 crime has at least one court row. |
| No V2 chargesheet | 10,142 | EXPECTED. V1 crimes are not V2 chargesheets, and not every V2 crime has one. |
| Chargesheet whose crime has no update | 697 | EXPECTED. |

Status: PASS. The anti-join finds the crime. It does not delete it.

### T05 Accused and person links

```sql
SELECT source_system, count(*) FROM accused_unified WHERE person_id IS NULL GROUP BY 1;
SELECT count(*) FROM accused_unified a
WHERE person_id IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM persons_unified p WHERE p.person_id = a.person_id);
```

Null `person_id`: V1 616, V2 79, total 695. Accused pointing at a missing person: 0. Accused pointing at a missing crime: 0. A direct select of a null-person accused returns the accused row. Sample ids: V2 `693187e7d195552dfcd1fe47`, V1 `V1:e650fed812bf4ad307b20a4f211dfab5`. Status: PASS.

V1 names are not columns on `accused_unified`. They are on the linked person, or only in the observation payload when the person link is unresolved. That is an application integration requirement, not a dropped accused row.

### T06 V1 accused collapse and the 69 keyless persons

Observed V1 accused keys: 34,390. Logical unified rows: 17,356. Open `v1_person_key_absent` gaps: 69. All 69 gap keys are absent from `persons_unified`. Status: EXPECTED. No person id was invented for those 69 rows.

### T07 Two shared V1 person codes

```sql
SELECT payload->>'person_code', count(DISTINCT source_record_id)
FROM arrests_source
WHERE source_system = 'V1' AND source_table = 'accused_details'
  AND NULLIF(btrim(payload->>'person_code'), '') IS NOT NULL
GROUP BY 1
HAVING count(DISTINCT source_record_id) > 1;
```

Actual codes, each with two source records and one unified person:

| person_code | Source records | Payload full names | Unified person | Linked accused |
|---|---|---|---|---:|
| `202001915003534001` | 3720, 3721 | both null; father `saleem` | `shaik sameer` | 0 |
| `202003222040534001` | 3028, 63619 | both null | ` vikky` | 0 |

The unified model keeps one person per `person_code`. These two codes do not become two people, and they do not absorb a second code. The payload rows do not carry two different full names. Status: EXPECTED.

### T08 Person cardinality

| Shape | Count | Status |
|---|---:|---|
| Person with no accused | V1 3,387, V2 1 | EXPECTED. V1 persons come from `accused_details` and are linked only on an unambiguous name match. 16,740 V1 persons are linked (20,127 − 3,387), which matches the last consolidation's `person_linked` figure. The one V2 person is the open `unlinked_persons_placeholder` gap. |
| Person with one accused | the linked remainder | PASS |
| Person with more than one accused | 0 | EXPECTED for this data. The schema allows it. Nothing in the read path collapses those rows, because there are none to collapse. |

### T09 Arrests with null accused

Null `accused_id`: 2,434. Split: V1 2,312 and V2 122. Those are the open `unresolved_arrest_accused_link` gaps. Arrests whose `accused_id` is set all match an accused row. Arrests all match a crime. Sample null arrests still have a `crime_id`: `V1:8155`, `V1:14114`, `V1:13103`. Status: EXPECTED. The relationship was not guessed.

The V1 person-link gap is not one row per accused. Its key is the shared `(fir, name, father)` correlation plus the candidate count. There are 616 V1 accused with a null person and 554 open gap rows. The accused rows are all present. Status: EXPECTED.

### T10 Police station

| Check | Actual | Status |
|---|---:|---|
| V1 `ps_code` set | 3,582 | PASS |
| V1 `ps_code` null | 3,723 | EXPECTED |
| V2 `ps_code` null | 0 | PASS |
| Null V1 row that still has `ps_resolution` | 0 | PASS |
| Set V1 row that has `ps_resolution` | 3,582 | PASS |
| Non-null code with no hierarchy row | 0 | PASS |

Resolution example: `{"basis": "exact_ps_name_and_district", "ps_code": "2011004", "hierarchy_ps_code": "2011004"}`.

Unresolved samples keep the source names and a null code: Burgampahad / Bhadradri Kothagudem, Adilabad I TN / Adilabad, Lingapur / Komaram Bheem Asifabad.

Same station name in different districts does not share a code. `cyber crime ps` has 3 districts and 3 codes. `begumpet ps`, `balanagar ps`, and the other repeated names each have one code per district. Same normalized name plus same district never has two codes (0 groups). Status: PASS.

V2 crime rows store `ps_code` and leave `ps_name` null. The name is on `be_read.hierarchy`. Example: crime `6866751183d650371a3e2e9a`, code `2023109`, Tolichowki PS, Hyderabad. Status: APPLICATION INTEGRATION REQUIREMENT for any query that reads `crimes_unified.ps_name` instead of the hierarchy.

### T11 Court and chargesheet

Module counts match the source. Chargesheets with no crime: 0. Raw id `1` returns both:

```text
V1:court:1                     crime 2011083180180
V2:charge_sheet_updates:1      crime 62a0b38fe32fb443129faa96
```

Raw ids that appear in more than one source/module: 6,193. Distinct `charge_sheet_id`: 20,813, equal to the row count. Status: PASS.

### T12 FSL

Source `fsl_case_property` 2,006. Distinct observations 2,006. Unified rows 2,006. Physical observation rows 2,009 because three records were observed more than once. Reconciliation status: INTENTIONALLY_EXCLUDED. The 2,006 unified rows were not deleted. Status: PASS.

### T13 Identity

1,131 links, all `candidate`. Confirmed 0. Self-links 0. Duplicate pairs 0. Reverse pairs 0. Links to a missing person 0. Status: PASS. Candidates are not confirmed people.

### T14 Change log

Total 1,338,633. Classifications: `initial_observation` 1,330,134, `business_change` 8,499. No other classification. Duplicate tuples of `(entity, unified_id, field, observed_at, source_run_id, change_classification)`: 0. Crime and accused history ids all still exist on the current tables.

`observed_at` on 2026-08-24: 42 rows, all `accused` / `accused_status` / `business_change`. `bulk_event_exclusions` is empty, so those 42 rows were not reclassified. Status: PASS.

Chargesheet history is different. Of 116,848 `chargesheet` log rows, 36,370 use a namespaced id and 80,478 still use the raw id from before the key repair. Those 80,478 do not match a current `charge_sheet_id`. Sample raw id `4174` is an `initial_observation`. Current keys are unique and namespaced. Status: EXPECTED for an append-only log. Application integration requirement: do not inner-join every historical `change_log.unified_id` to the current chargesheet key.

### T15 Gap ledger

No gap type falls through to `ETL_DEFECT`.

| Source | Type | Status | Rows | Class |
|---|---|---|---:|---|
| V1 | unresolved_record_key | OPEN | 33530 | KNOWN_SOURCE_LIMITATION |
| V1 | unresolved_v1_ps_code | OPEN | 3723 | UNRESOLVED_RELATIONSHIP |
| V1 | unresolved_arrest_accused_link | OPEN | 2312 | UNRESOLVED_RELATIONSHIP |
| V1 | unresolved_accused_person_link | OPEN | 554 | UNRESOLVED_RELATIONSHIP |
| V1 | ora_06502_window | OPEN | 180 | KNOWN_SOURCE_LIMITATION |
| V1 | v1_person_key_absent | OPEN | 69 | KNOWN_SOURCE_LIMITATION |
| V2 | unresolved_arrest_accused_link | OPEN | 122 | UNRESOLVED_RELATIONSHIP |
| V2 | unresolved_interrogation_person_link | OPEN | 11 | UNRESOLVED_RELATIONSHIP |
| V2 | unresolved_arrest_accused_link | RESOLVED | 8 | closed |
| V2 | fk_retry_capped | OPEN | 3 | KNOWN_SOURCE_LIMITATION |
| V2 | fk_retry_capped | RESOLVED | 1 | closed |
| V2 | address_unresolved | OPEN | 1 | DATA_QUALITY |
| V2 | unlinked_accused | OPEN | 1 | KNOWN_SOURCE_LIMITATION |
| V2 | unlinked_persons_placeholder | OPEN | 1 | KNOWN_SOURCE_LIMITATION |

Status: PASS.

### T16 Duplicates

Duplicate primary keys: 0 on crimes, accused, persons, arrests, and chargesheets. Duplicate `(source_system, source_record_id)` on crimes: 0. Duplicate `(source_system, source_module, source_record_id)` on chargesheets: 0. Status: PASS.

### T17 Randomized traversal

One hundred crimes chosen by `md5(crime_id)`: 0 accused rows whose `person_id` points at a missing person. Twenty of those crimes are listed below. Counts come from left-style existence checks, so a zero does not remove the crime.

| Source | Crime | FIR | Date | PS code | Accused (null person) | Arrests (null accused) | Court | Sheet | Updates |
|---|---|---|---|---|---:|---:|---:|---:|---:|
| V2 | `6866751183d650371a3e2e9a` | 2023109250062 | 2025-07-03 | 2023109 | 3 (0) | 3 (0) | 0 | 1 | 1 |
| V2 | `650b36bf18498da12f1b19bf` | 2011020230190 | 2023-09-20 | 2011020 | 2 (0) | 2 (0) | 0 | 1 | 1 |
| V2 | `6598c9d93e830a95d16066bb` | 2023514240004 | 2024-01-05 | 2023514 | 2 (0) | 2 (0) | 0 | 2 | 1 |
| V1 | `2025007200327` | 2025007200327 | 2020-12-16 | null | 2 (0) | 2 (0) | 1 | 0 | 0 |
| V2 | `62f3eea623dc926b12b5eb28` | 2025054220182 | 2022-08-10 | 2025054 | 4 (0) | 4 (0) | 0 | 1 | 1 |
| V2 | `67f699ccaec0e666a36bad4a` | 2011050250090 | 2025-04-09 | 2011050 | 3 (0) | 3 (0) | 0 | 1 | 1 |
| V2 | `6457e7c1b5008d59a2918b2b` | 2022013230385 | 2023-05-07 | 2022013 | 3 (0) | 3 (0) | 0 | 0 | 0 |
| V2 | `64a30e4af58534269fd272f6` | 2023039230173 | 2023-07-01 | 2023039 | 3 (0) | 3 (0) | 0 | 1 | 1 |
| V1 | `2011001050161` | 2011001050161 | 2005-11-28 | null | 2 (0) | 2 (0) | 1 | 0 | 0 |
| V2 | `651fec25c013a815a84e0542` | 2023045230303 | 2023-10-06 | 2023045 | 2 (0) | 2 (0) | 0 | 1 | 1 |
| V1 | `2011051210049` | 2011051210049 | 2021-10-29 | null | 1 (0) | 1 (0) | 1 | 0 | 0 |
| V2 | `689609747aa2c91f3d5b20ba` | 2011057250173 | 2025-08-08 | 2011057 | 2 (0) | 2 (0) | 0 | 1 | 1 |
| V2 | `66d3d23bcddf226951d641f8` | 2023002240412 | 2024-08-31 | 2023002 | 1 (1) | 1 (1) | 0 | 0 | 0 |
| V2 | `66159d661f8fc710d51af8e8` | 2060015240119 | 2024-04-09 | 2060015 | 4 (0) | 4 (0) | 0 | 1 | 1 |
| V1 | `2022051160008` | 2022051160008 | 2016-01-08 | 2022051 | 2 (0) | 2 (0) | 1 | 0 | 0 |
| V1 | `2022046170516` | 2022046170516 | 2017-05-11 | null | 3 (0) | 3 (0) | 1 | 0 | 0 |
| V1 | `2029019170053` | 2029019170053 | 2017-09-13 | 2029019 | 1 (0) | 1 (0) | 1 | 0 | 0 |
| V2 | `6a25a69b1b5c78d8aac31405` | 2023010260176 | 2026-06-07 | 2023010 | 3 (0) | 3 (0) | 0 | 0 | 0 |
| V1 | `2036083060141` | 2036083060141 | 2006-11-25 | 2036083 | 1 (0) | 1 (0) | 1 | 0 | 0 |
| V2 | `64ff54bb27f36c3ccb2b5ab5` | 2024033230228 | 2023-09-11 | 2024033 | 1 (0) | 1 (0) | 0 | 1 | 1 |

Crime `66d3d23bcddf226951d641f8` is the explicit missing-link case in this sample: the accused has no person and the arrest has no accused. The crime is still returned. Status: PASS.

### T18 Search behavior

Deterministic keys from `md5(person_id)` / `md5(crime_id)`:

| Query | Key | Rows | Distinct | Plan | Time | Status |
|---|---|---:|---:|---|---:|---|
| Exact person name | Usirika Ganga Raju | 1 | 1 | Index scan `idx_persons_unified_full_name` | 0.019 ms | PASS |
| Prefix `Usi%` | same name | 1 | 1 | Seq scan | 26.647 ms | PASS at this volume |
| Exact relative name | Appa Raju | 1 | 1 | — | — | PASS |
| Exact phone | 7013437373 | 1 | 1 | Index scan `idx_persons_unified_phone` | 0.019 ms | PASS |
| DOB `2001-01-01` | — | 2 | — | — | — | EXPECTED. Only 79 persons have a DOB. |
| FIR number | 2023109250062 | 1 | 1 | Seq scan | 7.121 ms | PASS at this volume |
| Crime by id | `6866751183d650371a3e2e9a` | 1 | 1 | PK index | 0.022 ms | PASS |
| Crime plus accused plus person | same id | — | — | nested index lookups | 0.073 ms | PASS |

`full_name IS NULL` on 2 persons. `ILIKE` does not return those rows. `date_of_birth` is null on 52,962 of 53,041 persons. Status: PASS. A prefix search is a sequential scan because the name index is an equality index. That is acceptable at 53,041 rows and is not a reason to add an index in this exercise.

### T19 Year coverage

V1 crimes run from 1991 through 2022. V2 crimes run from 2022 through 2026. Both sources are present in 2022 (V1 908, V2 372).

Years with no crimes: 1992, 1996, 1998, 1999. Unified V1 crimes equal the source FIR count, so those years are absent from the source extract, not dropped by the load. Early years are sparse (1991 is 1 crime, 1993 is 4). Status: EXPECTED.

Years 1991 through 2001 have arrests and court rows and zero accused. That matches the 723 source FIRs that have no dossier accused. Arrests from `accused_details` are still there. Status: EXPECTED.

Court rows stop where V1 stops. Chargesheets and updates start in 2022. In 2026 there are 2,596 crimes, 550 chargesheets, and 334 updates. That is a smaller share than 2024–2025, which fits open recent cases, not a missing module. Status: EXPECTED. Not reclassified as a defect.

### T20 Completeness

| Relationship | Linked | Unlinked or null | Share null |
|---|---:|---:|---:|
| Accused to person | 49,653 | 695 | 1.38% |
| Arrest to accused | 50,752 | 2,434 | 4.58% |
| Accused to crime | 50,348 | 0 | 0% |
| Arrest to crime | 53,186 | 0 | 0% |
| Chargesheet to crime | 20,813 | 0 | 0% |
| V1 crime to ps_code | 3,582 | 3,723 | 50.97% of V1 |
| V2 crime to ps_code | 9,583 | 0 | 0% |

Status: PASS. The null shares are the known unresolved links.

### T21 Source safety

Start and end: V1 fir 7,305, V2 crimes 9,583. Both `CREATE TABLE` attempts raised `ReadOnlySqlTransaction` and were rolled back. Status: PASS.

## Findings

| Finding | Class |
|---|---|
| V1 accused 34,390 observations to 17,356 current rows | EXPECTED |
| 69 V1 detail rows with no person key, visible as open gaps | SOURCE LIMITATION |
| Two shared `person_code` values, one person each, no conflicting payload names | EXPECTED |
| 3,723 V1 crimes with null `ps_code` and no guessed code | SOURCE LIMITATION |
| 695 accused with null person, 2,434 arrests with null accused | SOURCE LIMITATION |
| 723 V1 and 18 V2 crimes with no accused, matching the source | EXPECTED |
| 3,387 V1 persons with no accused link | SOURCE LIMITATION |
| FSL observed and kept, not part of the new merge | EXPECTED |
| 1,131 identity rows, none confirmed | EXPECTED |
| 42 accused-status changes on 2026-08-24 left as business changes | EXPECTED |
| 80,478 chargesheet history rows still carry the pre-namespace raw id | EXPECTED |
| No `ETL_DEFECT` gap type | EXPECTED |
| Duplicate primary keys and broken foreign keys among required links | none found |

No ETL code was changed. Nothing in this pass required a fix.

## Application integration requirements

These are requirements for the later DOPAMS work. They are not database defects.

1. Read `be_read`, not `firs_mv` or `accuseds_mv`. Do not point the current GraphQL SQL at `dopams_cctns`.
2. Treat `charge_sheet_id` as `{source_system}:{module}:{raw_id}`. A lookup by raw id must return every module. Raw id `1` is two records.
3. Keep null `ps_code`, null `person_id`, and null arrest `accused_id`. Do not fill them.
4. For a V2 crime, take the station and district from hierarchy. `crimes_unified.ps_name` is null on V2 rows even when `ps_code` is set.
5. Accused name, relative name, phone, and date of birth are on `persons_unified`. An unlinked accused has no person row to search.
6. Do not treat `identity_links` as confirmed duplicates or as `person_deduplication_tracker`.
7. Do not inner-join historical `change_log.unified_id` for chargesheets to the current key. Rows written before the namespace repair use the raw id.
8. Users, files, IR54, brief facts, dedup, and drug fields are not in this database. They need an owner before cutover.
9. `fir_reg_num` equality and name prefix searches scan the table. At today's size that is a few milliseconds to about 27 ms. An index can wait until the application query is real.

## Acceptance checklist

| Check | Result |
|---|---|
| No unexplained primary-key duplicates | PASS |
| No unexplained foreign-key violations | PASS |
| No unexplained orphans | PASS. Null links are ledgered. |
| No unexplained source-to-unified gaps | PASS |
| No unexpected cross-module id collisions | PASS. The 6,193 shared raw ids have distinct keys. |
| V1 and V2 untouched | PASS |
| Random FIR, accused, person, arrest, court, and chargesheet traversal | PASS |
| Null relationships handled | PASS |
| Null police-station codes intentional | PASS |
| FSL exclusion explainable | PASS |
| Identity candidates not confirmed | PASS |
| Change log consistent | PASS, with the historical raw-id note |
| Gap ledger consistent | PASS |
| Year coverage explained | PASS |
| Application-style queries return the selected rows | PASS |
| Performance acceptable at this volume | PASS |

The unified database is ready for application integration design. It is not a cutover of the current DOPAMS API.
