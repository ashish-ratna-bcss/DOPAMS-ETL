# Phase 7 — DOPAMS cutover readiness

## Executive status

BLOCKED

The read contract on `dopams_cctns` is in place and was checked against live data. The existing DOPAMS GraphQL application cannot be pointed at that database. Production `DATABASE_URL` was not changed.

```text
CCTNS V1 ── read only ──┐
                        ├──> ETL-3 ──> dopams_cctns ──> be_read ──> optional backend reader
CCTNS V2 ── read only ──┘                                      │
                                                               └── existing GraphQL still uses DATABASE_URL
```

## What was inspected

DOPAMS backend: `D:\Dopams\DOPAMS-BE`, HEAD `589564c`. Prisma 6, Apollo GraphQL, Express, `pg`. Datasource `env("DATABASE_URL")`. The live value of `DATABASE_URL` was not read.

Primary list and search SQL uses materialized views `public.firs_mv` and `public.accuseds_mv`, plus `advanced_search_firs_mv` and `advanced_search_accuseds_mv`. Detail queries also read `accused`, `crimes.additional_json_data`, and `person_deduplication_tracker`.

Prisma maps the V2 operational tables (`crimes`, `accused`, `persons`, `hierarchy`, `arrests`, `chargesheets`, `charge_sheet_updates`, `fsl_case_property`) and DOPAMS-owned tables (`User`, `File`, IR54 children, dedup trackers, drug NLP). `Crimes.psCode` is required and the hierarchy relation is required. `Accused.person` is optional. `Chargesheet.id` is a UUID. `ChargeSheetUpdate.id` is a serial integer.

`firs_mv` inner-joins `hierarchy` on `ps_code`. `accuseds_mv` starts from `brief_facts_accused`. Those shapes are not the unified tables.

## Backend dependency map

| Feature | Current path | Current assumption | Unified target | Result |
|---|---|---|---|---|
| FIR list, detail, filters, home | `firs_mv` | `ps_code` always matches hierarchy; camelCase MV columns; drug arrays; brief-facts accused count | `be_read.crime` | BLOCKED for the existing SQL. The new view keeps NULL `ps_code`. |
| Accused list and filters | `accuseds_mv` | one row per accused from brief facts; physical description; domicile; drug type | `be_read.accused` | BLOCKED. Those columns were not invented. |
| Person and criminal profile | `persons` plus `person_deduplication_tracker` | tracker membership is the duplicate set | `persons_unified`; `identity_links` are candidates | ADAPT. Candidates are not the tracker. |
| Arrest | `arrests` UUID, optional `person_id` | crime is required | `be_read.arrest`; `accused_id` may be NULL | ADAPT |
| Court | court name taken from crime JSON | not a first-class backend table | V1 court rows live in `chargesheets_unified` with `source_module = court` | ADAPT |
| Chargesheet | `chargesheets` UUID; updates use a serial id | ids are not shared across modules | `{source_system}:{module}:{raw_id}` plus raw `source_record_id` | BLOCKED if the old id type is reused. PASS on `be_read.chargesheet`. |
| Police station | required `hierarchy.ps_code` | NULL station is invalid | 3,723 crimes have NULL `ps_code` | ADAPT. NULL is an unresolved match. |
| FSL | `fsl_case_property` and media | rich case-property row | `be_read.fsl_historical` over 2,006 historical rows | RETAIN the old table for the current app. Do not refill FSL from the source. |
| History | `date_created` / `date_modified` on V2 tables | source or app timestamps | `change_log`; `current_as_of` is the ETL observation time | RETAIN. Do not treat `current_as_of` as publication time. |

Classification of source dependencies:

- REMOVE: none. No replacement is serving the existing GraphQL contract yet.
- REPLACE: future FIR and accused reads, only after the response shape is agreed. Use `be_read`, not `firs_mv`.
- ADAPT: NULL `ps_code`, NULL `person_id`, namespaced chargesheet ids, candidate identity.
- RETAIN: users, files, IR54, `person_deduplication_tracker`, brief facts, drug NLP, and the operational FSL table. ETL-3 does not own them.
- UNKNOWN: which database production `DATABASE_URL` selects. The sample file leaves it empty.

