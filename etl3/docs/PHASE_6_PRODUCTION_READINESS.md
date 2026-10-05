# Phase 6 — Production Readiness

## Executive status

PASS WITH KNOWN LIMITATIONS

ETL-3 can be trusted as the read-only consolidation of CCTNS V1 and CCTNS V2 into `dopams_cctns`. This is not a claim that the DOPAMS backend can be cut over. That application is outside this repository and was not inspected.

## Environment

- Git baseline: `f5c3bbf137d6d26fff6923a858a0f23172055fd3`
- Unified database: `dopams_cctns`
- V1 database: `cctns_v1`
- V2 database: `cctns-v2`
- PostgreSQL: 16.14
- Migrations applied: `001_initial_schema.sql`, `002_arrests_accused_id_nullable.sql`, `003_current_as_of_nullable.sql`, `004_reconciliation_and_chargesheet_keys.sql`, `005_chargesheet_update_module.sql`
- Working tree at the start of Phase 6 was clean and was exactly the Phase 5 acceptance commit.

## What the design documents got wrong

The design files under `dopams_cctns/schema/` were written before `etl3/` existed. Their phase numbers are not the phase numbers used to build the pipeline. Counts in those files are a design-time snapshot. Two statements were unsafe against the live data:

- V1 `court_id` and V2 `charge_sheet_updates.id` are not independent keys. All 6,193 update ids also occur as V1 court ids. Using the raw id as `charge_sheet_id` had replaced 68 V1 court rows and had left 6,125 V2 updates out of current state.
- `fsl_case_property` is intentionally not merged. Historical `fsl_unified` rows were kept.

## Reconciliation

Statuses written by the current job:

| Domain | Source | Observed | Unified | Status |
|---|---:|---:|---:|---|
| V1 fir | 7305 | 7305 | 7305 | EXPECTED |
| V1 accused | 34390 | 34390 | 17356 | EXPECTED (logical collapse) |
| V1 accused_details / arrests | 20198 | 20198 | 20198 | EXPECTED |
| V1 court | 7534 | 7534 | 7534 | EXPECTED |
| V2 crimes | 9583 | 9583 | 9583 | EXPECTED |
| V2 accused | 32992 | 32992 | 32992 | EXPECTED |
| V2 persons | 32914 | 32914 | 32914 | EXPECTED |
| V2 arrests | 32988 | 32988 | 32988 | EXPECTED |
| V2 chargesheets | 7086 | 7086 | 7086 | EXPECTED |
| V2 charge_sheet_updates | 6193 | 6193 | 6193 | EXPECTED |
| V2 mo_seizures | 3540 | 3540 | 3540 | EXPECTED |
| V2 properties | 7683 | 7683 | 7683 | EXPECTED |
| V2 fsl_case_property | 2006 | 2006 | 2006 | INTENTIONALLY_EXCLUDED |
| V2 disposal | 482 | 482 | 482 | EXPECTED |
| V2 interrogation_reports | 19569 | 19569 | 19569 | EXPECTED |
| V2 hierarchy | 816 | 816 | 816 | EXPECTED |

V1 crimes and V2 crimes share no `fir_reg_num` (overlap 0). The catalog is checked against the V1 and V2 adapter module lists at the start of a run. A module on only one side fails the run.

V1 persons are derived from `accused_details`, not a separate source module. 20,129 rows have a `person_code`. 2 codes are shared by more than one source row, so `persons_unified` has 20,127 V1 rows. 69 rows have no `person_code`, no name, and no father name. They stay in `arrests_source` and are open `v1_person_key_absent` gaps. No person id is invented.

V1 seizures are one projection of each accused dossier row (34,390). They are not a separate source module.

## Open gaps

| Gap | Open | Class |
|---|---:|---|
| V1 unresolved_record_key | 33530 | KNOWN_SOURCE_LIMITATION |
| V1 unresolved_v1_ps_code | 3723 | UNRESOLVED_RELATIONSHIP |
| V1 unresolved_arrest_accused_link | 2312 | UNRESOLVED_RELATIONSHIP |
| V1 unresolved_accused_person_link | 554 | UNRESOLVED_RELATIONSHIP |
| V1 ora_06502_window | 180 | KNOWN_SOURCE_LIMITATION |
| V1 v1_person_key_absent | 69 | KNOWN_SOURCE_LIMITATION |
| V2 unresolved_arrest_accused_link | 122 | UNRESOLVED_RELATIONSHIP |
| V2 unresolved_interrogation_person_link | 11 | UNRESOLVED_RELATIONSHIP |
| V2 fk_retry_capped | 3 | KNOWN_SOURCE_LIMITATION |
| V2 address_unresolved | 1 | DATA_QUALITY |
| V2 unlinked_accused | 1 | KNOWN_SOURCE_LIMITATION |
| V2 unlinked_persons_placeholder | 1 | KNOWN_SOURCE_LIMITATION |

No open gap type fell through to `ETL_DEFECT`. Eight V2 arrest gaps are `RESOLVED` because the source later supplied a person id. Duplicate gap keys: 0. An unknown gap type classifies as `ETL_DEFECT`.

## Control plane

