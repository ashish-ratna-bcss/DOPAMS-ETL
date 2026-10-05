# DOPAMS `dev-2` CCTNS architecture audit

## 1. Executive summary

The running DOPAMS backend reads CCTNS through Prisma on `DATABASE_URL`. That URL selects database `dev-2`, schema `public`, port 5432. The host is omitted here. This investigation opened that database in a read-only session and confirmed `current_database()` is `dev-2`.

`dev-2` is not `cctns-v2` and it is not `dopams_cctns`. It is the application database. It stores V2-shaped CCTNS tables, application tables, and five materialized views. List and search screens read those views. Detail and dashboard code also reads the base tables.

Every one of the 7,403 `dev-2` crime ids is present in `dopams_cctns` as a V2 crime. The unified database also has 2,180 further V2 crimes and 7,305 V1 crimes that `dev-2` does not contain. `dev-2.crimes.ps_code` is `NOT NULL`, and zero rows are null. The unified model has 3,723 V1 crimes with a null `ps_code`.

The current views inner-join `hierarchy` on `ps_code`. Pointing that SQL at the unified tables would hide those 3,723 crimes. The existing `be_read` views are not a substitute for `firs_mv`: they do not expose the camelCase columns, drug JSON, documents, or brief-facts aggregates the GraphQL types select.

No application file, environment file, `dev-2` object, or `dopams_cctns` object was changed.

## 2. Current architecture

```text
DOPAMS backend
  Apollo GraphQL on Express
  Prisma 6  +  pg
        |
        | DATABASE_URL   (not read-only)
        v
     dev-2.public
        |
        +-- materialized views: firs_mv, accuseds_mv,
        |   advanced_search_firs_mv, advanced_search_accuseds_mv,
        |   criminal_profiles_mv
        |
        +-- V2-shaped tables: crimes, accused, persons, hierarchy,
        |   arrests, chargesheets, charge_sheet_updates,
        |   fsl_case_property, brief_facts_*, disposal, files,
        |   interrogation children
        |
        +-- application tables: "user", person_deduplication_tracker,
            dedup_*, files
```

Repository: `D:\Dopams\DOPAMS-BE`. Package name `toystack-boilerplate`. GraphQL roots are composed in `src/schema/query.ts`. There is no REST CCTNS API in that composition. Prisma datasource is `env("DATABASE_URL")` with `schema` in the URL query string, which Prisma uses as `public`. The Node `pg` pool used by Prisma does not set `default_transaction_read_only`.

`UNIFIED_CCTNS_DATABASE_URL` exists only as an unused opt-in in `.env-sample` and `src/datasources/unifiedCctns.ts`. No GraphQL resolver imports it.

`cctns-v2` remains the ETL source (9,583 crimes). `dev-2` is a smaller operational copy (7,403 crimes). All 7,403 ids match `crimes_unified` where `source_system = 'V2'`.

## 3. `dev-2` inventory

Session: `transaction_read_only = on`. Schema `public`.

| Object | Type | Rows | Role |
|---|---|---:|---|
| `firs_mv` | materialized view | 7,403 | FIR list, detail, home, seizures |
| `accuseds_mv` | materialized view | 25,398 | Accused list, detail, home, profiles |
| `advanced_search_firs_mv` | materialized view | same crime population as the definition | Advanced FIR search |
| `advanced_search_accuseds_mv` | materialized view | accused search population | Advanced accused/person search |
| `criminal_profiles_mv` | materialized view | profile list | Criminal profile screens |
| `crimes` | table | 7,403 | Prisma crime model. `ps_code` NOT NULL. Nulls: 0 |
| `accused` | table | 25,398 | `person_id` nullable. Nulls: 72 |
| `persons` | table | 25,327 | Person master |
| `hierarchy` | table | 815 | Station names. Required by the views |
| `arrests` | table | 25,386 | `id` uuid. `person_id` null on 96 rows |
| `chargesheets` | table | 5,470 | `id` uuid. `charge_sheet_id` varchar is null on the sampled population |
| `charge_sheet_updates` | table | 4,858 | `id` integer. Business key `update_charge_sheet_id` varchar |
| `fsl_case_property` | table | 2,056 | Used by FIR JSON and a home count |
| `brief_facts_accused`, `brief_facts_drug` | tables | — | Drug and accused narrative embedded in the views |
| `user`, `files`, `person_deduplication_tracker`, `dedup_*` | tables | — | Application-owned. Not CCTNS source data |

