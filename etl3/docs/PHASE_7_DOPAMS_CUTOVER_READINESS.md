# Phase 7 — DOPAMS cutover readiness

## Executive status

BLOCKED

The `be_read` contract on `dopams_cctns` matches the unified tables. The existing DOPAMS GraphQL application still reads `public.firs_mv` and `public.accuseds_mv` through `DATABASE_URL`. That SQL was not pointed at `dopams_cctns`. Production cutover was not performed.

```text
CCTNS V1 ── read only ──┐
                        ├──> ETL-3 ──> dopams_cctns ──> be_read ──> optional backend reader
CCTNS V2 ── read only ──┘                                      │
                                                               └── existing GraphQL still uses DATABASE_URL
```

ETL baseline: `27e67ad`. First integration commit: `84a7d89`. Backend reader commit: `040625d`. This pass re-checked the live database and the code. It did not repeat incremental runs `9a984c7a-3874-481b-8fee-4f7ea347f371` or `d64bc33b-60b0-4052-b70d-605a2121516d`.

## Implementation summary

Migration `006_backend_read_contract.sql` is applied on `dopams_cctns`. It adds schema `be_read` and does not alter unified tables or V1/V2.

The views are `hierarchy`, `crime`, `person`, `accused`, `arrest`, `chargesheet`, `fsl_historical`, and `identity_candidate`. Each has one `INSTEAD OF INSERT OR UPDATE OR DELETE` trigger. A write raises `be_read is read-only; ETL-3 owns source-derived CCTNS rows`.

`D:\Dopams\DOPAMS-BE\src\datasources\unifiedCctns.ts` is an opt-in reader. It runs only when `UNIFIED_CCTNS_DATABASE_URL` is set, forces a read-only session, and is not imported by the GraphQL resolvers. `listChargesheetsByRawId` returns every row for a raw id. It does not use `LIMIT 1`. `listArrestsForCrime` keeps arrests whose `accused_id` is NULL.

## Compatibility matrix

| Capability | Backend assumption | Unified implementation | Result |
|---|---|---|---|
| Crime / FIR | `firs_mv` inner-joins hierarchy. `ps_code` is required in Prisma. | `be_read.crime` left-joins hierarchy. 16,888 rows, same as `crimes_unified`. NULL `ps_code`: 3,723. | BLOCKED for the existing SQL. PASS on `be_read`. |
| Accused | `accuseds_mv` starts at `brief_facts_accused` and carries domicile, drug, and physical columns. | `be_read.accused` is `accused_unified` left-joined to persons. 50,348 rows. V1 17,356 logical rows. V2 32,992. | BLOCKED. Those extra columns were not invented. |
| Person | `persons` plus `person_deduplication_tracker`. | `be_read.person` is `persons_unified` only. V1 20,127. V2 32,914. Total 53,041. | ADAPT. The tracker is not `identity_links`. |
| Arrest | `arrests.id` is a UUID. Crime is required. | `be_read.arrest` has 53,186 rows. NULL `accused_id`: 2,434, same as the table. | ADAPT. The UUID is not the unified arrest id. |
| Court | Court name is read from crime JSON. There is no court table in Prisma. | V1 court is `be_read.chargesheet` with `source_module = court`. 7,534 rows. | ADAPT. |
| Chargesheet | `chargesheets.id` is a UUID. | `V2:chargesheets:{raw id}`. 7,086 rows. | BLOCKED if the UUID is reused. PASS on the namespaced id. |
| Charge updates | `charge_sheet_updates.id` is a serial integer. | `V2:charge_sheet_updates:{raw id}`. 6,193 rows. | BLOCKED if the serial is reused. PASS on the namespaced id. |
| Police station | Hierarchy is required. | Every non-null `ps_code` matches `be_read.hierarchy` (816 codes). NULL stays visible. | ADAPT. |
| District | `hierarchy.dist_name`. | `COALESCE(hierarchy.dist_name, crime.unit_district)`. | ADAPT. No code was guessed. |
| FSL | `fsl_case_property` and media. | `be_read.fsl_historical` over 2,006 `fsl_unified` rows. `inclusion = historical_not_merged`. FSL is not in the merge list. | RETAIN the operational table for the current app. |
| Identity | Dedup tracker membership. | 1,131 `identity_links`, all `candidate`. Confirmed 0. Self-links 0. The candidate view has 1,131 rows and no other status. | PASS on `be_read`. Do not treat candidates as the tracker. |
| History | `date_created` / `date_modified` on V2 tables. | `change_log` 1,338,633. `business_change` 8,499. `initial_observation` 1,330,134. `current_as_of` is the ETL observation time. | RETAIN. Do not treat `current_as_of` as publication time. |
| Search | Filters on `firs_mv` / `accuseds_mv` columns. | No equivalent search view. The reader has no search function. | BLOCKED. |
| Pagination | Limit/offset on a caller-selected MV column. | `be_read.crime` ordered by `crime_id`. Pages of 50 at offsets 0, 50, and 100 are disjoint, and page 1 is stable. | PASS for that order on `be_read`. The MV pagination was not retargeted. |
| Reports | Home and FIR aggregates on `firs_mv`. | Not projected. | BLOCKED. |
| Export | FIR export reads `firs_mv`. | Not projected. | BLOCKED. |

