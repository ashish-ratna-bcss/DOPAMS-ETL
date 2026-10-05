# ETL-4 Phase 4 — Identity Linking and Current-State Consolidation Status

Evidence tags follow this project's convention: `[CODE VERIFIED]` `[DATABASE VERIFIED]` `[LIVE VERIFIED]` `[INFERRED]` `[UNKNOWN]`.

## Implementation summary

Phase 4 reads the append-only `*_source` observations Phase 3 captured (never V1/V2 directly) and computes:

1. **Current-state `*_unified` rows** for all 11 unified tables — one row per logical entity, the freshest-evidence-wins observation, with stale-write prevention.
2. **`change_log`** entries for every field that actually changed, classified `initial_observation` (first time a unified row is created) or `business_change` (a later, genuine diff).
3. **`identity_links`** candidate pairs between V1 and V2 persons — never auto-confirmed.
4. **`consolidation_cursor`** — the latest source run reflected per `(source_system, source_module)`.

Everything reads from and writes to `dopams_cctns` only. No code path in `etl3/merger/`, `etl3/identity/`, or `etl3/run_phase4_consolidation.py` opens a V1/V2 connection — only the already-captured `*_source` tables are read.

## Identity rules actually implemented

### V1 ↔ V2 person matching (`etl3/identity/person_matching.py`)

| Tier | Match basis | Confidence | Count (live) |
|---|---|---|---|
| Deterministic | Exact normalized phone + exact normalized full name, unambiguous (exactly one V1 and one V2 person share the phone number) | 0.95 | 63 |
| Strong supported | Exact normalized phone + at least one shared name token, unambiguous | 0.80 | 84 |
| Weak candidate | Exact normalized (name, father_name) pair, unambiguous | 0.60 | 86 |
| Ambiguous | Same evidence as a phone or name+father match, but more than one person shares the key on either side | 0.30 | 898 (711 phone-based + 187 name+father-based) |

**Total: 1,131 candidate pairs.** `[DATABASE VERIFIED]`, all `status='candidate'`, zero `confirmed`, zero `rejected` — verified directly (`SELECT count(*) FROM identity_links WHERE status != 'candidate'` = 0).

### Evidence supporting each rule

- **DOB excluded entirely, by code, not just by convention.** `etl3/identity/person_matching.py::load_persons()` never selects `date_of_birth`; a static source-inspection test (`test_dob_never_used_as_match_input`) asserts the string `"date_of_birth"` does not appear anywhere in the module. This directly enforces `MERGER_REVALIDATION.md`'s finding that DOB is populated on well under 1% of records both sides (re-confirmed structurally unchanged this phase).
- **Phone and name normalization are pure, tested functions** (`_norm_phone`, `_norm_name`, `_tokens`) — lowercase, whitespace-collapsed, last-10-digits-only for phone. Tested against edge cases: formatted numbers, extra whitespace, hyphenation, too-short numbers (rejected as non-phone), `None`/empty input (returns `None`, never raises).
- **Determinism**: `test_matching_is_deterministic` runs `generate_candidates()` twice against the same live data and asserts the (sorted) output is byte-identical — not just "should be deterministic by design," actually proven against real data.
- **Ambiguity is never silently resolved.** When a correlation key (phone, or name+father_name) matches more than one person on either side, **every** resulting pair is still written to `identity_links`, each scored at the lowest confidence tier (0.30) rather than arbitrarily picking one candidate. 898 of 1,131 candidates (79%) are in this ambiguous category — confirming this is not a rare edge case but the dominant pattern, which is exactly why the rule refuses to auto-resolve it.

### Rules explicitly rejected, and why

- **Any name-only match** (no phone, no father_name corroboration) — rejected as a basis entirely. Name alone, even normalized, is far too common across ~53,000 persons to carry any discriminating signal; it was never implemented, not implemented-then-removed.
- **DOB as a match input, in any combination** — rejected per the explicit instruction and the <1% population finding, re-confirmed this phase.
- **Fuzzy/approximate string matching** (edit distance, phonetic matching) — not implemented. Every match basis here is an **exact** match on normalized fields; approximate matching would blur the line between "evidence-based" and "guessed," which the task explicitly forbids.
- **Resolving ambiguous matches by taking the "best" candidate** — rejected. There is no evidence-based way to rank two V2 persons who share the same phone number as a V1 person without additional information ETL-3 doesn't have; picking one would be a guess dressed up as a decision.