Other views present and not the CCTNS list contract: `advanced_search_firs`, `files_summary`, `ir_child_table_coverage`, `ir_field_persistence_check`, `person_deduplication_summary`, `update_chargesheets`.

Sizes: `firs_mv` 68 MB, `accuseds_mv` 135 MB, `advanced_search_accuseds_mv` 133 MB, `advanced_search_firs_mv` 31 MB, `criminal_profiles_mv` 26 MB.

Indexes used by the application path include `idx_firs_mv_id`, `idx_firs_mv_crime_reg_date`, `idx_firs_mv_case_status`, `idx_firs_mv_case_classification`, `firs_search_idx`, `idx_accuseds_mv_accused_id`, `idx_accuseds_mv_crime_id`, `idx_accuseds_mv_person_id`.

## 4. Materialized view definitions

Definitions were read with `pg_get_viewdef`. They were not inferred from the application. The full `firs_mv` text is about 25,000 characters. The shape that matters:

### `firs_mv` (42 columns)

Outer query:

```sql
FROM crimes c
JOIN hierarchy h ON h.ps_code::text = c.ps_code::text
```

That join is an inner join. On `dev-2` it drops nothing, because `crimes.ps_code` is NOT NULL and every crime has a hierarchy row (`firs_mv` count equals `crimes` count).

`id` is `crimes.crime_id` (`varchar(50)`), not a new uuid. Other columns are camelCase projections: `unit`, `ps`, `year`, `firNumber`, `firRegNum`, `crimeRegDate`, `caseClassification`, `caseStatus`, plus JSON aggregates.

Correlated JSON pulls `brief_facts_accused`, `brief_facts_drug`, `properties`, `mo_seizures`, `disposal`, `chargesheets`, `charge_sheet_updates`, `fsl_case_property`, `files`, and interrogation-report children. Persons inside those JSON blobs are left-joined or scalar subqueries. Chargesheet file rows join `chargesheets` on `chargesheets.id` (uuid).

### `accuseds_mv` (88 columns)

```sql
FROM accused a
LEFT JOIN brief_facts_accused bfa ON bfa.accused_id::text = a.accused_id::text
JOIN crimes c ON a.crime_id::text = c.crime_id::text
JOIN hierarchy h ON c.ps_code::text = h.ps_code::text
LEFT JOIN persons p ON a.person_id::text = p.person_id::text
```

`id` is `accused.accused_id`. `crimeId` and `personId` are the source varchar ids. Person and brief facts are optional. Hierarchy is not. Null `personId` rows remain: 72 in the table and 72 in the view.

Name, surname, address, and domicile columns come from `persons` and brief facts. They are not on the unified person table.

### `advanced_search_firs_mv` and `advanced_search_accuseds_mv`

Same hierarchy inner join. Accused search left-joins persons and brief facts. Drug names are aggregated from `brief_facts_drug`.

### `criminal_profiles_mv`

Built from persons and accused, with crimes joined from accused and drug rows from `brief_facts_drug`. Profile search is a separate path from `firs_mv`. It also uses `person_deduplication_tracker` in the service layer, which is an application table, not ETL `identity_links`.

## 5. Application query inventory

Framework path: GraphQL field → service function → `prisma.$queryRawUnsafe` or `prisma.<model>`. Pagination is page number plus limit. `src/utils/pagination.ts` caps browsing at 100 and treats a negative limit as an export cap of 10,000. Sort is a caller-selected column name, default `crimeRegDate` descending for FIRs and accused. Offset is `(page - 1) * limit`. There is no keyset cursor.

| API | Resolver | DB object | Sort / page |
|---|---|---|---|
| `fir`, `firs`, `firStatistics`, `firFilterValues`, `firsAbstract`, `overviewStatistics`, `uiptCasesStatistics` | `src/schema/firs/services/index.ts` | `firs_mv`; detail also `accused` and `crimes.additional_json_data` | page, limit, `sortKey` |
| `seizureStatistics`, `seizuresFilterValues`, `seizuresAbstract` | `src/schema/firs/services/seizures.ts` | `firs_mv` and `brief_facts_drug` | filters |
| `accused`, `accuseds`, `accusedStatistics`, `accusedFilterValues`, `accusedAbstract` | `src/schema/accused/services/index.ts` | `accuseds_mv` | page, limit, `sortKey` |
| `advancedSearch`, `fieldAutoComplete` | `src/schema/advanced-search/services/index.ts` | `advanced_search_firs_mv` or `advanced_search_accuseds_mv` | page, limit, `sortKey` |
| `criminalProfile`, `criminalProfiles`, `accusedCaseHistory`, `personCaseHistory`, `searchPersonsByName` | `src/schema/criminal-profile/services/` | `criminal_profiles_mv`, `accuseds_mv`, `person_deduplication_tracker`, `prisma.accused` | page, limit |
| `criminalNetworkDetails` | `src/schema/persons/services/index.ts` | `prisma.accused` include crime.hierarchy, dedup tracker | by `personId` |
| 14 home fields, including `overallCrimeStats`, `courtRelatedInfo`, `drugData`, `domicileClassification` | `src/schema/home/services/index.ts` | `firs_mv`, `accuseds_mv`, and Prisma counts on `crimes`, `arrests`, `chargesheet`, `fslCaseProperty` | date range `from`/`to` |
| `user`, `users` | `src/schema/user` | `user` | not CCTNS |