## Identifier compatibility

Unified chargesheet identity is `{source_system}:{module}:{raw_id}`.

| Identifier | Type and scope | Null | Backend hazard |
|---|---|---|---|
| `crime_id` | Text primary key. V1 7,305 and V2 9,583. `fir_reg_num` overlap is 0. | No | Already text in Prisma. |
| accused id | V2 source id, or `V1:` plus the logical hash. Every V1 accused id has that prefix. Raw `source_record_id` overlap between V1 and V2 accused is 0. | No | Not equal to V1 `accused_id` and not equal to `person_id`. |
| person id | V2 person id, or V1 `person_code`. Counts add to 53,041, so the two sources did not collapse onto one primary key. | No on the person row. 69 V1 source rows have no key and are not in this table. | Two shared V1 codes are one person each. No person was invented. |
| arrest id | Source arrest key, text. | `accused_id` may be NULL. | Not the Prisma UUID. |
| chargesheet id | Namespaced text. Distinct ids equal 20,813 rows. | No | Raw id is not unique. |
| `ps_code` | V2 hierarchy code, or the exact V1 name match. | 3,723 crimes | NULL is an unresolved match, not a failed load. |

Live pair for raw id `1`:

```text
V1:court:1                    crime 2011083180180
V2:charge_sheet_updates:1     crime 62a0b38fe32fb443129faa96
```

The lookup uses `chargesheets_unified_module_record_key`. Execution time 1.411 ms. Two rows, two crimes.

## Data model compatibility

- Crime to accused is many. Accused to person is many-to-one. NULL `person_id` on accused: 695. No accused points at a missing person.
- Person to many accused is allowed. The view count of those persons equals the table count.
- Arrests with NULL `accused_id`: 2,434. An arrest id that is set always matches an accused row.
- Chargesheet to crime: 0 missing crimes.
- Accused to crime: 0 missing crimes.
- Duplicate primary keys: 0 for crime, person, accused, arrest, and chargesheet.
- Duplicate gap keys: 0. Duplicate identity pairs: 0. Duplicate change-log tuples `(entity, unified_id, field, observed_at, source_run_id, change_classification)`: 0.
- Open NULL relationships above are unresolved source links, not hidden orphans.

## API validation

Existing GraphQL (`firs`, `accused`, `persons`, home, criminal profile, advanced search) was not executed against `dopams_cctns`. Those queries name `firs_mv` and `accuseds_mv`, which are not in this database. Starting the server on `dopams_cctns` would be a cutover, so it was not done.

The contract SQL was executed and the returned rows were checked:

- Crime, accused, person, arrest, chargesheet, and FSL view counts equal the base tables, and each primary key is unique in the view. The hierarchy join does not duplicate crimes.
- NULL police-station crimes stay visible.
- NULL accused links stay visible.
- Raw chargesheet id `1` returns the two namespaced ids above, on different crimes.
- Identity candidates are not confirmed.
- FSL rows are the historical 2,006, marked `historical_not_merged`.
- Insert, update, and delete on the views raise `ETL-3 owns`. A read-only session cannot update `crimes_unified`.

DOPAMS-BE has no unit-test suite (`npm test` is a placeholder). No HTTP status was collected for the production API.

## Performance

| Query | Plan | Time |
|---|---|---|
| `count(*)` on `be_read.crime` | Index-only scan on `idx_crimes_unified_ps_code`, 16,888 rows | 2.438 ms |
| Accused by one `crime_id` | Index scan on `idx_accused_unified_crime` | 0.106 ms |
| Chargesheets by raw id `1` | Index scan on `chargesheets_unified_module_record_key`, 2 rows | 1.411 ms |

No index was added. `firs_mv` was not planned, because it is not on this database.

## Security

- ETL source sessions set `default_transaction_read_only` and assert the database name. This pass again rejected `CREATE TABLE` on V1 and on V2 with `ReadOnlySqlTransaction`.
- That protection is a session setting, not a separate `GRANT` role.
- The backend reader sets the same read-only session flag and `application_name = dopams_be_unified_reader`. It has no connection to V1 or V2.
- `DATABASE_URL` in `.env-sample` is empty. The live production value was not read, so this report does not name that host or database.
- Prisma remains on `DATABASE_URL`. It is not read-only. Pointing it at `dopams_cctns` would bypass `be_read`.

## ETL regression

Established runs, re-checked against the live tables in this pass:

```text
9a984c7a-3874-481b-8fee-4f7ea347f371
0 inserts

d64bc33b-60b0-4052-b70d-605a2121516d
0 unified inserts
0 unified updates

change_log
1,338,633
```

```text
V1 court = 7,534
V2 chargesheets = 7,086
V2 updates = 6,193
```

```text
V1 writes rejected
V2 writes rejected
```

