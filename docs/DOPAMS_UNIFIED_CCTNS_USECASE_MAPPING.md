# DOPAMS Unified CCTNS Use-Case Mapping

## 1. Objective

Map each CCTNS use case in the DOPAMS GraphQL API to the current-state tables in `dopams_cctns`.

The application today reads `dev-2` materialized views. Those views are not the target shape. This document does not propose copying `firs_mv`, `accuseds_mv`, or the advanced-search views. A future service can select from the unified tables and assemble the GraphQL response in application code.

Nothing in this phase was implemented. `dev-2`, `dopams_cctns`, and the DOPAMS backend were not modified. Counts below were read in a session with `transaction_read_only = on` against `dopams_cctns`.

## 2. Current application architecture

```text
DOPAMS GraphQL
    |
    | DATABASE_URL  (Prisma, not read-only)
    v
  dev-2.public
    |
    +-- firs_mv, accuseds_mv
    +-- advanced_search_firs_mv, advanced_search_accuseds_mv
    +-- criminal_profiles_mv
    +-- base tables the views and some services also read
        crimes, accused, persons, hierarchy, arrests,
        chargesheets, charge_sheet_updates, disposal,
        fsl_case_property, brief_facts_accused, brief_facts_drug,
        files, interrogation_reports and child tables,
        chargesheet_acts, chargesheet_accused,
        person_deduplication_tracker, user
```

`dev-2` holds 7,403 crimes. All of those ids are V2 crimes inside `crimes_unified`. The unified database also holds the rest of V2 (9,583 crimes total) and 7,305 V1 crimes. There is no rule that one source replaces the other.

Read current state, not `*_source`. Source tables keep the raw payload for ETL. The application should query `*_unified`, and station names from `hierarchy_source` payload only because `hierarchy_unified` stores `ps_code` and not the name columns. `be_read.hierarchy` already projects those names. `be_read` is a thin projection. It is not a replacement for the materialized views, and this mapping does not require new views.

## 3. Application use cases

37 CCTNS GraphQL fields. User queries are not CCTNS.

| Area | Fields |
|---|---|
| FIR | `fir`, `firs`, `firStatistics`, `overviewStatistics`, `firFilterValues`, `uiptCasesStatistics`, `firsAbstract` |
| Seizure screens | `seizureStatistics`, `seizuresFilterValues`, `seizuresAbstract` |
| Accused | `accused`, `accuseds`, `accusedStatistics`, `accusedFilterValues`, `accusedAbstract` |
| Advanced search | `advancedSearch`, `fieldAutoComplete` |
| Profile | `criminalProfile`, `criminalProfiles`, `accusedCaseHistory`, `personCaseHistory`, `searchPersonsByName` |
| Network | `criminalNetworkDetails` |
| Dashboard | `overallCrimeStats`, `seizuresByDrugForm`, `caseStatusClassification`, `regionalOver  view`, `drugData`, `drugList`, `drugCases`, `caseClassificationUI`, `trialCasesClassification`, `accusedTypeClassification`, `domicileClassification`, `stipulatedTimeClassification`, `investigationRelatedInfo`, `courtRelatedInfo` |

Pagination on lists is page and limit, offset `(page - 1) * limit`, sort column chosen by the client, default `crimeRegDate` or `firDate` descending, `NULLS LAST`.

## 4. FIR / Crime

What the FIR screens actually use, taken from `src/schema/firs/services/index.ts` and the `firs_mv` definition:

| Need | `dev-2` today | Unified | Status |
|---|---|---|---|
| Crime id | `crimes.crime_id` / `firs_mv.id` | `crimes_unified.crime_id` | AVAILABLE. V2 hex id or V1 `fir_reg_num`. Disjoint. |
| FIR number, registration number, date | crime columns | `fir_num`, `fir_reg_num`, `fir_date` | AVAILABLE |
| Year | derived on the view | `occurrence_year` for V1. V2 year is `fir_date`, not a stored year | PARTIALLY_AVAILABLE. Service can use `EXTRACT(YEAR FROM fir_date)`. |
| Station code | `crimes.ps_code` NOT NULL | `ps_code`, null on 3,723 V1 rows | AVAILABLE, nullable |
| Station and unit names | inner join `hierarchy` | `be_read.hierarchy` (`ps_name`, `dist_name`, circle, sdpo, range, zone) left-joined on `ps_code`. V1 also has `ps_name` and `unit_district` on the crime row | AVAILABLE when a code or a source name exists. Null when enrichment did not match. Do not guess. |
| Acts / sections | crime column | `acts_sections` | AVAILABLE |
| Brief facts text | `crimes.brief_facts` | `brief_facts` (V1 from `fir_contents`) | AVAILABLE |
| Case status | `case_status` | `case_status` | AVAILABLE |
| Major / minor head, IO name, IO rank | V2 crime columns | same columns. Not mapped for V1 | PARTIALLY_AVAILABLE |
| Case class, crime type, FIR type | `class_classification`, `crime_type`, `fir_type` | V2 only, inside `additional_json_data` | PARTIALLY_AVAILABLE. Not a reason to add columns. The service can read the JSON keys that already exist. |
| Accused on the FIR | JSON inside `firs_mv` | `accused_unified` where `crime_id` matches | AVAILABLE as rows, not as the old JSON |
| Place and occurrence time | `crimes.additional_json_data` on detail | V2 residual JSON can contain the source `additional_json_data`. V1 residual keeps only `attach_path` and `dms_file_name` | PARTIALLY_AVAILABLE |

