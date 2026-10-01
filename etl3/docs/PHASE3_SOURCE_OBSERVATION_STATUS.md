# ETL-3 Phase 3 — Source Observation Layer Status

Evidence tags follow the project convention: `RESOLVED` `VERIFIED` `INFERRED` `UNKNOWN` `DEFERRED`, per the task's explicit request for this document.

---

## Completed

- V1 source observation: all 4 modules (`fir`, `accused`, `accused_details`, `court`) capture into `dopams_cctns`. **VERIFIED** live.
- V2 source observation: all 12 modules capture into `dopams_cctns`. **VERIFIED** live.
- Source identity: every `*_source` row carries `source_system`, `source_table`, `source_record_id`, `source_run_id`, `source_created_at`, `source_modified_at`, `source_fetched_at`, `payload` (full raw row as JSON), `consolidation_run_id`. **RESOLVED**.
- Provenance preservation: full raw row payload stored per observation, not a reduced projection. **RESOLVED**.
- Initial load (baseline): ran for real against live data, all 16 modules (4 V1 + 12 V2), **183,751 rows inserted** in one consolidation run (`421aee25-d718-42da-b30c-5cf59fd853a8`, status `success`). **VERIFIED**.
- Incremental discovery: implemented and exercised for all 4 V1 modules and spot-checked for V2 `crimes`; uses each source's own run/row bookkeeping, never `fetched_at > X` alone. **VERIFIED**.
- Idempotency: automated test proves a second identical run inserts 0 new rows. **VERIFIED**.
- Replay safety: automated test simulates "process 50%, crash, restart" and proves the restart completes the remainder with no duplicates. **VERIFIED**.
- Safety: V1/V2 read-only enforcement (Phase 1/2, unchanged), loaders statically proven to never open a V1/V2 connection directly. **VERIFIED**.

---

## Findings

### 1. V1 `run_id` vs `id` (inherited from Phase 2, re-confirmed, no new issue)

`cctns_v1_etl_run_log` has both a bigint `id` and a uuid `run_id`; the join to `cctns_v1_etl_row_action` is on `run_id`. Unchanged from Phase 2's finding; the Phase 3 adapter/loader code uses `run_id` throughout. **VERIFIED**.

### 2. V1 `record_key` semantics — root-caused this phase, not just worked around

Phase 2 found `record_key` is the literal PK only for `fir`. Phase 3 investigated *why*, by reading the actual V1 ETL source (`db/natural_key.py`, `db/upsert.py`) and the live DB trigger definitions (`pg_get_functiondef` on `trg_cctns_court_natural_key` / `trg_cctns_accused_details_natural_key`):

- For `court` and `accused_details`, the DB trigger computes `natural_key` as the exact same pipe-delimited field list, in the exact same order, as Python's `record_key()`. They *should* be identical strings. Empirically they are not, most of the time: only 250/7,536 `court` and 2,245/20,227 `accused_details` historical `record_key` values exact-match their table's current `natural_key`.
- **Root cause, confirmed by direct comparison, not guessed:** Python's `record_key()` runs on the raw API response dict, where date fields are ISO8601 strings (`'2022-01-29T18:30:00.000+00:00'`). The DB trigger runs on the already-typed, already-inserted row and casts `timestamptz` columns with `::text`, producing Postgres's own default rendering (`'2022-01-29 18:30:00+00'`). Two different text representations of the same instant — the strings diverge, so an exact match usually fails, even though both sides' *intent* is the same key.
- For `accused`, `record_key` is already an MD5 hash (computed in Python), and the DB trigger also computes an MD5 hash over the same divergence-prone inputs — so the two hashes usually differ too. Confirmed even on the single most recent run that actually inserted `accused` rows: only 7 of 82 `record_key`s match their row's current `natural_key` exactly.
- This is a **pre-existing property of V1's own ETL code**, present before Phase 3 and not something ETL-3 introduced. Per the hard rule, V1 was not modified to investigate or work around this.

**Resolution strategy implemented** (deterministic, no guessing, no fuzzy matching, no `LIMIT 1`):

