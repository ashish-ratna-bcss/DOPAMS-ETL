# DOPAMS ETL3 — Regular Pipeline Integration Report

Covers PART A of this phase: making the regular ETL3 pipeline consistently use the latest name-cleaning/multi-accused enrichment for every ingested and updated record. PART B (the standalone historical backfill) and PART C (separation proof) are covered in `DOPAMS_ETL3_NAME_ENRICHMENT_BACKFILL_REPORT.md`.

---

## 1. Key finding: most of this was already true by construction

The previous implementation phase extended `etl3/enrichment/names.py` and `etl3/enrichment/multi_accused.py` **in place** — it did not create a second, parallel cleaning module. `etl3/enrichment/runner.py`'s regular pipeline (`run_enrichment()` → `_person_enrichment()` → `etl3/enrichment/project.py:person_row()` → `names.py:clean_person_names()`, and `run_enrichment()` → `_multi_accused_split()` → `multi_accused.py:detect_multi_accused()`) calls those exact functions. **There is only one name-cleaning implementation in this codebase, and the regular pipeline already calls it on every run** — confirmed by reading the current, unmodified call graph, not assumed from the prior report.

What genuinely needed integration work in this phase:

1. The five new identity-metadata fields (`name_identified`, `unidentified_reason`, `extracted_role_hint`, `extracted_nationality_hint`, `review_flags`) were computed by `person_row()` but never reached `_person_enrichment()`'s database write — wired now, feature-flagged (§2).
2. `_person_enrichment()`'s loop had no per-record failure isolation — one bad record could abort the whole batch. Fixed (§3).
3. Regression tests proving the regular pipeline (not just the pure functions in isolation) uses the latest logic end to end, for every requirement in the task's §5 — added (§4).

No frontend, backend, GraphQL, `be_read.*` view, deduplication threshold, or face-matching threshold was touched, per the explicit scope restriction.

---

## 2. Identity-metadata wiring (flag-gated, same convention as migration 018)