A crime with no accused must still be returned. 723 V1 FIRs and 18 V2 crimes have no accused. The crime query does not join accused.

Future FIR list, in the service, not as a view:

```text
SELECT crime_id, source_system, fir_num, fir_reg_num, fir_date,
       ps_code, ps_name, district_name, case_status, acts_sections,
       major_head, minor_head, io_name, brief_facts
FROM crimes_unified
LEFT JOIN hierarchy names on ps_code
ORDER BY fir_date DESC NULLS LAST
LIMIT / OFFSET
```

No inner join. `source_system` goes out on the response so V1 and V2 stay visible. Filters on `unit` and `ps` use the name columns and must keep rows whose names are null when the filter is empty.

## 5. Accused

`accuseds_mv` is `accused` left join brief facts, inner join crime, inner join hierarchy, left join persons. The inner join to hierarchy is what would hide accused on a null-station crime. The person join is already a left join: 72 null `person_id` rows on `dev-2` stay in the view.

Unified accused, read-only:

| Source | Rows | `person_id` null |
|---|---:|---:|
| V1 | 17,356 | 616 |
| V2 | 32,992 | 79 |

V1 accused is a logical collapse of 34,390 dossier rows. The unified `accused_id` is not the raw dossier id and is not `person_id`.

| Need | Unified column | Status |
|---|---|---|
| Accused id, crime id | `accused_unified.accused_id`, `crime_id` | AVAILABLE |
| Status | `accused_status` | AVAILABLE. On `dev-2` the view prefers `brief_facts_accused.status` over `accused.accused_status`. That preference is application data. |
| CCL flag | `is_ccl` (V2) | PARTIALLY_AVAILABLE |
| Arrested / absconding flags on the accused row | not mapped for V2 accused. V1 maps `is_arrested` and `arrested_date` | PARTIALLY_AVAILABLE. Arrest facts for V2 belong on `arrests_unified`. |
| Name | `persons_unified.full_name` via left join `person_id` | AVAILABLE when linked. Null when `person_id` is null. `full_name` already exists. Do not add a generated column. |
| Alias, relative, gender, age, phone, occupation, caste, nationality | person columns | AVAILABLE on the person row. V1 occupation is unmapped. |
| Surname, split address, domicile, religion, physical features | `accuseds_mv` / person columns on `dev-2` | NOT_AVAILABLE on current state |
| Brief-facts accused narrative | `brief_facts_accused` | DEV2_ONLY |

Display an accused with a null person. Do not inner-join `persons_unified`.

## 6. Person

`persons_unified`: V1 20,127 (name present on all), V2 32,914 (name null on 2). V1 persons come from `accused_details.person_code`, not from the dossier accused id. V2 persons are the V2 person id. The two are never merged into one row. Cross-source identity is only `identity_links`, and those 1,131 rows are candidates, not confirmed matches.

The wide address on `dev-2` (`presentHouseNo`, street, ward, district, pin) is not stored. V2 mapping keeps a single `present_address_text` / `permanent_address_text` from the first non-empty source field. The service can expose that text. It cannot reconstruct the old address parts.

`date_of_birth` is display-only. It is not a match key.

## 7. Arrest

`arrests_unified.accused_id` is nullable.

| Source | Rows | `accused_id` null |
|---|---:|---:|
| V1 | 20,198 | 2,312 |
| V2 | 32,988 | 122 |

Each row has `crime_id`, `is_arrested`, `arrested_date`. `arrest_ps` is on the table. `is_41a_crpc`, which `investigationRelatedInfo` counts on `dev-2.arrests`, is not mapped.

An arrest with a null accused still belongs to the crime. The crime and the arrest query use `crime_id`. They do not require `accused_id`.

V1 arrest identity and V1 person identity both start from `accused_details`, but a failed name match leaves `accused_id` null. Do not fill it in the query.

## 8. Chargesheet

Three modules share `chargesheets_unified`. They are not one application object.

| `source_system` | `source_module` | Rows | What the app uses today |
|---|---|---:|---|
| V1 | `court` | 7,534 | No court table. Court name is pulled from crime JSON and from dashboard status text. |
| V2 | `chargesheets` | 7,086 | `chargesheets.id` uuid. Counted by `prisma.chargesheet.count`. |
| V2 | `charge_sheet_updates` | 6,193 | `charge_sheet_updates.id` integer, plus hex `update_charge_sheet_id`. |

Primary key is `{source}:{module}:{raw id}`. Raw id `1` is both `V1:court:1` and `V2:charge_sheet_updates:1`, on different crimes. A lookup by raw id must return every module, never `LIMIT 1`.