That is 37 CCTNS-facing GraphQL fields plus 2 user fields. FIR detail loads `SELECT * FROM firs_mv WHERE id = $1`, then recounts `accused` and reads `crimes.additional_json_data` for place, occurrence time, and court name. Court is not a first-class list API. It is JSON on the FIR and `courtRelatedInfo` on the home dashboard. Chargesheet updates travel inside `firs_mv.chargesheetUpdates`. FSL travels inside `firs_mv.casePropertyDetails` and `prisma.fslCaseProperty.count`.

## 6. Application contract

What a screen receives from `firs_mv`:

- `id`: V2 `crime_id` string, for example `629f70c6699c333d21fbaa5d`
- station display: `ps`, `unit` (hierarchy names, not only the code)
- `firRegNum`, `firNumber`, `crimeRegDate`, `year`
- `caseStatus`, `caseClassification`, heads, IO, brief facts
- arrays and JSON: `drugType`, `drugWithQuantity`, `accusedDetails`, `propertyDetails`, `moSeizuresDetails`, `disposalDetails`, `chargesheets`, `chargesheetUpdates`, `casePropertyDetails`, document JSON, `irDetails`

What a screen receives from `accuseds_mv`:

- `id`: V2 `accused_id` string
- `crimeId`, `personId` (nullable)
- `fullName`, `name`, `surname`, status, domicile, address, gender, drug JSON
- `ps`, `unit`, `year`, case classification

Chargesheet identity in this database:

- `chargesheets.id` is uuid
- `chargesheets.charge_sheet_id` exists but the non-null sample was empty
- `charge_sheet_updates.id` is a local integer. The value `1` is not present. A sample business key is `update_charge_sheet_id = 62e524df7332be53552fa810` with integer `id` 13564
- The app does not use the raw CCTNS integer as the update primary key

Arrest identity is a uuid, not the unified arrest key.

## 7. Hidden assumptions

| Assumption | Where | `dev-2` today | Unified impact | Handling |
|---|---|---|---|---|
| Every crime has a hierarchy row | `JOIN hierarchy` in all four list views | True. 0 null `ps_code` | 3,723 V1 crimes have null `ps_code` and would disappear | Left join, or keep the crime with null station fields |
| Every crime is a V2 crime | ids are 24-char hex; no V1 `fir_reg_num`-as-id population | 7,403 V2 ids, all found in unified V2 | 7,305 V1 crimes are invisible to this app | A read layer must label `source_system` and not require hex ids |
| `dev-2` is the full V2 extract | row counts | 7,403 crimes vs 9,583 in `cctns-v2` / unified V2 | 2,180 V2 crimes are already in unified and absent here | Do not treat `dev-2` counts as the unified target counts |
| Accused can lack a person | `LEFT JOIN persons` | 72 nulls kept in `accuseds_mv` | 695 nulls must stay visible | Keep the left join |
| Accused name lives on the person / brief facts | `accuseds_mv.fullName` | Populated from those joins | Unified accused has no name column. Unlinked accused have no person | Do not invent names. Return null |
| One chargesheet id space is a uuid, and updates are a separate integer | Prisma models and live types | Separate tables | Unified stores court, chargesheet, and updates in one table with `{source}:{module}:{raw_id}` | Do not merge those three into `chargesheets.id` |
| Raw id `1` is globally unique | not true of the unified table; not how `dev-2` keys updates | `charge_sheet_updates.id = 1` does not exist | `V1:court:1` and `V2:charge_sheet_updates:1` are different crimes | Lookups must include source and module |
| FSL rows exist as `fsl_case_property` | home count and FIR JSON | 2,056 rows | Unified FSL is 2,006 historical rows and is not the rich case-property shape | FSL stays required for the current screens and is not filled by the new merge |
| Dedup tracker is identity | criminal profile services | `person_deduplication_tracker` | `identity_links` are 1,131 candidates, 0 confirmed | Do not substitute one for the other |
| Drug, documents, IR children, files are part of a FIR | `firs_mv` JSON | Present on `dev-2` | Not in the unified current-state tables | Those fields stay on `dev-2` until a separate owner is chosen |
| Pagination is offset on a chosen column | GraphQL args | Stable for a static materialized view | Unified text ids sort differently from `crimeRegDate` | Keep the same sort keys if the column still exists |