- `consolidation_run_log`: success 13, failed 2, running 0. The two failures predate this phase. A new run marks a leftover `running` row failed. A raised exception is stored on the run row and re-raised. A second concurrent run raises `ConcurrentRunError` and does not open a run row.
- `consolidation_cursor`: 16 rows, all `idle`. None store `__initial__` or `__initial_no_run_id__`. Both live runs left every cursor unchanged. The cursor moves only after consolidation, never backward, and never to a sentinel.
- `change_log`: 1,338,633 rows. Duplicate tuples: 0. Classifications: `initial_observation` 1,330,134 and `business_change` 8,499. The chargesheet repair was logged as initial observation. `business_change` on 2026-08-24: 42. `bulk_event_exclusions` has 0 rows. Timestamp-only repeats do not become business changes because unchanged mapped fields are not logged. The bulk-exclusion table itself is unused.
- `identity_links`: 1,131, all `candidate`. Confirmed: 0. Self-links: 0. Duplicate pairs: 0. No code path confirms a link. DOB is not a match input.

## Safety

V1 write attempt: `ReadOnlySqlTransaction`. `cctns_fir` count 7,305 before and after.
V2 write attempt: `ReadOnlySqlTransaction`. `crimes` count 9,583 before and after.
ETL-3 opens source connections only in `etl3/db/connections.py`, with `default_transaction_read_only=on` and a database-name assertion. The unified connection is the only writer, and it asserts `dopams_cctns`.

There is no separate `GRANT SELECT` role. The source credentials can write in some other session. This session cannot. Creating a new database role was not done, because that would change the source databases.

## Integrity

Duplicate primary keys: 0 across crimes, persons, accused, arrests, and chargesheets.
Foreign keys checked (accused→crime, arrest→accused, chargesheet→crime): 0 violations.
Duplicate change-log tuples: 0.
Duplicate gap keys: 0.
Duplicate identity pairs: 0.

## Idempotency

Run `5b09ec35-b913-4f0c-b5e6-78619a75837f` restored the missing chargesheet rows: V1 court 68 inserted, V2 updates 6,125 inserted. That was the repair.
Run `9a984c7a-3874-481b-8fee-4f7ea347f371` then inserted 0, updated 0, and added 0 change-log rows. Cursors did not move. `fsl_unified` stayed 2,006. Police-station codes stayed 3,582, with provenance still present, and 0 new assignments.

## Recovery and cursor

The Phase 5 crash, restart, and cursor tests were run again after the lock and key changes. They passed. A failed run does not advance the cursor. The next run recomputes current state from the observations already stored.

Consolidation is not one database transaction. Observations commit per module. Most unified entities commit per entity. The three chargesheet writers commit together. A crash in the middle leaves a failed or interrupted run and a cursor that has not moved past work that was not consolidated. The next run converges. It does not roll back observations that were already committed.

## Provenance

A unified row carries `source_system`, `source_record_id`, and `current_source_run_id`. That run id is the observation key. Checked live: V1 crime `2011001020144` joins `crimes_source` on system, record, and run, source table `fir`. A V2 update row is `V2:charge_sheet_updates:1049` with raw source record `1049`. V1 police-station enrichment keeps `ps_name` and `unit_district` and stores the derived code plus `additional_json_data.ps_resolution`.

## Operational failures

- Source database unavailable: the adapter raises, the run is marked failed, and the cursor is not advanced for work that did not finish.
- Unified database unavailable: the run fails before it can mark success.
- Second concurrent run: refused by the session advisory lock.
- Network drop: the session lock releases when the connection dies. The next run closes a leftover `running` row and recomputes.

## Performance

One incremental pass recomputes current state from the full observation set. Measured duration about 60 seconds at the current volume. That is the same shape as Phase 5. No rewrite was made for it.

## Known limitations

- V1 accused collapses 34,390 source rows to 17,356 logical rows.
- 69 V1 person rows have no usable key.
- 2 V1 `person_code` values are shared, so those source rows share one person.
- 3,723 V1 crimes have no exact police-station match. 0 were ambiguous. No code was guessed.
- Arrest→accused and accused→person gaps above stay open. They are not guessed.
- Identity links are candidates only.
- FSL is observed and excluded. 2,006 historical `fsl_unified` rows remain.
- `bulk_event_exclusions` is empty. Bulk handling is the mapped-field comparison.
- The run is recoverable but not one atomic transaction.
- Source write protection is the read-only session, not a separate database role.
- The DOPAMS backend schema and API were not inspected.

## Cutover blockers

ETL blockers: none.

DOPAMS BE integration blockers: the backend repository, its database target, and its read/write contract are not in this repository. Phase 7 cannot start until those are inspected. Do not point the backend at `dopams_cctns` on the evidence in this report alone.

## Adversarial review

- A source row missing from observations is `UNRESOLVED`, including an excluded module.
- Two runs cannot both write. The second is refused. Primary keys and the change-log tuple stay unique.
- The idempotent rerun added no business changes.
- A failed run does not advance the cursor.
- Source writes raise `ReadOnlySqlTransaction`.
- No identity link is confirmed automatically.
- V1 and V2 chargesheet rows no longer share a primary key. FIR overlap is 0.
- FSL is not in the unified merge list.
- An ambiguous or unmatched police station does not receive a code. Replay left the 3,582 codes and their provenance in place.
- A crash leaves observations and a failed run. The next run rebuilds current state from those observations.
- The sampled unified rows join back to a source observation.