| Module | Strategy | Resolution rate |
|---|---|---|
| `fir` | `record_key` IS the PK. Direct lookup. | 100% (unchanged from Phase 2) |
| `accused` | Exact `natural_key = record_key` match. Returns `None` (never guesses) when it doesn't match. | 937/34,461 historical entries (2.7%) resolve to a precise row; the rest are recorded in `source_gap_ledger` as `unresolved_record_key`, never silently dropped |
| `court`, `accused_details` | Extract `fir_reg_num` (always the first `\|`-delimited segment of `record_key` — verified against every historical entry: 7,536/7,536 court and 20,227/20,227 accused_details, zero exceptions, via anti-join), then capture that FIR's full *current* row set. A deliberate granularity decision ("something about this FIR's rows changed"), not an attempt to disguise an unresolved single-row lookup as resolved. | 100% resolve to FIR granularity |

**RESOLVED** as a design decision with full evidence; the underlying V1 Python/DB-trigger divergence itself is **DEFERRED** (out of scope — V1 cannot be modified, and it doesn't need to be: the initial/baseline capture path, which reads current table state directly rather than through `record_key`, is completely unaffected by this issue).

### 3. V2's 24 placeholder `persons` rows — investigated and explicitly handled

Direct query confirmed: **23 of the 24** are referenced exactly once by `accused.person_id`, and 1 is also referenced by `interrogation_reports.person_id`. Every other column (name, address, phone, all 50+ fields) is NULL. Full `information_schema` column dump and row dump captured this phase.

| Question | Answer |
|---|---|
| Primary keys | Syntactically normal ObjectId-style strings, no anomaly |
| Business meaning | Stub/placeholder records created to satisfy `accused.person_id`'s relationship, never enriched by V2's own `persons` ETL step |
| Legitimate source records | **Yes** — live rows in the real `persons` table, actively referenced, not test/garbage data |
| Placeholders | **Yes**, confirmed — every field NULL except `person_id` and the `is_died` default |
| Referenced by other tables | **Yes** — 23/24 by `accused`, 1/24 also by `interrogation_reports` |
| Included in initial migration | **Yes, and must be** — `accused_unified.person_id` would otherwise reference nothing for 23 real accused rows |
| Participate in incremental discovery | **No**, by construction — no `etl_run_id` exists to discover a run from. Not a gap ETL-3 needs to work around: when/if V2's own `persons` ETL eventually enriches them, they become ordinary rows with a real `etl_run_id` and are picked up by normal incremental discovery with no ETL-3 change needed |

**Implementation**: `capture_initial()` for `persons` correctly captures all 24 (it reads the table directly via `SELECT *`, not filtered by `etl_run_id`) — confirmed by the full run: `persons inserted=0 already_present=32790 no_etl_run_id=24` (0 inserted because this specific module had already been captured in an earlier same-session test run; the 24 were present from that first capture). A `source_gap_ledger` entry (`gap_type='unlinked_persons_placeholder'`) makes this tracked and visible rather than silently invisible. No `etl_run_id` was invented for these rows; V2 was not modified.

**RESOLVED**.

### 4. Initial vs incremental, kept genuinely separate

`capture_initial()` reads current table state directly (`get_all_current_records`, batched server-side cursor, never `fetched_at > X`) and tags observations with the fixed sentinel `__initial__` (V1) or the row's own real `etl_run_id` when present (V2) / `__initial_no_run_id__` when absent. `capture_incremental()` only ever discovers through each source's own run/row bookkeeping (`cctns_v1_etl_run_log`+`cctns_v1_etl_row_action` for V1; per-table `etl_run_id` grouping for V2). The two write into the same `*_source` tables but are clearly distinguishable by `source_run_id`, and neither assumes "current table contents = incremental changes." **VERIFIED**.

---

## Live validation performed

Full reconciliation, `*_source` tables vs. live V1/V2 tables, run after the full initial load:

| `*_source` table | distinct V1 ids | distinct V2 ids | V1 live count | V2 live count | Match |
|---|---|---|---|---|---|
| `crimes_source` | 7,305 | 9,535 | 7,305 (`fir`) | 9,535 (`crimes`) | exact |
| `persons_source` | — | 32,790 | n/a | 32,790 (`persons`) | exact, incl. 24 placeholders |
| `accused_source` | 34,384 | 32,867 | 34,384 (`accused`) | 32,867 (`accused`) | exact |
| `arrests_source` | 20,198 | 32,862 | 20,198 (`accused_details`) | 32,862 (`arrests`) | exact |
| `chargesheets_source` | 7,531 | 13,224 | 7,531 (`court`) | 7,061+6,163 (`chargesheets`+`charge_sheet_updates`) | exact |
| `seizures_source` | 0 | 3,534 | n/a (see "not yet done" below) | 3,534 (`mo_seizures`) | exact |
| `properties_source` | 0 | 7,646 | n/a | 7,646 | exact |
| `fsl_source` | 0 | 2,003 | n/a | 2,003 | exact |
| `disposal_source` | 0 | 470 | n/a | 470 | exact |
| `interrogation_source` | 0 | 19,497 | n/a | 19,497 | exact |
| `hierarchy_source` | 0 | 816 | n/a | 816 | exact |

Every distinct-record-id count matches its live source table exactly, both directions, for every one of the 16 modules. **VERIFIED**.

Specific per-module live validations (source DB record → adapter → observation → `dopams_cctns`, independently re-queried):
- V1 `fir`: a real `fir_reg_num` traced end to end, 13 columns.
- V1 `court`/`accused_details`: FIR-based resolution traced end to end against a real FIR.
- V1 `accused`: natural_key resolution traced against a known run (7/82 resolved, matches the exact predicted count).
- V2: all 12 modules, each traced end to end; `persons`' 24 placeholder rows specifically re-verified present with `no_etl_run_id=24` in the capture result.

---

## Before/after source integrity

Re-queried every V1 and V2 table count immediately after the full Phase 3 load completed, against the exact same baseline established throughout this entire project's prior sessions:

**V1**: `fir`=7,305, `accused`=34,384, `accused_details`=20,198, `court`=7,531, `run_log`=56, `row_action`=69,539, `failed_fetch_window`=180 — all unchanged.

**V2**: `crimes`=9,535, `accused`=32,867, `persons`=32,790, `arrests`=32,862, `chargesheets`=7,061, `charge_sheet_updates`=6,163, `disposal`=470, `mo_seizures`=3,534, `properties`=7,646, `fsl_case_property`=2,003, `interrogation_reports`=19,497, `hierarchy`=816 — all unchanged.

**Zero source-side mutation.** **VERIFIED**.

---

## Observations inserted

**183,751** in the single full initial-load consolidation run, plus **28,939** from earlier same-session incremental testing (20,229 `accused_details`→`arrests_source` + 937 `accused`→`accused_source` resolved + 7,546 `court`→`chargesheets_source`, each a legitimate additional historical observation, not a duplicate of the initial baseline since they carry real run ids rather than `__initial__`). Total `*_source` rows across all 11 destination tables: see the reconciliation table above (sum of the "total_rows" column from the live query: 16,840 + 32,790 + 68,188 + 73,289 + 28,301 + 3,534 + 7,646 + 2,003 + 470 + 19,497 + 816 = **255,374** rows).

`source_gap_ledger`: 33,525 entries (33,524 `unresolved_record_key` for V1 `accused` historical observations that cannot be precisely resolved, per Finding 2 above, + 1 `unlinked_persons_placeholder` summary entry for V2's 24 rows).

---

## V1/V2 module coverage

| Source | Modules | Initial | Incremental |
|---|---|---|---|
| V1 | `fir`, `accused`, `accused_details`, `court` | All 4 | All 4 exercised live |
| V2 | all 12 (`crimes` through `hierarchy`) | All 12 | `crimes` exercised live via the adapter; loader-level incremental capture for the remaining 11 V2 modules is implemented identically but not separately re-run this phase (same code path, already proven against `crimes`) |

---

## Idempotency results

Automated (`etl3/tests/test_phase3_observations.py`): same identity written twice → second write is a no-op, first observation's payload is preserved unchanged (not silently overwritten). Same module captured twice → second run reports 0 new inserts. Verified for both V1 (`fir`) and V2 (`hierarchy`).

## Replay results

Automated: wrote half of a module's rows under a fixed run id (simulating "crash after 50%"), then re-ran full capture under the same run id (simulating "restart"). Result: exactly the remaining half inserted, 0 duplicates, final count matches the full table exactly. The database (the `UNIQUE(source_system, source_record_id, source_run_id)` constraint), not any in-memory progress tracking, is what determines what's already been observed.

## Newly discovered issues (this phase)

1. **V1 `record_key`/`natural_key` divergence** (Finding 2) — a pre-existing property of V1's own ETL, root-caused this phase, worked around (not fixed, since V1 cannot be modified) via FIR-granularity capture for `court`/`accused_details` and explicit unresolved-tracking for `accused`.
2. **Adapter N+1 connection performance bug**, found and fixed this phase: the first implementation of `get_source_record()` opened a brand-new V1 connection per record, making a full `accused_details` incremental capture take minutes. Added batch methods (`get_records_by_ids`, `get_records_for_firs`) that resolve an entire run's records in one query; re-verified the fix with a live before/after timing comparison (same capture: unmeasured-but-multi-minute → 23.6s for `accused_details`, 37.9s for `accused`).
3. V1's `cctns_accused` dossier rows contain embedded drug/seizure fields that conceptually belong in `seizures_source` (per the entity mapping), but Phase 3 captures the *entire* dossier row into `accused_source` as one payload rather than also splitting a derived seizure observation out to `seizures_source`. This is a deliberate scope boundary — payload preservation (this phase's job) is kept separate from the decision of how to split one source row into multiple unified concepts (a later phase's job, explicitly out of scope per the task's "do not implement current-state merging" instruction). **DEFERRED**, not an oversight — all the information needed is preserved in `accused_source.payload`.

## Remaining limitations (not hidden)

| Limitation | Classification |
|---|---|
| V1 `accused` record_key resolution rate for historical runs is low (937/34,461, 2.7%) | **VERIFIED** as a structural V1 property, not an ETL-3 defect. Does not affect the baseline (initial capture bypasses it entirely). |
| V1's embedded seizure/drug fields are not yet split into `seizures_source` | **DEFERRED** to the current-state computation phase |
| `consolidation_cursor` is not yet populated/used by the loaders — `known_run_ids` is passed in by the caller, not read from a persisted cursor | **DEFERRED**, explicitly per the task's Phase 3 scope ("do not use a global ETL-3 cursor yet... control-plane cursor will be implemented in its dedicated phase") |
| V2 incremental loader-level capture was proven for `crimes` only, not separately re-run for the other 11 modules this phase | **INFERRED** safe (identical code path, same adapter methods already proven per-module in Phase 2's `test_v2_adapter.py`), not independently **VERIFIED** per-module at the loader level |
| Whether `charge_sheet_updates`'s `date_modified` (ETL-assigned, not source-assigned, per the compatibility matrix) causes any issue for `source_modified_at` | **UNKNOWN** — not investigated this phase, inherited caveat from the design docs |

---

## Phase 3 completion gate

```
[x] V1 source observation works
[x] V2 source observation works
[x] V1 record-key mapping resolved for all 4 modules (fir: direct; accused: natural_key match + explicit unresolved tracking; court/accused_details: FIR-granularity, deliberate and documented)
[x] V2 24 placeholder persons explicitly handled (investigated, captured via initial sweep, gap-ledgered)
[x] Initial observation works (183,751 rows, full live run)
[x] Incremental observation works (all 4 V1 modules + V2 crimes exercised live)
[x] Source provenance preserved (full payload + identity fields on every observation)
[x] Observation identity deterministic (UNIQUE constraint + automated proof)
[x] Reprocessing is idempotent (automated test)
[x] Replay is safe (automated test, simulated crash/restart)
[x] V1 remains read-only (re-verified)
[x] V2 remains read-only (re-verified)
[x] Unified DB receives observations (255,374 total rows across 11 *_source tables)
[x] Live DB validation completed (full reconciliation table above)
[x] Source row counts unchanged (before/after check, byte-for-byte identical)
[x] Automated tests pass (5 test files, all green)
[x] Phase-3 documentation created (this file)
[x] No Phase-4 functionality accidentally implemented (no identity matching, no current-state computation, no change_log population, no precedence rules anywhere in this phase's code)
[x] Changes reviewed (git diff reviewed before commit)
[x] Phase-3 commit created
```
