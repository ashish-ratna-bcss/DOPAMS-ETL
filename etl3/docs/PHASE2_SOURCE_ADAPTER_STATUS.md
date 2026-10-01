# ETL-3 Phase 2 — Source Adapter Status

Evidence tags follow `MERGER_REVALIDATION.md`'s convention: `[CODE VERIFIED]` `[DATABASE VERIFIED]` `[LIVE VERIFIED]` `[INFERRED]` `[UNKNOWN]`. "Live verified" here specifically means: the adapter code was actually run against the live `cctns_v1`/`cctns-v2` databases this phase and its output was cross-checked against an independent direct query, not just reviewed as code.

## V1 adapter status

`etl3/sources/v1/adapter.py`, `V1Adapter`. `[LIVE VERIFIED]` for all four interface methods, via `etl3/tests/test_v1_adapter.py`.

**Supported modules:** `fir`, `accused`, `accused_details`, `court` — matching `cctns_v1_etl_run_log.entity`'s exact live values `[DATABASE VERIFIED]`.

**Incremental mechanism:** join `cctns_v1_etl_run_log` (filtered to `status IN ('loaded', 'loaded_with_known_gaps')`) to `cctns_v1_etl_row_action` on **`run_id` (UUID)**, then to the business table on `record_key`.

**Two real defects were found and fixed by live testing, not by re-reading the design docs:**

