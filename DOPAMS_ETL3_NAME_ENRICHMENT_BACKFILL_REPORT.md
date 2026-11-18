# DOPAMS ETL3 — Standalone Historical Name-Enrichment Backfill Report

Covers PART B (the standalone backfill utility) and PART C (strict separation from the regular pipeline) of this phase. All numbers below are **real**, produced by actually running the backfill script in `--dry-run` mode against the live `dopams_cctns_v2` development database over a **read-only** Postgres session. **`--execute` was never run.** No row was written, no checkpoint was saved, no migration was applied.

---

## 1. Where it lives, and why there

The task suggested `etl3/scripts/backfill_name_enrichment.py`, but explicitly allowed adjusting the location to match this repository's actual structure. This repo has no `etl3/scripts/` package; every standalone, operator-invoked utility of this kind already lives at the top of `etl3/` with a `run_*.py` name (`run_ai_backfill.py`, `run_phase5_incremental.py`, `run_media_consolidation.py`, …). The backfill was placed at **`etl3/run_name_enrichment_backfill.py`** to match that existing convention exactly, including its CLI shape, signal-handling pattern, and `[name-backfill] ...` print-logging style — all mirrored from `run_ai_backfill.py`.

## 2. What it reuses — nothing reimplemented

| Need | Reused from | Not reimplemented |
|---|---|---|
| Per-person name cleaning | `etl3/enrichment/runner.py:build_person_row_safe()` → `etl3/enrichment/project.py:person_row()` → `names.py:clean_person_names()` | No cleaning rule, regex, or extraction function |
| Per-person failure isolation | `build_person_row_safe()` (the exact function added to the regular pipeline this same phase, §3 of the integration report) | No separate try/except logic |
| The actual write | `etl3/enrichment/persist.py:upsert_rows()` (unmodified) | No parallel insert/update/hash-compare logic |
| Multi-accused detection | `etl3/enrichment/multi_accused.py:detect_multi_accused()` | No parallel pack-detection regex |
| Multi-accused split execution | `etl3/enrichment/runner.py:_multi_accused_split()` (unmodified — the exact function already used in production, with 19 real split children to its name) | No parallel split-writing logic |
| V2 payload merge | `etl3/enrichment/runner.py:_v2_person_payload()` (factored out of `_person_enrichment()` this same phase so both callers share it) | No duplicate merge logic |

The backfill script's own code is CLI plumbing, batching/pagination, a checkpoint file, dry-run gating, and a reporting layer around these existing functions — never a second name-cleaning or split-detection implementation.

## 3. Identifying what needs backfilling — reusing `input_hash`, no new "version" column

`person_enrichment.input_hash` is already a deterministic, content-addressed hash of the enrichment output (`etl3/enrichment/project.py:_hash()`, unmodified). Comparing a freshly computed hash against the currently stored one for the same `person_id` already answers "does this row need updating" precisely — whether because the raw source data changed, or because the cleaning *logic* changed and now produces different output for the same input (exactly the situation this backfill exists for). No new "enrichment version" column or migration was needed for this. `classify_backfill_outcome()` in the script makes this explicit and distinguishes exactly the states the task required:

| State | Meaning | NULL-safe? |
|---|---|---|
| `not_yet_processed` | No `person_enrichment` row exists yet for this `person_id` | — |
| `stale_update` | A row exists, but its stored hash differs from a fresh computation | — |
| `already_current` | A row exists and the hash matches — genuinely nothing to do | — |
| `no_enrichable_data` | `person_row()` returned `None` — there is no name, given/surname/alias, domicile, or address signal at all for this person. **Not** treated as an error or as "incomplete"; a real person can legitimately have no alias, no known father's name, or (after this phase's invalid-literal detection) no identifiable name at all. | — |
| `failed` | An unexpected exception while building the row — isolated per-record, logged with `person_id`/`source_record_id`, never aborts the batch | — |

## 4. Dry-run results against the live database (2026-11-18)

```
python -m etl3.run_name_enrichment_backfill --dry-run --batch-size 2000
```

Connection opened via `connections.get_unified_connection(readonly=True)` — a Postgres-enforced read-only session, independent of application-level discipline (the same mechanism this codebase already uses for every other read-only tool). 28 batches, person_id-keyset-paginated, no OFFSET, no full-table load.

### 4.1 Person-level name enrichment

| Outcome | Count | % of 54,516 |
|---|---:|---:|
| **Total `persons_unified` records considered** | 54,516 | 100% |
| `already_current` (no change needed) | 53,799 | 98.68% |
| `stale_update` (would be rewritten with improved enrichment) | 708 | 1.30% |
| `no_enrichable_data` (legitimately nothing to enrich) | 9 | 0.02% |
| `not_yet_processed` (no row exists at all) | 0 | 0% |
| `failed` | 0 | 0% |

Of the 708 that would be updated:

| Sub-outcome | Count |
|---|---:|
| `name_identified_with_changes` — `cleaned_full_name` would actually change (the improved rules produce different, better text) | 514 |
| `name_identified_unchanged` — hash changed (e.g. a newly extracted relationship/role/alias field), but `cleaned_full_name` text itself is the same | 3 |
| `name_unidentified` — now correctly flagged `name_identified=False` (an invalid-literal, role-only, or relationship-only value previously stored as if it were a real name) | 191 |

**Zero `not_yet_processed` and only 9 `no_enrichable_data`** confirms the regular ETL3 pipeline has already kept `person_enrichment` fully populated for essentially every `persons_unified` row historically (9 ÷ 54,516 = 0.016% have no enrichable signal at all) — the backfill's real job here is exclusively the **708 stale rows** that would benefit from this phase's cleaning improvements, not catching up a backlog of never-processed records.

### 4.2 Multi-accused split backfill

```
51,096 un-split parent accused_unified rows considered
13 would currently match detect_multi_accused()'s high-confidence rule
139 flagged low-confidence (surfaced for review, never auto-split)
50,944 show no multi-person signal at all
```

**Of the 13 high-confidence matches, a separate targeted check (matching each against `accused_unified.split_superseded_at`) found 8 are already-split parents** (`_load_parent_accused_for_split()` does not exclude an already-superseded parent — recognized, not a bug: re-running `detect_multi_accused`/`build_split_rows` against it is a harmless idempotent no-op, since `split_child_id()`'s deterministic hash reproduces the identical child IDs and `upsert_rows()`/`UnifiedBatchWriter` then recognizes nothing changed). **The remaining 5 are genuinely new** — real packed records that would create new split occurrences on `--execute`:

| Parent `accused_id` | Crime | Raw text | Segments |
|---|---|---|---|
| `V1:5707b813b1318dbbf5419c662d08f520` | `2022022130811` | `1) Mohammed Layaq, 2). Mohammed Javeed` | Mohammed Layaq / Mohammed Javeed |
| `V1:1386e3a27feb24c0aee6c2a4d7d94a04` | `2032004140060` | `1.Jadav Prabhu s/o Narayana, 20 yrs, ST, 2.Jadav Vasantha d/o Raju, 27 yrs, both r/o Vasar of Kangti` | Jadav Prabhu s/o Narayana, 20 yrs, ST / Jadav Vasantha d/o Raju, 27 yrs |
| `V1:10a7a071d7ee724d0779193ea78b310e` | `2022011140267` | `A1) Malluvarasa Eswara Rao, A2.) Jajimoggala Kondala Rao` | Malluvarasa Eswara Rao / Jajimoggala Kondala Rao — **the new A-code detector's first live-data match** |
| `V1:ed28b7db2bd3989cd1d563adfe3040f3` | `2022022141092` | `A1. Bishan Singh, A2. Karnati Sharath Kumar, A3. Badari Sai Reddy` | Bishan Singh / Karnati Sharath Kumar / Badari Sai Reddy — a 3-person A-code pack, starting cleanly at `A1`, fully live-data-confirmed |
| `6a35a0541103855a721a342c` | `6a359f414d1e611f530f007e` | `1). Divi Sampath, 2). Syed Ameer Ali, 3). Bebri koushal Naik, 4). Kattimani Mandira` | 4 segments — the **`"N)."` punctuation fix's** first live-data match |

Each of these three newly-supported shapes (A-code starting at 1, and the `"N)."` punctuation variant) was specifically added this phase and is now confirmed working against real, live, previously-`"none"` production records — not just the Excel-derived examples in the earlier validation report.

Representative low-confidence (flagged, never auto-split) examples from the live data:
- `"Ch.Edukondalu, P.Raju and S.Simhadri"` — unnumbered 3-way candidate, correctly left for review.
- `"A-1. Mangali Shivraj A-2.Md. Masoodkhan both r/o Zaheerabad"` — `weak_space_boundary`, correctly capped at low confidence rather than guessed at.
- `"mamadivari and five others Chiluka Narsiah"` — a narrative, not a clean list; correctly not split.

## 5. CLI reference (as actually implemented)

```
python -m etl3.run_name_enrichment_backfill --dry-run
python -m etl3.run_name_enrichment_backfill --dry-run --limit 100
python -m etl3.run_name_enrichment_backfill --execute --confirm --batch-size 200
python -m etl3.run_name_enrichment_backfill --execute --confirm --resume
python -m etl3.run_name_enrichment_backfill --dry-run --skip-multi-accused
python -m etl3.run_name_enrichment_backfill --dry-run --skip-persons
python -m etl3.run_name_enrichment_backfill --execute --confirm --start-id V2:12345
```

| Flag | Purpose |
|---|---|
| `--dry-run` / `--execute` | Mutually exclusive, one required |
| `--confirm` | Required alongside `--execute` — `--execute` alone is refused with `SystemExit` **before any database connection is opened** (verified: `test_execute_without_confirm_refused_before_any_db_connection` monkey-patches `get_unified_connection` to raise if called, then confirms it never is) |
| `--batch-size N` | Default 500 (the dry-run above used 2000) |
| `--limit N` | Caps total person records considered, for a bounded validation run |
| `--resume` | Reads the last saved checkpoint and continues after it (no effect combined with `--dry-run`, which never saves one) |
| `--start-id PERSON_ID` | Explicit override, takes precedence over `--resume` |
| `--checkpoint-file PATH` | Default `etl3/.backfill_name_enrichment_checkpoint.json` (git-ignored — see `.gitignore`) |
| `--skip-persons` / `--skip-multi-accused` | Run either half independently |
| `--report-file PATH` | Also dump the full JSON stats to a file |

## 6. Safety mechanisms (implemented and verified)

- **Dry-run cannot write, structurally, not just by convention:** the connection itself is opened read-only (`get_unified_connection(readonly=True)`) — the same Postgres-session-level enforcement this codebase already relies on elsewhere, so a bug in the dry-run code path could not cause a write even if it tried.
- **Bounded reads:** `persons_unified` is paginated by `person_id` keyset (`WHERE person_id > :cursor ORDER BY person_id LIMIT :batch_size`), never `OFFSET`, never a full-table load. Confirmed by the dry-run's own batch log: exactly `ceil(54516 / 2000) = 28` batches, strictly increasing cursor values.
- **Transaction-safe, per-batch commits:** each batch's writes (in `--execute`) commit together or not at all; `--dry-run` never calls `conn.commit()` for a write (only the connection's own read-only session exists).
- **Checkpointing after success only:** the checkpoint file is written immediately after a batch's commit succeeds, never before, and never in dry-run. An interruption (Ctrl-C / SIGTERM, caught the same way `run_ai_backfill.py` already does) stops after the current batch finishes — the next `--resume` starts exactly at the next unprocessed `person_id`.
- **Retry for transient failures only:** `_retry_commit()` retries up to 3 times (short backoff) on `psycopg2.OperationalError`/`SerializationFailure` specifically; any other exception is raised immediately, never masked as "transient."
- **No duplicate split occurrences:** `--execute`'s multi-accused step calls the existing, unmodified `_multi_accused_split()`, whose own `split_child_id()` (deterministic hash of parent + position + exact text) and `diff_superseded_children()` already guarantee this — confirmed by this phase's dry-run finding exactly the 8 already-split parents and correctly not double-counting them as "new."
- **No destructive updates:** nothing in this script issues a `DELETE`; `supersede_children`/`supersede_parent_if_unset` (called only inside the unmodified `_multi_accused_split`) only ever set a timestamp column, never remove a row.
- **No canonical identity changes, no face-matching/dedup changes:** `etl3/identity/person_matching.py` is never imported by this script.