## Schema compatibility

`dopams_cctns` does not contain `firs_mv`, `accuseds_mv`, `brief_facts_accused`, `User`, `files`, or the IR54 child tables. Pointing Prisma at `crimes_unified` would also fail: the table name differs, `ps_code` is nullable, and chargesheet primary keys are text namespaced ids rather than UUIDs or serials.

Migration `006_backend_read_contract.sql` is applied and recorded. It adds schema `be_read` only. It does not alter unified tables and does not touch V1 or V2.

| View | Source | Null / identity rule |
|---|---|---|
| `be_read.hierarchy` | latest V2 `hierarchy_source` payload per `ps_code` | names come from the payload; no guessed code |
| `be_read.crime` | `crimes_unified` LEFT JOIN hierarchy | NULL `ps_code` stays visible; `ps_code_unresolved` is true |
| `be_read.person` | `persons_unified` | no surname, split address, or domicile added |
| `be_read.accused` | `accused_unified` LEFT JOIN persons | `person_id` stays NULL when unresolved |
| `be_read.arrest` | `arrests_unified` | `accused_unresolved` when `accused_id` is NULL |
| `be_read.chargesheet` | `chargesheets_unified` | namespaced id, `source_module`, raw `source_record_id` |
| `be_read.fsl_historical` | `fsl_unified` | `inclusion = historical_not_merged` |
| `be_read.identity_candidate` | `identity_links` where status is `candidate` | confirmed links are not selected |

Each view has an `INSTEAD OF INSERT OR UPDATE OR DELETE` trigger. The trigger raises `be_read is read-only; ETL-3 owns source-derived CCTNS rows`.

## Identifier compatibility

| Identifier | Unified meaning | Backend hazard |
|---|---|---|
| `crime_id` | source crime / FIR key, text | already text in Prisma; safe to pass through |
| `fir_reg_num` | business number; V1 and V2 overlap is 0 | unique in Prisma; still unique across the union |
| accused id | V2 source id, or V1 logical `V1:` + md5 of FIR, name, father, mobile or DOB | not equal to V1 `accused_id` or to `person_id` |
| person id | V2 `person_id`, or V1 `person_code` | 69 V1 rows have no key and are not invented; 2 shared V1 codes |
| arrest id | source arrest key | Prisma `arrests.id` is a UUID; do not equate the two |
| chargesheet id | `V1:court:{id}`, `V2:chargesheets:{id}`, `V2:charge_sheet_updates:{id}` | raw ids collide: all 6,193 update ids also exist as V1 court ids. Stored ids do not collide |
| `ps_code` | V2 hierarchy code, or the exact V1 name match | NULL for 3,723 V1 crimes |

## Relationship compatibility

Live unified counts, also visible through the views:

- Crimes: 16,888 (V1 7,305 + V2 9,583). NULL `ps_code`: 3,723. The crime view count equals `crimes_unified`.
- Accused with NULL `person_id`: 695. The view count equals `accused_unified`. No person row was created for them.
- Persons with more than one accused: the view count equals the table count.
- Chargesheets: V1 court 7,534, V2 chargesheets 7,086, V2 updates 6,193. Distinct `charge_sheet_id` equals the row count. Raw-id intersection of court and updates is 6,193.
- Identity links: 1,131, all `candidate`, 0 confirmed. The candidate view excludes every other status.
- FSL historical view: 2,006, same as `fsl_unified`.

## API compatibility

Existing GraphQL roots (`firs`, `accused`, `persons`, home, criminal profile, advanced search) were not executed against `dopams_cctns`. Doing so would query missing views and would drop crimes that have no police-station code.