Columns that exist: `chargesheet_no`, `chargesheet_date`, `court_name`, `court_case_num`, `court_disposal_date`, `court_disposal_type`, `court_remarks`, `acts_sections`, `accused_person_ids`. Which of those are filled depends on the module. V2 updates store status in `court_disposal_type` and the court case number in `court_case_num`. They do not store a court name.

`chargesheet_acts`, `chargesheet_accused` (including NBW), and chargesheet files are `dev-2` tables. They are not in this model.

## 9. Court

Court is not a separate unified table. V1 court rows are `source_module = 'court'`.

`courtRelatedInfo` on the dashboard does not read a court table. It counts `crimes.case_status` for pending trial, acquittal, conviction, and abated, and counts `chargesheet_accused.requested_for_nbw`. Bail counts are hardcoded to 0. Status text can move to `crimes_unified.case_status`. The NBW count cannot. V1 `court_name` and `court_case_num` can be listed per crime with `source_module = 'court'`, which is new information the current list API does not show.

## 10. Seizure / drug / property

These are different objects. The seizure screens do not read `mo_seizures`.

| Application data | Where it is now | Unified | Status |
|---|---|---|---|
| Drug name, kg/ml/count, worth, commercial flag | `brief_facts_drug` joined by the home and seizure services | not a unified table | DEV2_ONLY |
| V2 MO seizure | inside `firs_mv` JSON as well as `mo_seizures` | `seizures_unified` V2: 3,540 rows. `drug_type` null on 5. `quantity` null on all 3,540. Lat/long and address mapped | PARTIALLY_AVAILABLE. Quantity and worth are not in the current-state map. |
| V1 drug fields on the dossier | not the DOPAMS seizure screen | `seizures_unified` V1: 34,390 rows (one per dossier row, not collapsed to the logical accused). `drug_type` null on 21,316. `quantity` null on 22,150 | PARTIALLY_AVAILABLE, and it is not the same grain as `accused_unified` |
| Property details | `properties` embedded in `firs_mv` | `properties_unified`: 7,683 rows, id and `crime_id` only | NOT_AVAILABLE beyond “a property row exists for this crime” |

Do not point `seizureStatistics` at `seizures_unified`. That would change the metric from brief-facts NLP to MO-seizure / dossier drug rows. Whether a future screen should show `seizures_unified` is a product decision. It is not a substitute.

## 11. Disposal

`caseStatusClassification` and the FIR status filter walk `firs_mv.disposalDetails` (`disposalType` text such as conviction, acquittal, compounded, abated). That JSON is built from the `dev-2` `disposal` table.

`disposal_unified` has 482 rows and only `disposal_id`, `source_record_id`, and `crime_id`. Disposal type was not mapped. A count of disposal rows is not the dashboard classification. `court_disposal_type` on chargesheets is a different field and must not be substituted.

Status for the current disposal waterfall: NOT_SUPPORTED.

## 12. FSL

`fsl_case_property` on `dev-2` has 2,056 rows. The FIR view embeds them as `casePropertyDetails`. `investigationRelatedInfo` counts rows whose status contains “pending”.

`fsl_unified` has 2,006 historical rows (id and `crime_id` only) and is excluded from the current-state merge. It has no status and no property description. It cannot serve the pending-FSL count or the FIR property panel.

Status: NOT_SUPPORTED for the current FSL screens. Leave them on `dev-2`.

## 13. Advanced search

`advancedSearch` picks `advanced_search_firs_mv` or `advanced_search_accuseds_mv` from the requested columns, then applies the filter list. `fieldAutoComplete` selects distinct values from the same views.

| Search field group | Unified source | Status |
|---|---|---|
| id, FIR number, FIR date, sections, case status, heads, IO, brief facts | `crimes_unified` | READY_WITH_QUERY_CHANGE |
| `psCode` | `crimes_unified.ps_code`, which may be null | READY_WITH_QUERY_CHANGE. A filter for a specific station does not have to return null-station crimes. An unfiltered search must. |
| Station, district, circle, range, zone names | hierarchy payload left-joined | READY_WITH_QUERY_CHANGE |
| `caseClass`, `crimeType`, `firType` | V2 `additional_json_data` only | PARTIALLY_AVAILABLE |
| Drug name, quantity, worth | `brief_facts_drug` | DEV2_ONLY |
| Accused code, status, CCL | `accused_unified` | READY_WITH_QUERY_CHANGE for those three |
| Physical features, accused role/type, seq | not mapped | NOT_AVAILABLE |
| `fullName`, alias, relative, gender, age, phone, email, occupation, caste, nationality | `persons_unified` left join | READY_WITH_QUERY_CHANGE |
| Split present/permanent address, domicile, religion, education | not stored | NOT_AVAILABLE |
| Stipulated period | computed in the service from FIR date and case class | PARTIALLY_AVAILABLE, because case class is V2 JSON only |