## 8. Unified compatibility

| Requirement | `dev-2` | Unified | Compatible? | Gap |
|---|---|---|---|---|
| FIR list row | `firs_mv.id` = crime id | `be_read.crime.crime_id` | Partial | Missing camelCase JSON columns. Inner join must not be copied |
| FIR count | 7,403 | 16,888 (7,305 V1 + 9,583 V2) | No, by design | App DB is a V2 subset |
| Station required | `ps_code` NOT NULL, inner join | 3,723 null | No | Left join required |
| Accused list | `accuseds_mv`, left join person | `be_read.accused` | Partial | No surname, domicile, drug, brief facts |
| Null person | 72 kept | 695 kept in `be_read` | Yes, if the left join is kept | |
| Person physical / address split | `persons` wide table | thin `persons_unified` | No | Do not invent columns |
| Arrest id | uuid | source arrest key, `accused_id` nullable | No | Different identifier |
| Chargesheet id | uuid | namespaced text | No | Different identifier and three modules in one table |
| Update id | local integer + hex business key | `V2:charge_sheet_updates:{source id}` | No | Do not equate the integer with the source id |
| Court | JSON and dashboard, not a court table | V1 `source_module = court` | No current list | New to the app |
| FSL | `fsl_case_property` 2,056 | `fsl_unified` 2,006 historical | No | Rich FSL is not merged |
| Identity | dedup tracker | candidate links only | No | Different table and meaning |
| Users, files, IR, dedup | on `dev-2` | absent | No | Must stay on the application database |

Sample checked read-only: crime `629f70c6699c333d21fbaa5d` is in both `dev-2` and unified V2, and its unified `ps_code` is not null. Raw chargesheet id `1` is still two unified rows, `V1:court:1` on crime `2011083180180` and `V2:charge_sheet_updates:1` on crime `62a0b38fe32fb443129faa96`.

## 9. Data-model differences

- The app has only V2-shaped rows. The unified database adds V1 as a peer source, with no rule that V2 wins.
- V1 accused is a logical collapse (34,390 source rows, 17,356 current rows) and is not the same object as `dev-2.accused`.
- Null station, null person, and null arrest-accused links are real in the unified data and must remain visible.
- Police-station display for V2 crimes in the unified tables is on hierarchy, not on `crimes_unified.ps_name`.
- Chargesheet identity is namespaced. The app's uuid and local integer are a different key space.
- FSL in the app is operational and richer than `fsl_unified`.

## 10. Integration options

### Option A — rebuild the materialized views inside `dev-2` over a foreign table

Application impact: low if column names match. Database impact: changes `dev-2`. Migration risk: high, because refresh and foreign tables touch the database the app uses now. Rollback: restore the previous view definitions. Performance: unknown until refresh cost is measured. V1 support: only if the foreign source includes V1. Security: the app role would still be the writer of `dev-2`.

### Option B — leave `DATABASE_URL` on `dev-2`, add a CCTNS read connection

Application impact: new read path, current path unchanged. Database impact: none on `dev-2`. Rollback: unset the read URL or the feature flag. Performance: depends on the new view. Completeness: V1 and the full V2 set become available only on the new path. Operational complexity: two connections. Security: the read connection can be read-only.

### Option C — application-facing views on `dopams_cctns` that copy the `firs_mv` contract

Application impact: still requires the app to call them. Database impact: views only, if built later. Risk: a literal copy of the inner join drops null-station crimes, and drug/document JSON cannot be filled from unified tables. Rollback: drop the new schema. V1 support: yes only with a left join and an explicit source column.

### Option D — keep CCTNS screens on `dev-2` and add unified reads only for V1 gaps

Application impact: smallest. Data completeness: the app would keep showing 7,403 crimes and would not see the rest of V2 or any V1 unless a screen is migrated. This avoids a false "cutover" but does not meet the unified-read goal by itself.