## 7. Separation from the regular ETL3 pipeline — proof

```
grep -rn "run_name_enrichment_backfill" etl3/ --include="*.py" | grep -v "tests/test_name_enrichment_backfill.py"
```
→ **zero matches** outside the script's own file and its own test. It is not imported by `etl3/enrichment/runner.py`, not listed in `run_enrichment()`'s `steps` tuple, not referenced by `run_daily_pipeline.py`, `run_phase5_incremental.py`, `run_enrichment.py`, or any scheduler/cron configuration in this repository. It has its own `if __name__ == "__main__":` entrypoint and is invoked only via `python -m etl3.run_name_enrichment_backfill`, by an operator, by name.

Conversely, the regular pipeline's own files were checked for any new reference to this script: none exist. The only things shared between the two paths are the pre-existing, unmodified enrichment functions listed in §2 — which is by design (reuse, not duplication), not a coupling that could cause the backfill to run automatically.

## 8. Operational instructions (for when this is approved to run for real)

1. Review `etl3/migrations/022_person_enrichment_identity_fields.sql`; if the identity-metadata fields are wanted persisted, apply it (`python etl3/migrations/run_migration.py 022_person_enrichment_identity_fields.sql`) and set `ETL3_NAME_IDENTITY_FIELDS_ENABLED=1`. Otherwise skip this — the backfill still fully updates `cleaned_full_name`/`cleaned_alias`/`relation_type`/`cleaned_relative_name` either way.
2. Re-run `--dry-run` close to execution time to get fresh numbers (source data changes daily).
3. `python -m etl3.run_name_enrichment_backfill --execute --confirm --batch-size 500` for the name-enrichment half. Expect ~708 rows rewritten (per §4.1; this will drift as new data arrives), each inside its own committed batch.
4. If multi-accused splitting is also wanted, set `ETL3_MULTI_ACCUSED_SPLIT_ENABLED=1` before running — otherwise this half stays a documented no-op (§6 of this report, same gate the regular pipeline uses). Expect 5 new split parents → 10+ new `persons_unified`/`accused_unified` child rows (per §4.2), plus the 8 already-split parents reconfirmed as a no-op.
5. If interrupted, re-run with `--resume` added.
6. Check `etl3/.backfill_name_enrichment_checkpoint.json` after an interrupted run to see exactly where it stopped; it is deleted automatically on a full, uninterrupted completion.

**This task did not execute step 3 or step 4.** Only the read-only `--dry-run` was run, producing the numbers in this report.