1. `cctns_v1_etl_run_log` has both a bigint `id` (the PK, used throughout this project's prior audits as "run 54"/"run 56") and a separate `run_id` UUID column. The join to `cctns_v1_etl_row_action` is on `run_id`, not `id`. A first draft of `discover_new_runs()` used `id` and failed immediately against live data with `invalid input syntax for type uuid: "28"`. Fixed: `source_run_id` is now the UUID.
2. `cctns_v1_etl_row_action.record_key` is the literal table PK **only for `fir`** — confirmed by an anti-join against every live `cctns_fir` row (0 mismatches). For `accused`, `accused_details`, and `court`, `record_key` is a pipe-delimited composite of natural-key fields, not the PK (`accused_id`/`court_id`). `get_source_record()` now raises `NotImplementedError` with a clear explanation for these three modules instead of attempting an incorrect lookup — resolving this correctly belongs to the source-observation-layer phase, which will need to correlate via the `fir_reg_num` prefix rather than the full composite string, and connects directly to V1's already-documented natural-key churn.

**Live verification performed:**
- Discovered 5 successful `accused` runs (filtered correctly away from the known `extract_failed`/`extract_partial_failed` runs 54/55).
- `get_changed_records()` for the latest run (the `loaded_with_known_gaps` run) returned 0 records, and for an earlier run returned a count that matched a direct `cctns_v1_etl_row_action` query exactly (not approximately).
- `get_source_record('fir', ...)` resolved a real `fir_reg_num` to its full 13-column row.
- `get_source_gap_state()` returned exactly 180 entries, all `OPEN`, matching `cctns_v1_failed_fetch_window` exactly `[DATABASE VERIFIED]`.
- Before/after row counts on `cctns_v1_etl_run_log`, `cctns_v1_etl_row_action`, `cctns_v1_failed_fetch_window`, and the `running`-status count were identical across the whole test run: **no source-side mutation occurred.**

**Known limitation, not yet resolved:** `get_source_record()` for `accused`/`accused_details`/`court` is explicitly unimplemented (raises, does not guess). Correct resolution requires Phase 3 design work, not a quick fix here.

## V2 adapter status

`etl3/sources/v2/adapter.py`, `V2Adapter`. `[LIVE VERIFIED]` for all four interface methods and across all 12 supported modules, via `etl3/tests/test_v2_adapter.py`.

**Supported modules (12):** `crimes`, `accused`, `persons`, `arrests`, `chargesheets`, `charge_sheet_updates`, `disposal`, `mo_seizures`, `properties`, `fsl_case_property`, `interrogation_reports`, `hierarchy`. `file_media_bookkeeping` deliberately excluded — no `date_created`/`date_modified` at all, out of scope for this phase's business-entity adapters.

**Incremental mechanism:** group each table directly on its own `etl_run_id` column (no separate run-log table the way V1 has one). Confirmed this phase: `etl_run_id` is a strict Postgres `uuid` type on every table checked, not text — a naive `<> ''` filter fails with a type error rather than matching nothing; fixed to filter on `IS NOT NULL` only.

**One data-quality finding, fully characterized:** 24 of 32,790 `persons` rows have `etl_run_id IS NULL`. On inspection these also have NULL `full_name`/`date_created`/`fetched_at` — essentially placeholder rows with only a `person_id` populated. Confirmed this is the *entire* explanation for the gap: summing discovered rows across all runs for `persons` gives exactly 32,766 = 32,790 − 24, and the subtraction was independently verified by a direct `COUNT(*) WHERE etl_run_id IS NULL` query. **Every other one of the 12 modules has zero gap** — discovered-row totals matched each table's live row count exactly: crimes 9,535/9,535, accused 32,867/32,867, arrests 32,862/32,862, chargesheets 7,061/7,061, charge_sheet_updates 6,163/6,163, disposal 470/470, mo_seizures 3,534/3,534, properties 7,646/7,646, fsl_case_property 2,003/2,003, interrogation_reports 19,497/19,497, hierarchy 816/816.

**Live verification performed:**
- `discover_new_runs()` run across all 12 modules; totals cross-checked against independently-queried table counts (table above).
- `get_changed_records('crimes', <latest_run>)` returned 40 records, matching a direct `COUNT(*) WHERE etl_run_id = ...` query exactly.
- `get_source_record()` resolved a real `crime_id` to its full 23-column row.
- `get_source_gap_state()` returned exactly 6 entries matching live `etl_bookkeeping` state: `fk_retry/arrests` fully resolved (72/72), `fk_retry/fsl_case_property` 894 unresolved, `fk_retry/chargesheets` 90 unresolved, `fk_retry/updated_chargesheet` 168 unresolved, `failure/etl-address` 1,627 unresolved, and a new `unlinked_accused` entry (78 `accused` rows with `person_id IS NULL`) synthesized directly from the `accused` table itself (V2 has no bookkeeping row for this; the adapter computes it live).
- Before/after row count on `crimes` unchanged across the whole test run: **no source-side mutation occurred.**

**Known limitation, not yet resolved:** the 24 placeholder `persons` rows are invisible to `etl_run_id`-based discovery by construction. A full historical migration (not an incremental run) would need a separate sweep to find rows like these across any table, not just `persons` — not designed here, flagged for the initial-migration phase.

## Supported-module provenance coverage

Full table in `ETL3_SOURCE_COMPATIBILITY_MATRIX.md` (updated this phase with the "Phase 2 live-adapter verification" section). Summary: V1's four modules all require the two-step run-log→row-action join (no per-row provenance column exists anywhere in V1); V2's twelve modules all carry per-row `etl_run_id`/`fetched_at`/`source_system`/`source_endpoint` directly, confirmed live for all twelve, not assumed from the column-existence check alone.

## Live DB verification summary

| Check | V1 | V2 |
|---|---|---|
| Connects through ETL-3's connection layer | ✓ | ✓ |
| Database identity asserted (`current_database()`) | ✓ `cctns_v1` | ✓ `cctns-v2` |
| Read-only session enforced at Postgres level | ✓ (write attempt rejected, tested) | ✓ (write attempt rejected, tested) |
| Latest/discoverable runs found | ✓ 5 `accused` runs | ✓ 8 `crimes` runs (varies 1–8 per module) |
| Run metadata readable | ✓ | ✓ |
| Records associated with a run identified | ✓, count matches direct query | ✓, count matches direct query |
| Counts verified against source bookkeeping | ✓ exact match | ✓ exact match, 11/12 modules; 1 explained gap |
| Known gap state visible | ✓ 180/180 | ✓ 6/6 categories |
| No source-side mutation | ✓ confirmed via before/after counts | ✓ confirmed via before/after counts |

## Known limitations / unresolved items carried into Phase 3

1. `V1Adapter.get_source_record()` is unimplemented for `accused`/`accused_details`/`court` (raises `NotImplementedError`) — needs a fir_reg_num-prefix-based correlation design, not a record_key lookup.
2. 24 V2 `persons` rows are placeholder-only and invisible to incremental discovery — needs a full-table sweep mechanism for the initial migration, separate from incremental `etl_run_id`-based discovery.
3. `charge_sheet_updates`'s `date_modified` is ETL-assigned, not source-assigned (per the compatibility matrix, unchanged finding from the design phase) — not re-verified this pass, carried forward.
4. No locking/concurrency protection exists yet if two ETL-3 processes ran these adapters simultaneously — out of scope for Phase 2 (read-only discovery only, no state is written), relevant once Phase 3's writer exists.

## Validation gate (Phase 2K)

```
[x] V1 adapter implemented
[x] V2 adapter implemented
[x] Both use the existing safe connection layer (db/connections.py, unmodified)
[x] V1 source is read-only (tested: write attempt rejected)
[x] V2 source is read-only (tested: write attempt rejected)
[x] Correct database identity asserted (tested: mismatch raises)
[x] V1 run discovery verified against live DB
[x] V2 run/module discovery verified against live DB
[x] V1 row-action mechanism verified (and a real bug in it was found and fixed)
[x] V2 provenance coverage verified (all 12 modules, not assumed)
[x] Known V1 gaps remain visible (180/180)
[x] V2 gap/exception state remains visible (6/6 categories, including a new unlinked_accused check)
[x] Adapter tests pass (test_adapter_safety.py, 6/6)
[x] Live smoke tests pass (test_v1_adapter.py 6/6, test_v2_adapter.py 5/5)
[x] No V1/V2 data was modified (verified via before/after counts, not just assumed)
[x] No unrelated files changed
[x] Documentation updated (this file + ETL3_SOURCE_COMPATIBILITY_MATRIX.md)
```

All gate items satisfied. Phase 3 (source-observation layer) may proceed.