| Component | Change |
|---|---|
| `etl3/enrichment/project.py:person_row()` | New parameter `persist_identity_metadata` (default `False`). When `True`, the 5 identity fields are added to the row **before** `input_hash` is computed, so a genuine future change to one of them is detected as a change. When `False` (today's default everywhere), they are still computed and returned on every call, just excluded from the hash. |
| `etl3/enrichment/runner.py:_name_identity_settings()` | New. Reads `ETL3_NAME_IDENTITY_FIELDS_ENABLED` (default `"0"`) — the exact pattern `_multi_accused_settings()`/`ETL3_MULTI_ACCUSED_SPLIT_ENABLED` already established for migration 018. |
| `etl3/enrichment/runner.py:_person_enrichment()` | Reads the flag once per run, passes it to every `person_row()` call, and only appends the 5 identity columns to the `upsert_rows()` column list when the flag is on. |
| `etl3/migrations/022_person_enrichment_identity_fields.sql` | Updated (still **not applied**) — `review_flags` changed from `TEXT[]` to `JSONB` so `persist.py`'s existing `_db_value()` (which already wraps any Python list in `Json()` for every other JSONB column, e.g. `address_resolution`) handles it correctly with zero changes to that shared helper. Exact apply command and two-step activation sequence documented in the migration file's own header. |

**Why gate the hash by the same flag, not just the column list:** if the 5 fields were hashed unconditionally while the flag is off, every existing `person_enrichment` row's freshly computed hash would differ from what's stored (since the stored hash was computed without these keys) — `persist.upsert_rows()` would treat every single row as "changed" and force a pointless mass rewrite + `change_log` entry for every person the moment this code shipped, even though no actually-persisted column changed. Gating both together keeps them permanently in lockstep: the hash only reflects these fields once they are actually being written.

Verified (`test_regular_etl_identity_metadata_flag_gates_hash_only`): same input, `persist_identity_metadata=False` vs `True`, produces a different `input_hash` — proving the flag genuinely controls what gets hashed, not just a documentation claim.

**Activation, once migration 022 is reviewed and applied:** set `ETL3_NAME_IDENTITY_FIELDS_ENABLED=1` for the next ETL3 run. No code change needed — the wiring is already complete and already tested; only the flag and the migration are pending, both deliberately left for separate approval per this task's constraints.

---

## 3. Per-record error isolation

**Before:** `_person_enrichment()`'s loop called `person_row()` directly; an unexpected exception for one malformed `persons_unified` row would propagate uncaught out of `_person_enrichment()`, through `run_enrichment()`'s `try/except`, rolling back the whole "persons" stage (everything since the last stage's commit) and marking the entire enrichment run `'failed'` — not corrupting data, but also not processing any OTHER person in that run.

**After:** the per-record build step was factored into a new, directly unit-testable pure function, `build_person_row_safe()` in `runner.py`:

```python
def build_person_row_safe(source_system, person_id, record_id, payload, geo_kb=None,
                          persist_identity_metadata=False):
    """person_row(), isolated: returns (row_or_None, failure_or_None), never raises."""
```

`_person_enrichment()`'s loop now calls this instead of `person_row()` directly. A failure is caught, logged with its own `person_id`/`source_record_id`/error message, counted, and the loop continues — every other record in the batch is still processed and persisted normally. The stage's returned stats dict now includes `"failed": <count>` and `"failed_records": [...]` (first 20), so a failure is visible in `enrichment_run_log.stats` without silently vanishing.

Verified (`test_regular_etl_failed_record_isolated_others_continue`): a 3-record batch with one deliberately malformed (`None`) payload in the middle produces exactly 2 successful rows and 1 isolated failure, naming the correct `person_id`.

This same `build_person_row_safe()` function is reused — not reimplemented — by the standalone backfill script (`etl3/run_name_enrichment_backfill.py`), so both execution paths get identical isolation behaviour from one source.

---

## 4. Regression tests (task §5, all 10 items addressed)

Added to `etl3/tests/test_enrichment.py` (8 new test functions, all passing):

| # | Requirement | Test | How |
|---|---|---|---|
| 1 | New records use the latest name-cleaning logic | `test_regular_etl_person_row_uses_latest_name_cleaning_end_to_end` | Calls `person_row()` (the actual function the pipeline calls) with a spelled-out-alias and an invalid-literal input, confirms both newly-added capabilities reach the enrichment row |
| 2 | Updated records are re-enriched correctly | `test_regular_etl_reenriches_on_name_change` | Same `person_id`, two different `full_name` values → different `cleaned_full_name` AND different `input_hash` |
| 3 | Existing clean records remain stable | `test_regular_etl_stable_hash_for_unchanged_input` | Same input called twice → byte-identical output and `input_hash` |
| 4 | Multi-accused records generate separate occurrences | `test_regular_etl_multi_accused_generates_separate_linked_occurrences` | End to end through `detect_multi_accused()` → `build_split_rows()` for an A-code pack (a pattern newly supported this phase, not a re-check of the already-covered N./N) case) |
| 5 | All generated occurrences link to the correct crime | *(same test as #4)* | Asserts `{a["crime_id"] for a in accused_rows} == {"FIR9"}` |
| 6 | Aliases are not treated as separate accused | `test_regular_etl_aliases_not_treated_as_separate_accused` | `detect_multi_accused()` on `@`-chain and spelled-out-`Alias`-chain inputs stays `"none"`, zero segments |
| 7 | Ambiguous records remain unchanged | `test_regular_etl_ambiguous_records_remain_unchanged` | `build_split_rows()` on a genuinely ambiguous comma pair produces nothing |
| 8 | Repeated ETL runs do not create duplicates | *(same mechanism as #3, plus existing, unmodified `persist.upsert_rows()` hash-compare and `accused_split.py`'s deterministic `split_child_id()`)* | Stable-hash test + the pre-existing, unmodified idempotency tests in `test_multi_accused.py` (`test_build_split_rows_idempotent_rerun_same_ids`) |
| 9 | Failed records can be retried | `test_regular_etl_failed_record_isolated_others_continue` | See §3 |
| 10 | Existing ETL stages continue to work | Full `etl3/tests/` suite re-run | 24 other test files run before and after this phase's changes; zero new failures beyond the one pre-existing, unrelated AI-retry timing test confirmed present on the unmodified codebase too |

**Deduplication/matching thresholds:** not touched. Confirmed by `grep`: nothing in this phase's diff touches `etl3/identity/person_matching.py`.

---

## 5. Verification of "every relevant ingestion path"

| Path | Uses the same enrichment? |
|---|---|
| Initial ingestion (`etl3/run_phase3_initial_load.py` → merger → `persons_unified`/`accused_unified`) | Yes — enrichment is a separate, later stage (`run_enrichment()`) that reads whatever is currently in `persons_unified`/`accused_unified` regardless of how it got there; it does not care whether a row arrived via initial load or incremental sync. |
| Incremental ingestion (`etl3/run_phase5_incremental.py`, `etl3/run_daily_pipeline.py`) | Same reasoning — these populate/update `persons_unified`/`accused_unified`; `run_enrichment()` (called from `etl3/run_enrichment.py` and wired into the daily pipeline) re-scans the full current table every time, so an incrementally-updated row gets the identical treatment as one from initial load. |
| Updated source records | A changed `full_name` produces a different `input_hash` (confirmed, test #2 above) → `persist.upsert_rows()` (unmodified) detects the change and rewrites that row's `person_enrichment` entry, logging a `change_log` row with `change_classification='business_change'`. |
| Records reprocessed after an enrichment failure | Since a failure is now isolated per-record (§3) rather than aborting the batch, the failed record simply has no `person_enrichment` row written for it that run; the NEXT run's full re-scan picks it up again automatically (no special "retry queue" needed — the regular pipeline's own unconditional full re-scan IS the retry mechanism, already idempotent via the hash-compare). |

No second ingestion path exists that calls a different or older cleaning function — confirmed by `grep -rn "clean_person_names\|detect_multi_accused" etl3/` returning only the one production call site per function (`project.py`/`runner.py` and `accused_split.py` respectively) plus their tests.

---

## 6. What was deliberately NOT changed

- `etl3/identity/person_matching.py` — untouched, per explicit instruction not to change deduplication logic.
- Face-matching thresholds — none exist in `etl3/`; nothing to change.
- `be_read.*` views, any frontend/backend/GraphQL file — untouched, per the explicit scope restriction (same as the previous two phases).
- Migrations 018 and 022 — neither was applied by this phase. 018 was already applied in a prior session (confirmed via the earlier read-only inspection, documented in the validation report); 022 remains prepared-only.
- `run_enrichment()`'s stage list, commit boundaries, and overall orchestration shape — unchanged. The new error isolation lives entirely inside `_person_enrichment()`'s own loop; it does not change how `run_enrichment()` sequences or commits its stages.