The future query is a normal select with left joins and a `WHERE` built from the filter list. It does not need a search materialized view. Fields the unified tables do not have should stay on the `dev-2` path until a product decision, rather than being silently dropped from a mixed filter.

## 14. Criminal profile

`criminalProfile` / `criminalProfiles` read `criminal_profiles_mv` (`SELECT *`). Detail then loads crimes from `accuseds_mv`, chargesheets with `chargesheet_acts` and `chargesheet_accused`, and `interrogation_reports` plus child tables.

| Piece | Owner | Unified |
|---|---|---|
| Person name and identifiers | CCTNS person | `persons_unified` |
| Crimes for that person | accused → crime | `accused_unified.person_id` left side, crime by `crime_id`. Persons with no accused (3,387 V1, 1 V2) still exist and simply have an empty crime list. |
| Drug association on the profile | `brief_facts_drug` | DEV2_ONLY |
| Chargesheet acts and accused-on-chargesheet | `dev-2` child tables | not in `chargesheets_unified` |
| Interrogation narrative | `interrogation_reports` and children | `interrogation_unified` is 19,569 rows with `crime_id` and nullable `person_id` (11 null). No narrative columns. |
| Repeat-offender grouping | `person_deduplication_tracker` | not equivalent to `identity_links` |

`accusedCaseHistory` and `personCaseHistory` walk accused and crime, and the network service expands ids through `person_deduplication_tracker`. The crime walk can use unified tables. The cluster walk stays on `dev-2`.

`uploadCriminalProfileFile` writes `files` on `dev-2`. That stays on `dev-2`.

## 15. Dashboard

Counts will change if a metric moves to the unified tables, because the population becomes 7,305 V1 crimes plus 9,583 V2 crimes, not the 7,403-crime `dev-2` subset. That difference is expected. It is not a defect to correct by filtering back to the 7,403 ids.

| Metric | Current source | Unified possibility |
|---|---|---|
| Crime count, by status text, by date | `firs_mv` / `crimes` | `crimes_unified`. Meaning changes: V1 included, full V2 included. |
| Accused count | `accuseds_mv` | `accused_unified`. V1 is the collapsed 17,356, not 34,390 dossier rows. |
| Arrest count | `arrests` | `arrests_unified`, including null `accused_id`. |
| Chargesheet filed | `chargesheets` | `source_module = 'chargesheets'`. Do not add court or updates into that count. |
| Charge-sheet update / CC number | `charge_sheet_updates` | `source_module = 'charge_sheet_updates'`. `court_case_num` is the mapped case number, not `taken_on_file_case_type`. |
| Drug form, drug list, drug cases | `brief_facts_drug` | DEV2_ONLY |
| Disposal class (conviction, acquittal, police disposal) | `disposalDetails` JSON | NOT_SUPPORTED |
| Domicile | `accuseds_mv` domicile | NOT_SUPPORTED |
| Accused type / role | brief facts or accused type | NOT_SUPPORTED. `is_ccl` is a different flag. |
| FSL pending | `fsl_case_property.status` | NOT_SUPPORTED |
| 41A / absconding / property forfeiture | arrests flag, brief-facts status, disposal type | NOT_SUPPORTED on current-state columns |
| Station-wise overview | crimes + hierarchy, often with drug totals | Station slice is possible. Drug slice stays on `dev-2`. |
| Stipulated time | FIR date + case class | Partial. Date is available. Class is V2 JSON only. |

## 16. Documents / files / IR

| Data | Owner | Move to unified reads? |
|---|---|---|
| `files`, file proxy, criminal-profile media | application | No |
| FIR copy URL, document JSON on `firs_mv` | `dev-2` files and crime JSON | No, except V1 `attach_path` / `dms_file_name` already kept in `additional_json_data` |
| `brief_facts_accused`, `brief_facts_drug` | application enrichment on `dev-2` | No |
| Interrogation child tables and IR text | `dev-2` | No. Unified IR is an id link only. |
| `user` | application | No |

## 17. V1 / V2 handling

Every crime, accused, person, arrest, seizure, and chargesheet row carries `source_system`. Queries that power a list should not filter to V2 unless the client asks. Both sources are valid. There is no precedence.

The service should return `source_system` and, for chargesheets, `source_module`. Clients that only understand a V2 hex `crime_id` will need to accept a V1 `fir_reg_num` as `crime_id`. That is a response-mapping change, not a database change.

V1 relationships that differ from V2:

- Accused id is a logical key, not the raw dossier id.
- Person id is `person_code`.
- Arrest may not resolve to that logical accused.
- Seizure grain is the dossier row, not the logical accused.
- Station code is null when name+district did not match hierarchy.
- Heads and IO are empty.
- Court lives in the chargesheet table as module `court`.

## 18. NULL relationship handling

Confirmed on `dopams_cctns` in this read-only session.