Also still true on the live database: V1 fir 7,305, V2 crimes 9,583, `fsl_unified` 2,006, identity links 1,131, `bulk_event_exclusions` 0. Business changes whose `observed_at` is 2026-08-24: 42. `logged_at` on that date: 0. The bulk-exclusion table was not used to reinterpret those 42 rows.

`etl3/tests/test_phase7_backend_contract.py` passed after this pass. ETL consolidation code was not changed, so the incremental cycle was not run again.

## Known limitations

- V1 accused collapses 34,390 source rows to 17,356 logical rows.
- 69 V1 person rows have no usable key. Two V1 `person_code` values are shared.
- 3,723 V1 crimes have no exact police-station match. None were ambiguous. No code was guessed.
- Arrest and accused-person gaps stay open. They are visible as NULL foreign keys.
- Identity links are candidates only.
- FSL is historical. The rich `fsl_case_property` shape is not in the unified model.
- `bulk_event_exclusions` is empty. The 42 August rows stay `business_change`.
- Consolidation is not one transaction. A reader during a run can see a mid-run snapshot.
- Source protection is a read-only session, not a separate role.
- The existing GraphQL response shape is not implemented on `be_read`.
- Offset pagination on `crime_id` is stable for a static set. A concurrent insert can shift later pages. That is the same limit/offset behavior the current API uses.

These are source, ETL, or contract limits. They are not reasons to rewrite the merge.

## Blockers

1. Replacing `DATABASE_URL` with `dopams_cctns` makes the current FIR and accused queries fail or drop the 3,723 crimes that have no `ps_code`.
2. Users, files, IR54, brief facts, dedup, and drug NLP have no owner on the unified database.
3. The frontend contract expects materialized-view columns that were not added.
4. Prisma chargesheet ids are a UUID and a serial. The unified key is a namespaced string.
5. The database selected by production `DATABASE_URL` is unknown here.

## Cutover procedure

Do not run this until the blockers are closed.

1. Replace `firs_mv` and `accuseds_mv` with queries on `be_read`, or keep those views on the current application database.
2. Name an owner for users, files, brief facts, IR54, and dedup. Do not copy them into ETL current state by default.
3. Snapshot `dopams_cctns` and the database the backend uses today.
4. Deploy the backend build that reads CCTNS facts only through `UNIFIED_CCTNS_DATABASE_URL`. Leave `DATABASE_URL` on the application database.
5. Smoke-test one V1 crime, one V2 crime, a NULL `ps_code`, a NULL `person_id`, court `V1:court:1`, update `V2:charge_sheet_updates:1`, and one historical FSL row.
6. Watch 5xx, those row counts, and any write against `be_read` or `change_log`.

## Rollback procedure

The application path was not switched, so rollback of this phase is:

1. Unset `UNIFIED_CCTNS_DATABASE_URL`.
2. Leave `DATABASE_URL` unchanged.
3. Redeploy the previous backend build if the reader was deployed.

To remove the contract itself:

```text
DROP SCHEMA be_read CASCADE;
```

That statement was executed inside a transaction and rolled back. The eight views and 16,888 crime rows were present afterward. Do not drop unified tables. Do not change V1 or V2.

If a future cutover fails a smoke test, a CCTNS read writes a unified row, or `change_log` grows from the application: unset `UNIFIED_CCTNS_DATABASE_URL`, restore the previous build, and confirm `DATABASE_URL` still selects the pre-cutover database.

## Post-cutover monitoring

- HTTP 5xx on FIR, accused, person, arrest, and chargesheet reads.
- `be_read.crime` count against 16,888, NULL `ps_code` against 3,723, and chargesheet module counts against 7,534 / 7,086 / 6,193.
- `change_log` count. Application traffic must not increase it.
- Failed statements containing `be_read is read-only`.
- V1 `cctns.cctns_fir` staying at 7,305 and V2 `crimes` staying at 9,583.
- Latency of crime detail, accused-by-crime, and chargesheet-by-raw-id against the plans above.

## Gate results

| Gate | Result |
|---|---|
| Backend schema compatible with `be_read` | PASS |
| Direct Prisma / `firs_mv` schema | FAIL |
| IDs safe on `be_read` | PASS |
| Relationships correct on `be_read` | PASS |
| NULL handling safe on `be_read` | PASS |
| Existing APIs correct against `dopams_cctns` | FAIL |
| Pagination on `be_read.crime` | PASS |
| Search and filters on the existing API | FAIL |
| FSL behavior understood | PASS |
| History behavior correct | PASS |
| Provenance preserved by the views | PASS |
| Write ownership defined | PASS. ETL owns source-derived rows. |
| Performance of the contract queries | PASS |
| Security of the reader and source sessions | PASS. No separate role. |
| Production DB configuration verified | FAIL. `DATABASE_URL` was not read. |
| Shadow validation of the contract SQL | PASS |
| Differential old-vs-new API results | Not run. The old database target is unknown, and the shapes are not the same. |
| ETL regression evidence | PASS. Incremental was not repeated. |
| V1 mutation | 0 |
| V2 mutation | 0 |
| Integrity checks | PASS |
| Rollback procedure | PASS. The drop was rolled back in a transaction. |
| Documentation | PASS |