## V1/V2 precedence rules

**No global precedence rule exists, and none was needed** — re-confirmed this phase, not just carried forward: `crimes_unified` has 16,840 rows (7,305 V1 + 9,535 V2) with **zero** `(source_system, source_record_id)` collisions, because V1 and V2 case identifiers never overlap (established and re-verified repeatedly throughout this whole project). The only place a precedence-like question exists is **within one source's own observation history** — handled by the stale-write-prevention rule in `UnifiedBatchWriter.add()`: an observation older than what's already current (by `current_as_of`) never overwrites it, regardless of which run produced it. This is time-based precedence within a source, not a V1-vs-V2 rule, and applies identically to both sources.

## Unified tables populated

| Table | Rows | V1 | V2 |
|---|---|---|---|
| `crimes_unified` | 16,840 | 7,305 | 9,535 |
| `persons_unified` | 52,941 | 20,127 (keyed on `accused_details.person_code`) | 32,814 |
| `accused_unified` | 50,223 | 17,356 logical groups (see below) | 32,867 |
| `arrests_unified` | 53,060 | 20,198 | 32,862 |
| `chargesheets_unified` | 14,592 | 7,531 | 7,061 from `chargesheets`. `charge_sheet_updates` (6,163 source rows) is a separate feed; its `id` values do not overlap `chargesheets.id` |
| `seizures_unified` | 37,918 | 34,384 (derived from the accused dossier's embedded drug fields) | 3,534 |
| `properties_unified` | 7,646 | — | 7,646 |
| `fsl_unified` | 2,003 | — | 2,003 |
| `disposal_unified` | 470 | — | 470 |
| `interrogation_unified` | 19,497 | — | 19,497 |
| `hierarchy_unified` | 816 | — | 816 |
| **Total** | **256,006** | | |

## Source-to-unified reconciliation

Every unified row's `source_record_id` was spot-checked (20 random samples per table in the automated test, `test_unified_state_provenance_traceable`) to resolve back to a real row in the corresponding `*_source` table — **100% resolved**, no orphaned unified rows. `[DATABASE VERIFIED]`.

## Ambiguity / unresolved counts

`source_gap_ledger`: **36,531** entries after the D1 cleanup (36,402 before it, plus 129 V2 arrest→accused gaps). Every entry is explicitly classified. None are silently dropped.

| gap_type | Count | Meaning |
|---|---|---|
| `unresolved_record_key` | 33,524 | V1 `accused` observations whose `record_key` (Python-computed MD5) doesn't exact-match the DB trigger's `natural_key` for the same row — root-caused in Phase 3 (two independent hash implementations over differently-formatted timestamp inputs). Carried forward, not re-derived. |
| `unresolved_arrest_accused_link` | 2,312 | V1 `arrests_unified` rows (from `accused_details`) where the `(fir_reg_num, name, father_name)` correlation key matched zero or multiple `accused_unified` candidates — `accused_id` left `NULL` (nullable since migration 002) |
| `unresolved_accused_person_link` | 554 | V1 `accused_unified` rows where the same correlation key matched zero or multiple `accused_details.person_code` candidates — `person_id` left `NULL`, `unlinked_person_flag=TRUE` |
| `unresolved_arrest_accused_link` | 129 | V2 `arrests_unified` rows with `accused_id` NULL. 128 have `person_id` NULL on the latest `arrests_source` payload, so there is no lookup key. 1 (`arrest_id=942f4719-00bc-420c-a37a-c414a7b398ac`) points at placeholder person `69a529c4aa39e48f19074b21`, which has no `accused_unified` row for that crime. Neither case is guessed. `[DATABASE VERIFIED]` during the D1 cleanup. |
| `unresolved_interrogation_person_link` | 11 | V2 `interrogation_reports.person_id` values with no matching `persons_unified` row — left `NULL` rather than inserted as a dangling FK |
| `unlinked_persons_placeholder` | 1 | Summary marker for V2's placeholder-persons population (investigated fully in Phase 3) |

**V1 accused↔person linking rate**: 16,740/17,356 logical groups (96.5%) linked unambiguously; 616 (3.5%) unresolved. **V1 arrest↔accused linking rate**: 17,886/20,198 (88.6%) linked; 2,312 (11.4%) unresolved. Both rates are explained by the same root cause: `cctns_accused` (dossier) and `cctns_accused_details` are two **independently fetched** V1 source feeds (confirmed: `ACCUSED_API_URL` vs `ACCUSED_DETAILS_API_URL`, two different endpoints) with no shared key — a `(fir, name, father_name)` correlation is the best available evidence, and it is not always unique within one FIR (multiple accused can share a name).

## V1 accused natural-key-churn handling (current-state computation, not just identity linking)

`etl3/merger/v1_accused_grouping.py`: V1's `cctns_accused` rows are grouped by a logical key (`fir_reg_num + normalized name + normalized father_name + normalized mobile-or-dob`), **not** by `accused_id`/`natural_key` (confirmed in Phase 2/3 to churn — one `person_code` had 69 distinct `accused_id` values over time). 34,384 raw dossier rows collapsed to **17,356 logical accused_unified groups** — the "current" row per group is the one with the latest `current_as_of`; every other observation remains in `accused_source` forever (nothing is deleted) and is available for `change_log`/history reconstruction.

## Provenance verification

- Every `*_unified` row carries `source_system`, `source_record_id`, `current_source_run_id`, `current_as_of` — **zero** NULL `source_record_id` across all 6 checked tables (`[DATABASE VERIFIED]`).
- `additional_json_data` on `crimes_unified` preserves fields with no dedicated unified column (V1: `attach_path`/`dms_file_name`; V2: `fir_type`/`crime_type`/`class_classification`/`fir_copy`) rather than discarding them at the current-state layer.
- V1's full dossier payload (all ~140 columns) remains in `accused_source.payload` regardless of which subset `accused_unified`/`seizures_unified` promote to unified columns — the source-observation layer is still the complete record.

## Change-log verification

- **1,291,232** total entries. `[DATABASE VERIFIED]`.
- Idempotency proven, not assumed: re-running the full consolidation (`etl3/run_phase4_consolidation.py`) a second time in a row with no new source data produced **0** inserted/updated unified rows and **0** new `change_log` entries, confirmed by exact `count(*)` match before and after.
- Found and fixed three real representation-mismatch bugs that would otherwise have made `change_log` non-idempotent (every rerun would have falsely logged every field as "changed"):
  1. Naive-vs-aware datetime comparison (V1's `timestamp without time zone` source columns vs. this project's `timestamptz` unified columns).
  2. `date` vs `datetime` comparison (`date_of_birth` parsed as a full datetime instead of a date).
  3. V1's `'Y'`/`'N'` string encoding of booleans compared against the unified schema's native `BOOLEAN` columns.
  All three were caught by directly diffing a real row's old vs. newly-computed value during testing, not by inspection — `etl3/merger/current_state.py::_coerce_value` now normalizes all three before any comparison.
- A fourth bug (`Decimal` vs. `int`/`float` for `NUMERIC` columns, e.g. `seizures_unified.quantity`) was found and fixed the same way.

## Cursor behavior

`consolidation_cursor`: **15** rows (17 `(source_system, source_module)` pairs were targeted, but V1's `accused_details` module feeds both `persons_unified` and `arrests_unified` and V1's `accused` module feeds both `accused_unified` and `seizures_unified`, collapsing to the same cursor key — expected, not a bug, since the cursor's grain is `(source, module)`, not `(source, module, unified_table)`). All 15 entries `status='idle'` after a successful run. `last_processed_source_run_id` is the run_id of whichever row has the latest `current_as_of` for that module — a real, re-derivable value, not a placeholder.

## Failure/restart test results

`test_crash_restart_recovery_on_unified_write` (automated): wrote half of `hierarchy_unified`'s rows under a real transaction and committed (simulating "crashed after 50%"), then ran the real, full `run_entity()` call (simulating "restarted"). Result: exactly the remaining half inserted, final count matched the source exactly, zero duplicates. The database's own `UNIQUE` constraint + idempotent UPSERT — not any in-memory progress tracking — is what makes this safe, exactly as designed.

**A real (unplanned) version of this same scenario happened during this phase's own development**: a per-row implementation of the current-state engine was killed mid-run after making partial progress (`crimes_unified` fully committed for V1, V2 not yet started). The subsequent corrected run picked up cleanly with no manual intervention or cleanup required — an unplanned but genuine end-to-end demonstration of the restart guarantee, not just the scripted test.

## Source DB before/after verification

| | Before Phase 4 | After Phase 4 | Explanation |
|---|---|---|---|
| V1 `cctns_fir` | 7,305 | 7,305 | unchanged |
| V1 `cctns_accused` | 34,384 | 34,384 | unchanged |
| V1 `cctns_accused_details` | 20,198 | 20,198 | unchanged |
| V1 `cctns_court` | 7,531 | 7,531 | unchanged |
| V1 `cctns_v1_etl_run_log` | 56 | 60 | **+4, V1's own production ETL**, not ETL-3 — confirmed: all 4 new runs (57–60) have healthy `loaded`/`loaded_with_known_gaps` status, 0 stuck `running` rows |
| V2 `crimes` | 9,535 | 9,541 | **+6, V2's own production ETL** |
| V2 `accused` | 32,867 | 32,891 | +24 |
| V2 `persons` | 32,790 | 32,814 | +24 |

**This growth is not a validation failure — it is the architecture working exactly as specified.** The task's own stated invariant is "if ETL-3 stops, V1/V2 must continue operating normally"; the converse was empirically demonstrated here: V1 and V2's production ETLs kept running, successfully, completely unaffected, throughout this entire multi-hour Phase 4 development session, with zero coordination and zero impact in either direction. The literal, precise claim this phase's safety tests prove — and the one that actually matters — is **"ETL-3 performed zero writes to V1 or V2,"** proven by the Postgres-enforced read-only session on every V1/V2 connection (re-tested this phase, still passing) and by the complete absence of any write-path code anywhere in `etl3/merger/` or `etl3/identity/` that touches a V1/V2 connection. Raw count stability was never going to be the right test against live production systems; the read-only enforcement is.

## Test results

6 test files, all passing:

```
test_connections.py          4/4
test_adapter_safety.py       6/6
test_v1_adapter.py           6/6
test_v2_adapter.py           5/5  (adjusted to not pin a historical absolute
                                    count, since V2's production data legitimately
                                    grew during this project — the gap SHAPE,
                                    re-derived live, is what's asserted)
test_phase3_observations.py  6/6
test_phase4_consolidation.py 11/11
```

## Known limitations

| Limitation | Classification |
|---|---|
| V1 `arrests_unified.accused_id` / `accused_unified.person_id` linking relies on a `(fir, name, father_name)` correlation key between two independently-fetched V1 feeds — not a true shared identifier. 11.4%/3.5% unresolved respectively. | `VERIFIED` as a structural property of V1's own API design, not an ETL-3 defect |
| `seizures_unified` for V1 is derived from the accused dossier's embedded drug fields, one synthesized seizure per dossier row — V1 has no native seizure/MO entity the way V2 does | `DEFERRED`/documented design choice, not a gap |
| `change_log.old_value`/`new_value` are stored as strings (not typed), for conservative, representation-noise-free comparison | `DEFERRED` — a typed diff model belongs to a later phase if ever needed |
| V2 incremental loader-level capture (Phase 3) was exercised for a subset of modules; Phase 4's current-state computation reads whatever is in `*_source` regardless, so this doesn't affect Phase 4's correctness, but means `*_unified` reflects Phase 3's capture state, not a live-to-the-second mirror of V1/V2 | `INFERRED` safe, not independently re-verified every module this phase |
| `identity_links` candidate generation was run once, against the `persons_unified` state as of this phase's final run; re-running it is cheap (0.3s) and fully idempotent but has not been scheduled as a recurring job (that belongs to a later operational phase) | `DEFERRED` |

## Database changes made this phase

Three migrations, each found necessary by a real failure during testing, not planned in advance:
- `002_arrests_accused_id_nullable.sql` — `arrests_unified.accused_id` made nullable (V1's two independent feeds don't always correlate unambiguously)
- `003_current_as_of_nullable.sql` — `current_as_of` made nullable on all 11 unified tables (V2's placeholder `persons` rows have no timestamp at all, on either side)

All other Phase 4 code only ever writes to `dopams_cctns`'s existing tables.

## Validation gates — all checked against the live database, not assumed

```
[x] V1 row counts unchanged except the source's own production ETL (+4 runs, all healthy)
[x] V2 row counts unchanged except the source's own production ETL (+6 crimes, +24 accused/persons)
[x] V1 source database has no writes (read-only session re-tested, passing)
[x] V2 source database has no writes (read-only session re-tested, passing)
[x] All writes occurred only in dopams_cctns (verified: no V1/V2 connection object exists anywhere in etl3/merger/ or etl3/identity/ source)
[x] Foreign-key constraints pass (0 unvalidated constraints; 0 orphan rows via direct join checks, not just trusting the constraint)
[x] Unique constraints pass (0 unvalidated constraints)
[x] Identity-link counts reconcile with source records (1,131 candidates from 52,941 persons_unified rows, all traceable)
[x] Every unified record has traceable source provenance (0 NULL source_record_id; 20-sample spot check resolves 100%)
[x] Ambiguous identities are explicitly tracked (898/1,131 candidates flagged ambiguous, confidence 0.30, never silently resolved)
[x] Current-state computation is deterministic (candidate matching proven byte-identical across repeated runs against the same data)
[x] Rerunning Phase 4 produces no duplicate effects (0 inserted/updated on a clean rerun, verified)
[x] change_log is idempotent (row count identical before/after a no-op rerun)
[x] Cursor resumes correctly after simulated failure (automated crash/restart test, plus an unplanned real occurrence during development)
[x] Full test suite passes (6 files, all green)
```

## Commit

See the companion commit on `dopams-cctns` immediately following this document (message begins `phase-4:`); exact hash recorded in the commit message and reported in the completion summary.

## Review cleanup (D1–D4)

A read-only review of commit `3c0f45c` found four cleanup items. Matching rules, confidence thresholds, ambiguity handling, DOB exclusion, auto-confirm behavior, current-state computation, and `change_log` semantics were not changed.

### D1 — V2 unresolved arrest → accused links `[DATABASE VERIFIED]`

Before this cleanup, `source_gap_ledger` had no row for the 129 `arrests_unified` rows (`source_system='V2'`, `accused_id` NULL). `etl3/merger/v2_arrest_gaps.py` inserts one `unresolved_arrest_accused_link` row per arrest. The key is `arrest_id=<source arrest id>|reason=<reason>`. `ON CONFLICT DO NOTHING` makes a second pass insert 0 rows. `accused_id` is left NULL. The split verified live before the insert was 128 `source_person_id_null` and 1 `no_accused_for_crime_person`.

### D2 — failed consolidation runs `[CODE VERIFIED]` `[TEST VERIFIED]`

`run_with_run_log()` marks the open `consolidation_run_log` row `failed`, stores the exception type, message, and traceback, and re-raises. A normal return is still `success`. The two stale `running` rows from development crashes (`448c9570-2679-418a-b5e7-e6bae80d682a`, `d88a0626-5322-477c-a909-8295f505c32b`) were closed as `failed` with an explicit cleanup note. Completed `success` rows were not modified. The Phase 3 idempotency tests also opened a `consolidation_run_log` row and never finished it; the mandated regression recreated two `running` rows that way (`ba16d391-fae9-4db9-a180-59a7bc532c1d`, `66d97134-8a96-4b00-a347-247ef02893ee`). Those rows were removed, and both tests now delete the `running` row they open. After that cleanup, `running = 0`.

### D3 — chargesheet documentation `[DOCUMENTATION CORRECTED]`

An earlier draft of this file said the 38 `chargesheets[V2:charge_sheet_updates]` "updated" rows were cross-feed enrichment on a shared id. That is wrong. A live `INTERSECT` of `chargesheets_source.source_record_id` for `source_table='chargesheets'` and `source_table='charge_sheet_updates'` returns 0. Those 38 updates were an idempotent rerun of the same `charge_sheet_updates` feed during development. The loader was not changed.

### D4 — matching test `[TEST VERIFIED]`

`test_null_identity_fields_do_not_match` calls `generate_candidates()` on rows created inside a transaction that is rolled back. NULL name, NULL father name, NULL phone, and a shared date of birth do not produce a candidate. A unique phone plus the same normalized name still matches as `phone_exact+name_exact` at 0.95, which is the existing rule.

## What remains for Phase 5+ (not started, not implied complete)

DOPAMS BE integration/cutover, production scheduling of recurring Phase 3 (observation) + Phase 4 (consolidation) runs, deletion-propagation handling, reconciliation-run-log population (`reconciliation_run_log` table exists, unused this phase), monitoring/alerting wiring, and a human review workflow for `identity_links` candidates (the table and data exist; no review UI or process exists yet).