| Situation | Rows | Join the query must use |
|---|---:|---|
| Crime, `ps_code` null | 3,723, all V1 | Crime is the driving table. Hierarchy is a left join. |
| Accused, `person_id` null | 695 (616 V1, 79 V2) | Accused is the driving table. Person is a left join. |
| Arrest, `accused_id` null | 2,434 (2,312 V1, 122 V2) | Arrest stays. Accused is a left join. |
| Crime with no accused | 723 V1 + 18 V2, from the earlier validation | Do not inner-join accused when listing crimes. |
| IR, `person_id` null | 11 | Left join person if IR ids are ever listed. |
| Chargesheet always has `crime_id` | enforced by the foreign key | The crime does not need a chargesheet to appear. |

`dev-2` views inner-join hierarchy. That is safe only because `dev-2.crimes.ps_code` is NOT NULL. The same SQL against unified crimes would drop 3,723 crimes and their accused.

## 19. Unified table mapping

Current-state tables a future CCTNS read may use:

| Table | Rows observed | What a query can honestly return |
|---|---:|---|
| `crimes_unified` | 16,888 | Identity, FIR fields, status, brief facts, optional station code, V2 heads/IO, residual JSON |
| `hierarchy_unified` / hierarchy payload | 816 codes | Names and parent units. Not a column on the crime when `ps_code` is null. |
| `accused_unified` | 50,348 | Link to crime, optional person, status, V1 arrest flags, V2 code and CCL |
| `persons_unified` | 53,041 | Name and the thin demographic set |
| `arrests_unified` | 53,186 | Crime, optional accused, arrested flag and date |
| `chargesheets_unified` | 20,813 | Three modules, per-crime court or chargesheet fields listed in section 8 |
| `seizures_unified` | 37,930 | Drug type where the source had it. Not brief-facts worth or V2 quantity. |
| `properties_unified` | 7,683 | Existence and crime id only |
| `disposal_unified` | 482 | Existence and crime id only |
| `interrogation_unified` | 19,569 | Id, crime, optional person |
| `fsl_unified` | 2,006 | Historical id and crime id. Not the operational FSL screen. |

Do not read `change_log`, `source_gap_ledger`, or `*_source` from GraphQL. Do not treat `identity_links` as the dedup tracker.

## 20. Proposed GraphQL query changes

No field can keep its current SQL. None of the 37 fields is a drop-in against the unified tables, so “fully supported with no query change” is 0. A field is `READY_WITH_QUERY_CHANGE` only when every input to its current filters and counts exists on a current-state column. None of the 37 meet that bar, because each one also reads a `dev-2` enrichment (drug NLP, disposal type, files, dedup, address parts, or FSL status) or a classification that was never mapped.

The entity queries below are still the right implementation unit. They do not recreate the views. The service maps snake_case to the GraphQL names.

| Proposed repository read | Tables | Joins | Sort / page |
|---|---|---|---|
| Crime page | `crimes_unified` | `LEFT JOIN` hierarchy on `ps_code` | `fir_date DESC NULLS LAST`, limit/offset. Index today: `ps_code`, `case_status`. No `fir_date` index yet. |
| Crime by id | `crimes_unified` | none required | primary key |
| Accused for a crime | `accused_unified` | `LEFT JOIN persons_unified` | by `accused_id`. Index: `crime_id`, `person_id` |
| Arrests for a crime | `arrests_unified` | `LEFT JOIN accused_unified` | by `arrest_id`. Does not drop null accused |
| Chargesheets for a crime | `chargesheets_unified` | none | `WHERE crime_id = $1`, return `source_module`. Index: `crime_id` |
| Chargesheets by raw id | same | none | no `LIMIT 1` |
| Person by id or name | `persons_unified` | none | `full_name` is indexed |