The opt-in reader is `D:\Dopams\DOPAMS-BE\src\datasources\unifiedCctns.ts`. It runs only when `UNIFIED_CCTNS_DATABASE_URL` is set, forces `default_transaction_read_only=on`, and selects from `be_read.crime`, `be_read.accused`, and `be_read.chargesheet`. It is not wired into the GraphQL resolvers. `DATABASE_URL` remains the production path.

Contract checks in `etl3/tests/test_phase7_backend_contract.py` passed: NULL stations stay visible, chargesheet modules stay apart, NULL persons stay unmerged, three pages of 50 crimes ordered by `crime_id` are disjoint and page 1 is stable, FSL is historical only, and a view update is rejected.

## Pagination

`be_read.crime` ordered by `crime_id` with `LIMIT 50 OFFSET 0/50/100` returned three disjoint pages. A second read of page 1 matched the first. `crime_id` is text, so the order is the text order, not a numeric sequence. Equal timestamps are not the sort key.

The existing FIR list sorts a caller-selected `firs_mv` column and uses limit/offset. That query was not retargeted.

## Performance

Measured on `dopams_cctns` after the contract was applied:

- `SELECT count(*) FROM be_read.crime`: index-only scan on `idx_crimes_unified_ps_code`, 16,888 rows, execution 2.438 ms.
- Accused lookup by one `crime_id`: index scan on `idx_accused_unified_crime`, execution 0.106 ms.

No index was added. The existing `firs_mv` plan was not compared, because that view is not on this database.

## Security

- ETL source sessions set `default_transaction_read_only` and assert the database name. That is a session setting, not a separate `GRANT` role.
- The backend unified pool sets the same read-only session flag and `application_name = dopams_be_unified_reader`.
- `be_read` writes raise even on a read-write unified session, because of the trigger.
- Production `DATABASE_URL` was not inspected, so this report does not name its host or database.
- V1 and V2 were not granted to the backend.

## Write path, provenance, and history

ETL-3 owns source-derived current state in `dopams_cctns`. The backend reader cannot update `be_read`. Provenance columns (`source_system`, `source_module`, `source_record_id`, `current_source_run_id`) are exposed and are not writable through the views.

`change_log` stayed at 1,338,633 after the post-change incremental. The backend path added here does not insert history rows. Application edits of users, files, and dedup state stay on the database selected by `DATABASE_URL`. They are not mixed into `change_log`.

`current_as_of` is the ETL observation time. It is not source publication time and it is not an application `updated_at`.

## ETL regression

The ETL suite, including the Phase 7 contract test, exited 0.

Incremental run `d64bc33b-60b0-4052-b70d-605a2121516d` then reported `Total inserted+updated unified rows: 0` in 61.2s. Cursors did not move. Identity candidates newly inserted: 0. Police-station assignments: 0.

After that run:

- `change_log` 1,338,633
- `fsl_unified` 2,006
- chargesheets 7,086 / updates 6,193 / court 7,534
- identity links 1,131
- `consolidation_run_log`: success 14, failed 2, running 0
- V1 `cctns.cctns_fir` 7,305
- V2 `crimes` 9,583

Phase 7 also rejected `CREATE TABLE` on V1 and V2 with `ReadOnlySqlTransaction`.

## Adversarial review

- Raw chargesheet ids still collide (6,193). Stored `charge_sheet_id` values do not.
- The read contract does not merge people and does not read candidate links as persons.
- NULL `person_id` and NULL `ps_code` remain in the views. Counts match the base tables.
- The inner join that would hide 3,723 crimes is in `firs_mv`, which was not pointed at this database.
- Three crime pages did not overlap or reshuffle.
- An update of `be_read.chargesheet` was rejected. `change_log` did not grow on the following incremental.
- `be_read.fsl_historical` reads `fsl_unified` only.
- No backend code path to V1 or V2 was added.
- Production configuration can still select the old database. That is the remaining blocker.
- Rollback of the new schema was rehearsed inside a transaction: `DROP SCHEMA be_read CASCADE` removed the views, `ROLLBACK` restored 8 views and 16,888 crime rows.
- ETL and this reader do not write the same fields. A second consolidation run is still refused by the existing advisory lock.
- The measured contract queries use the existing crime and accused indexes.