## 11. Recommended architecture

Use Option B together with a narrowed Option C. Do not retarget `DATABASE_URL`.

```text
DOPAMS
  |
  +-- DATABASE_URL --> dev-2
  |                     users, files, dedup, IR, brief facts,
  |                     current materialized views
  |
  +-- CCTNS read URL (not wired yet) --> dopams_cctns
                                        read-only session
                                        application-facing views
                                        LEFT JOIN hierarchy
                                        source_system on every row
                                        separate court / chargesheet / update shapes
                                          |
                                          +-- V1 and V2 unified tables
```

`be_read` as it exists is the safety contract (nulls preserved, namespaced chargesheet ids, writes rejected). It is not yet the application contract. The application contract still needs the camelCase columns the resolvers select, and it must not inner-join hierarchy.

Do not build those views in this phase.

## 12. Migration strategy

The current path stays in place.

1. Observation. This document.
2. On `dopams_cctns` only, and only when authorized, add read-only views whose column names match what GraphQL selects, with a left join for hierarchy and a `source_system` column. Do not copy drug, file, or IR JSON from tables the unified model does not have.
3. Run the same SQL text against `dev-2` and against the new views for the 7,403 shared crime ids.
4. Classify each difference: expected V1 addition, expected fuller V2 set, missing app-owned JSON, or bug.
5. Fix the view, not the unified tables, unless a view bug hides a real unified row.
6. Put one GraphQL field behind a flag that reads the new connection. Default remains `dev-2`.
7. Extend field by field.
8. Cut over a field only after its differential check passes. `DATABASE_URL` stays on `dev-2` for application tables.

None of stages 2–8 are authorized by this document.

## 13. Rollback

Rollback of a future read path is: turn the flag off, or unset the CCTNS read URL, and restart only if the process cached the pool. `dev-2` and its views stay as they are. No restore of V1, V2, or `dopams_cctns` is required. Do not drop `dev-2` views as part of introducing the new path.

## 14. Open questions

- Which job loads `cctns-v2` into `dev-2`, and why 2,180 V2 crimes are absent. The application repository does not show that load.
- Whether `chargesheets.charge_sheet_id` is unused on purpose. The column exists and the non-null probe returned no rows.
- The home dashboard's `courtRelatedInfo` SQL was not copied line by line. It is a Prisma/service aggregate, not a court table.

## 15. Next phase

Write the column-level mapping from each GraphQL-selected `firs_mv` and `accuseds_mv` column to a unified source, marking each column as available, null, or still owned by `dev-2`. Do not create the views and do not change the application until that mapping is accepted.

## Adversarial review of this design

- V1 crimes are not in the current views. A future view that selects only hex V2 ids, or inner-joins hierarchy, would drop them. The recommendation forbids both.
- Null `ps_code` crimes are invisible to today's inner join. The future view must left-join.
- Null-person accused survive today because persons are left-joined. That must be preserved. Unified nulls are 695, not 72.
- Putting court and updates into `chargesheets.id` would collapse two key spaces. The recommendation keeps them separate from the uuid.
- Offset pagination on `crimeRegDate` changes if the column or null dates change. Unified `fir_date` has no nulls in the last validation, but sort stability still has to be checked per flag.
- This design does not change current behavior, because nothing is wired.
- The read connection is specified read-only. `DATABASE_URL` is not moved, so the app can still write users and files on `dev-2`.
- Rollback does not require a database restore.

## Performance notes

`dev-2`, read-only:

| Query | Plan | Time |
|---|---|---|
| `firs_mv` by `id` | Index scan `idx_firs_mv_id` | 0.042 ms |
| `count(*)` on `firs_mv` | Index-only scan | 0.728 ms |
| `accuseds_mv` by exact `fullName` | Seq scan | 47.547 ms |

Unified contract queries already measured on `dopams_cctns` are in the same band for key lookups (crime by id about 0.02 ms, accused by crime about 0.1 ms). A future view that embeds the `firs_mv` JSON aggregates would not automatically match 0.042 ms, because that cost is paid when the materialized view is refreshed, not when it is read. That is a reason to keep heavy JSON on `dev-2` until a refresh design exists.

## Security notes

The application role on `dev-2` is the Prisma role. This audit did not grant or revoke anything. The Prisma client is not read-only, and the app writes user and file rows there. CCTNS source databases were not given to this session. A future CCTNS connection should set `default_transaction_read_only` and should not be the same URL as `DATABASE_URL`. No privilege change was made.