| GraphQL field | Current query | Unified tables | Change | Complexity |
|---|---|---|---|---|
| `firs`, `fir`, `firsAbstract`, `firFilterValues`, `overviewStatistics` | `firs_mv` | crimes, hierarchy, accused, persons | NEW QUERY. Nested drug, document, IR, FSL JSON stays empty or stays on `dev-2`. | High |
| `firStatistics`, `uiptCasesStatistics` | `firs_mv` plus disposal and drug JSON | crimes plus `dev-2` drug/disposal if those filters remain | NEW QUERY. Status-only slice can use `case_status`. Disposal slice cannot. | High |
| `seizureStatistics`, `seizuresFilterValues`, `seizuresAbstract` | `firs_mv`, `brief_facts_drug` | none equivalent | NO CHANGE to the data source. Remains `dev-2`. | n/a |
| `accused`, `accuseds`, `accusedAbstract`, `accusedFilterValues`, `accusedStatistics` | `accuseds_mv` | accused, persons, crimes, hierarchy | NEW QUERY. Name filters use `full_name`. Address-part and domicile filters have no column. | High |
| `advancedSearch`, `fieldAutoComplete` | advanced-search views | crimes, accused, persons, hierarchy for the mapped fields | NEW QUERY per field group. Drug and physical-feature filters stay on `dev-2`. | High |
| `criminalProfile`, `criminalProfiles` | `criminal_profiles_mv` plus chargesheet children and IR | persons, accused, crimes for the case list | NEW QUERY for the case list. Profile card, drugs, IR text, and files stay on `dev-2`. | High |
| `accusedCaseHistory`, `personCaseHistory` | `accuseds_mv`, Prisma accused | accused, crimes, persons | NEW QUERY for the case list. Dedup expansion stays on `dev-2`. | Medium |
| `searchPersonsByName` | profile / person services | `persons_unified.full_name` plus `dev-2` dedup if the screen merges clusters | NEW REPOSITORY METHOD for the name lookup. Do not read `identity_links` as if it were the tracker. | Medium |
| `criminalNetworkDetails` | Prisma accused, hierarchy, dedup tracker | accused, crimes, hierarchy for edges | NEW QUERY for edges. Cluster membership stays on `dev-2`. | High |
| `overallCrimeStats`, `regionalOverview`, `caseClassificationUI`, `stipulatedTimeClassification` | `firs_mv`, `crimes`, drug and disposal | crimes, hierarchy | NEW QUERY for counts that use status, date, and station. Drug and class slices stay partial. | Medium |
| `courtRelatedInfo` | `crimes.case_status`, `chargesheet_accused` | `case_status` only | NEW QUERY for the four status counts. NBW stays on `dev-2`. | Medium |
| `investigationRelatedInfo` | crimes, accused, brief facts, arrests, FSL, chargesheets, disposal | crimes, arrests, chargesheets for existence counts | NEW QUERY for UI count, arrest rows, and chargesheet-module counts. 41A, absconding, FSL pending, forfeiture stay on `dev-2`. | High |
| `seizuresByDrugForm`, `drugData`, `drugList`, `drugCases` | `brief_facts_drug` | none | NO CHANGE. DEV2_ONLY. | n/a |
| `caseStatusClassification`, `trialCasesClassification` | disposal JSON | disposal type not stored | NOT CURRENTLY SUPPORTED. Leave on `dev-2`. | n/a |
| `accusedTypeClassification`, `domicileClassification` | `accuseds_mv` | no role/domicile column | NOT CURRENTLY SUPPORTED. Leave on `dev-2`. | n/a |

`DATABASE_URL` stays on `dev-2`. A future CCTNS read uses the separate read-only connection. That wiring is not part of this phase.

## 21. Application-owned data

Stays on `dev-2`:

- `user` and authentication
- `files` and uploads
- `person_deduplication_tracker` and `dedup_*`
- `brief_facts_accused`, `brief_facts_drug`
- interrogation report bodies and child tables
- `chargesheet_acts`, `chargesheet_accused`, chargesheet media
- operational `fsl_case_property`
- `disposal` type text used by the dashboards

Moves to unified reads only where section 19 says the column exists: crime, accused, person, arrest, the three chargesheet modules, hierarchy names, and the thin seizure drug-type row if a future screen asks for MO seizures rather than brief-facts drugs.

## 22. Unsupported / missing data

| Item | Why it is missing | What to do |
|---|---|---|
| Disposal type waterfall | `disposal_unified` has no type | Keep the dashboard on `dev-2` |
| Domicile, accused role, physical features | never mapped | Keep those filters on `dev-2` |
| Drug worth and the seizure screens | `brief_facts_drug` is application data. V2 `seizures_unified.quantity` is null on all 3,540 rows | Do not retarget the seizure API |
| FSL pending and FSL panel | historical id only, merge excluded | Keep FSL on `dev-2` |
| Split postal address | stored as one text line | Service returns that line, not the old parts |
| IR narrative, documents, chargesheet acts | application / `dev-2` children | Stay on `dev-2` |
| Confirmed cross-source person merge | `identity_links` are candidates | Do not merge |

## 23. Performance considerations

Current `dev-2` baseline, from the architecture audit: FIR by id 0.042 ms (index), `firs_mv` count 0.728 ms, accused name equality about 47 ms (sequential scan on 25,398 rows).

Unified key lookups already measured on the read contract are in the same range for a single crime or the accused of one crime. A page of crimes ordered by `fir_date` does not have an index on `fir_date` today. At 16,888 crimes that sort is acceptable to try before adding an index. If a later measurement shows a sort as the cost, the candidate index is `(fir_date, crime_id)`, not a materialized view.

Accused-by-crime and chargesheet-by-crime already have indexes. Person name search can use `idx_persons_unified_full_name` for equality. A leading-wildcard `ILIKE` will not use that index, which is the same limitation as the current accused name scan.

Do not build a materialized view to match the old 0.042 ms number. That time is a cached projection. The relational query is the design unless a measured page query is too slow.

## 24. Migration strategy

Not executed.

1. Keep every current resolver on `dev-2`.
2. Add repository methods for the entity reads in section 20, behind the existing unused read-only connection. Do not register them in GraphQL yet.
3. Compare one crime id that exists in both databases: header fields, accused count, arrest count, chargesheet modules.
4. Compare a V1 crime that is absent from `dev-2`, including one with null `ps_code`, and confirm the row is returned.
5. Compare an accused with null `person_id` and an arrest with null `accused_id`.
6. Only after that, switch one field (`fir` by id is the smallest) behind a flag. Default remains `dev-2`.
7. Leave drug, disposal class, FSL, files, IR text, and dedup on `dev-2` for the whole of this sequence.