## Known limitations

- V1 accused collapses 34,390 source rows to 17,356 logical rows.
- 69 V1 person rows have no usable key.
- 2 V1 `person_code` values are shared.
- 3,723 V1 crimes have no exact police-station match. None were ambiguous. No code was guessed.
- Open arrest and accused-person gaps from Phase 6 stay open.
- Identity links are candidates only.
- FSL is historical. The rich `fsl_case_property` shape is not in the unified model.
- `bulk_event_exclusions` is empty.
- Source protection is a read-only session, not a separate role.
- The existing GraphQL response shape is not implemented on `be_read`.

## Cutover blockers

1. Replacing `DATABASE_URL` with `dopams_cctns` makes `firs_mv` and `accuseds_mv` queries fail or drop rows. That is a P0.
2. Brief facts, files, users, IR54, dedup, and drug NLP have no unified owner. Copying them was not authorized.
3. Frontend contracts expect materialized-view columns that `persons_unified` and `accused_unified` do not have. Those columns were not invented.
4. Chargesheet identifiers in the current Prisma models are UUID and serial. The unified key is a namespaced string.
5. The production database named by `DATABASE_URL` is unknown to this phase because that file was not read.

## Rollback

Applied and left in place: schema `be_read`.

To remove it:

```text
DROP SCHEMA be_read CASCADE;
```

That drop was executed inside a transaction and rolled back. The eight views and the crime rows were still present afterward.

To keep the application on its current database: leave `DATABASE_URL` unchanged and unset `UNIFIED_CCTNS_DATABASE_URL`. No GraphQL resolver was switched, so unsetting the new variable returns the process to the previous read path.

Do not drop unified tables. Do not change V1 or V2.

## Recommended cutover sequence

Do not run this until the blockers above are closed.

1. Confirm `firs_mv` / `accuseds_mv` are replaced by queries on `be_read`, or by a new response contract the frontend accepts.
2. Name an owner for users, files, brief facts, IR54, and dedup. Do not fold them into ETL current state by default.
3. Snapshot `dopams_cctns` and the database the backend uses today.
4. Deploy the backend that reads `UNIFIED_CCTNS_DATABASE_URL` for CCTNS facts and keeps application tables on their own database.
5. Smoke-test health, one V1 crime, one V2 crime, a NULL `ps_code`, a NULL `person_id`, one court row, one chargesheet update with the same raw id, and one historical FSL row.
6. Watch 5xx, row counts, and write errors.

Rollback condition: any of those smoke checks fail, a CCTNS read writes a unified row, or `change_log` grows from the application. Unset `UNIFIED_CCTNS_DATABASE_URL` and restore the previous build. Drop `be_read` only if the contract itself must be removed.

## Gate results

| Gate | Result |
|---|---|
| 1 Schema, explained | PASS as an audit. Direct Prisma mapping is not compatible. |
| 2 Identifiers on `be_read` | PASS |
| 3 Relationships on `be_read` | PASS |
| 4 NULL safety on `be_read` | PASS |
| 5 Existing CCTNS APIs | BLOCKED |
| 6 Query correctness on `be_read` | PASS. Counts match the base tables. |
| 7 Pagination on `be_read.crime` | PASS |
| 8 Provenance through the views | PASS |
| 9 History | PASS. `change_log` unchanged by this phase's run. |
| 10 FSL | PASS |
| 11 Source safety | PASS. V1 fir 7,305. V2 crimes 9,583. |
| 12 ETL regression | PASS |
| 13 Contract query performance | PASS at this volume |
| 14 Security | PASS for the session flags that exist. No separate role. |
| 15 Rollback | PASS. Drop was rolled back in a transaction. |
| 16 Production cutover | BLOCKED |