Rollback is the flag, or unsetting the read connection. `dev-2` is untouched, so no restore is required.

## 25. Open questions

- Should a future seizure screen mean `brief_facts_drug` or `seizures_unified`? They are different populations. This mapping does not choose.
- `overviewStatistics` shares the FIR service. Its exact aggregate list was not re-copied line by line here. It uses the same `firs_mv` path as the other FIR stats.
- Who loads the 7,403-crime subset into `dev-2` is still outside this repository.

## 26. Final recommendation

Query `dopams_cctns` for crime, accused, person, arrest, hierarchy, and the three chargesheet modules. Shape the GraphQL response in the service. Keep `DATABASE_URL` on `dev-2` for users, files, dedup, brief facts, disposal classification, operational FSL, and IR text.

Do not create compatibility views. Do not inner-join hierarchy or person. Do not treat a raw chargesheet id as unique. Do not replace `person_deduplication_tracker` with `identity_links`.

The next step is a review of this mapping. No GraphQL change follows from this document until it is accepted.

## Master table

| DOPAMS use case | GraphQL field | Current source | Data required | Unified table(s) | Relationship | Query change | Status |
|---|---|---|---|---|---|---|---|
| FIR detail | `fir` | `firs_mv`, `accused`, `crimes` | header, station, accused, JSON blobs | `crimes_unified`, `accused_unified`, `persons_unified`, hierarchy | crime left join station; accused left join person | NEW QUERY | PARTIALLY_SUPPORTED |
| FIR list | `firs` | `firs_mv` | page of FIR headers and filters | `crimes_unified`, hierarchy | left join station | NEW QUERY | PARTIALLY_SUPPORTED |
| FIR statistics | `firStatistics` | `firs_mv` | status plus disposal waterfall | `crimes_unified`; disposal type absent | crime row | NEW QUERY for status only | PARTIALLY_SUPPORTED |
| Overview statistics | `overviewStatistics` | `firs_mv` | FIR aggregate | `crimes_unified` | none | NEW QUERY | PARTIALLY_SUPPORTED |
| FIR filters | `firFilterValues` | `firs_mv` | distinct station, unit, status, drug | crimes, hierarchy; drug is `brief_facts_drug` | left join station | NEW QUERY | PARTIALLY_SUPPORTED |
| UI/PT statistics | `uiptCasesStatistics` | `firs_mv` | status, year, drug | crimes; drug on `dev-2` | none | NEW QUERY | PARTIALLY_SUPPORTED |
| FIR abstract | `firsAbstract` | `firs_mv` | export of the FIR list | crimes, hierarchy | left join station | NEW QUERY | PARTIALLY_SUPPORTED |
| Seizure statistics | `seizureStatistics` | `brief_facts_drug`, `firs_mv` | drug NLP totals | none | n/a | NO CHANGE | DEV2_ONLY |
| Seizure filters | `seizuresFilterValues` | same | drug filter values | none | n/a | NO CHANGE | DEV2_ONLY |
| Seizure abstract | `seizuresAbstract` | same | seizure export | none | n/a | NO CHANGE | DEV2_ONLY |
| Accused detail | `accused` | `accuseds_mv` | accused, person, crime, station | accused, persons, crimes, hierarchy | left join person and station | NEW QUERY | PARTIALLY_SUPPORTED |
| Accused list | `accuseds` | `accuseds_mv` | page of accused | same | same | NEW QUERY | PARTIALLY_SUPPORTED |
| Accused statistics | `accusedStatistics` | `accuseds_mv` | status, domicile, type | accused status only | left join person | NEW QUERY | PARTIALLY_SUPPORTED |
| Accused filters | `accusedFilterValues` | `accuseds_mv` | distinct names, units, address | persons text, hierarchy | left joins | NEW QUERY | PARTIALLY_SUPPORTED |
| Accused abstract | `accusedAbstract` | `accuseds_mv` | accused export | same as list | left joins | NEW QUERY | PARTIALLY_SUPPORTED |
| Advanced search | `advancedSearch` | advanced-search MVs | mixed crime, person, drug, address | crimes, accused, persons, hierarchy | left joins | NEW QUERY | PARTIALLY_SUPPORTED |
| Autocomplete | `fieldAutoComplete` | same views | distinct values | same, field by field | left joins | NEW QUERY | PARTIALLY_SUPPORTED |
| Profile detail | `criminalProfile` | `criminal_profiles_mv`, chargesheet children, IR | person, crimes, IR, files | persons, accused, crimes | accused by `person_id` | NEW QUERY | PARTIALLY_SUPPORTED |
| Profile list | `criminalProfiles` | `criminal_profiles_mv` | profile cards | persons, accused | same | NEW QUERY | PARTIALLY_SUPPORTED |
| Accused case history | `accusedCaseHistory` | `accuseds_mv` | cases for an accused | accused, crimes | `crime_id` | NEW QUERY | PARTIALLY_SUPPORTED |
| Person case history | `personCaseHistory` | Prisma accused, dedup tracker | cases across a cluster | accused, crimes; cluster on `dev-2` | `person_id` | NEW QUERY | PARTIALLY_SUPPORTED |
| Person search | `searchPersonsByName` | profile services | name match | `persons_unified.full_name` | none | NEW REPOSITORY METHOD | PARTIALLY_SUPPORTED |
| Criminal network | `criminalNetworkDetails` | accused, hierarchy, dedup | co-accused edges and clusters | accused, crimes, hierarchy | by `crime_id` | NEW QUERY | PARTIALLY_SUPPORTED |
| Overall crime stats | `overallCrimeStats` | `firs_mv`, accused, drug, disposal | mixed totals | crimes, accused | left joins | NEW QUERY | PARTIALLY_SUPPORTED |
| Drug form | `seizuresByDrugForm` | `brief_facts_drug` | drug form totals | none | n/a | NO CHANGE | DEV2_ONLY |
| Case status chart | `caseStatusClassification` | disposal JSON on `firs_mv` | disposal type | type not stored | n/a | NOT CURRENTLY SUPPORTED | NOT_SUPPORTED |
| Regional overview | `regionalOverview` | crimes, hierarchy, drug | station totals and drugs | crimes, hierarchy | left join station | NEW QUERY | PARTIALLY_SUPPORTED |
| Drug data | `drugData` | `brief_facts_drug` | named drug totals | none | n/a | NO CHANGE | DEV2_ONLY |
| Drug list | `drugList` | `brief_facts_drug` | distinct drug names | none | n/a | NO CHANGE | DEV2_ONLY |
| Drug cases | `drugCases` | `brief_facts_drug` | cases per drug | none | n/a | NO CHANGE | DEV2_ONLY |
| Case class chart | `caseClassificationUI` | `firs_mv.caseClassification` | class | V2 JSON only | none | NEW QUERY | PARTIALLY_SUPPORTED |
| Trial classification | `trialCasesClassification` | disposal JSON | disposal type | type not stored | n/a | NOT CURRENTLY SUPPORTED | NOT_SUPPORTED |
| Accused type chart | `accusedTypeClassification` | `accuseds_mv` | role / type | not mapped | n/a | NOT CURRENTLY SUPPORTED | NOT_SUPPORTED |
| Domicile chart | `domicileClassification` | `accuseds_mv` | domicile | not mapped | n/a | NOT CURRENTLY SUPPORTED | NOT_SUPPORTED |
| Stipulated time | `stipulatedTimeClassification` | FIR date and class | overdue UI cases | `fir_date`; class partial | none | NEW QUERY | PARTIALLY_SUPPORTED |
| Investigation info | `investigationRelatedInfo` | crimes, accused, arrests, FSL, chargesheets, disposal | mixed operational counts | crimes, arrests, chargesheets | by `crime_id` | NEW QUERY | PARTIALLY_SUPPORTED |
| Court info | `courtRelatedInfo` | `case_status`, `chargesheet_accused` | trial status and NBW | `case_status`; NBW on `dev-2` | none | NEW QUERY | PARTIALLY_SUPPORTED |

Tally of the 37 fields: ready with no query change 0, ready with a query change and no missing input 0, partially supported 26, `dev-2` only 7, not supported 4.

## Adversarial review

Data. Crime, accused, person, arrest, and chargesheet columns used above are the current-state columns in `001_initial_schema.sql` and `field_maps.py`, checked against live counts. Property, disposal, FSL, and interrogation current-state tables do not carry the descriptive columns the screens use. Source payloads were not proposed as the application read path.

Relationships. Listing a crime does not join accused. Listing an accused left-joins person. Listing an arrest left-joins accused. Station is a left join. V1 seizure grain is the dossier row, not the collapsed accused, so those seizures must not be joined as if `accused_id` matched.

IDs. `crime_id` is unique across V1 and V2. Accused id is not person id. Chargesheet id includes source and module. Raw id `1` is two records.

Application. CamelCase and `fullName` are service mappings. `persons_unified.full_name` already exists, so the service does not concatenate a name and the database does not gain a `fullName` column. Files, dedup, brief facts, and users stay on `dev-2`.

Coverage. A left join from `crimes_unified` returns the 7,305 V1 crimes and the 9,583 V2 crimes, including 3,723 null station codes. Accused queries return the 695 null-person rows. The 7 `dev-2`-only fields and the 4 unsupported classifications stay on `dev-2`, so those screens do not go empty.

Performance. The design uses the existing foreign-key indexes and a normal `ORDER BY fir_date`. It does not add a materialized view. A `fir_date` index is a later measurement, not part of this phase.

Second pass. No join in the proposed reads drops a parent for a missing child. No write is proposed. `DATABASE_URL` is unchanged. Rollback does not need a restore.
