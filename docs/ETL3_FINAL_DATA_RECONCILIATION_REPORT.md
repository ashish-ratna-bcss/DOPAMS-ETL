# Final Data Reconciliation Report

## EXECUTED (2026-10-24, later the same day)

Everything this report identified as "blocked pending approval" was
subsequently, explicitly authorized and executed: the 27-row domicile
backfill, migrations 018 and 020, and multi-accused split activation.
Full execution evidence is in the "Production execution evidence"
section appended at the end of this file, and in the equivalent sections
now added to `ETL3_DOMICILE_RECONCILIATION_REPORT.md` and
`ETL3_MULTI_ACCUSED_SPLIT_READINESS_REPORT.md`. The body below is the
pre-execution analysis this report originally delivered and is preserved
unchanged as the record of what was planned and why — every number in it
matched the eventual execution exactly.

---

Synthesizes every workstream's current code, deployment, and existing-data
state as of 2026-10-24, and resolves Step 6's reconciliation requirement:
which tables/fields are actually affected by the five finalized fixes, what
a full dry-run recomputation finds, which write strategy is safe, and what
remains blocked pending approval. **No production write was performed in
this phase.** Personal identifiers are redacted throughout; real values
are retained only in protected server-side diagnostic logs, never in this
file.

## A. Code and deployment versions (re-verified fresh, not assumed)

| Commit | What | Local | Origin | Server (`dopams-etl`) HEAD-reachable |
|---|---|---|---|---|
| `e3c9ff0` | Deterministic address matching | ✅ | ✅ | ✅ |
| `d57f727` | Name cleanup/deduplication | ✅ | ✅ | ✅ |
| `c4516a0` | Nationality false-positive correction | ✅ | ✅ | ✅ |
| `1c5da12` | Multi-accused detector correction | ✅ | ✅ | ✅ |
| `1ef60f8` | Domicile precedence + Muscat/Oman | ✅ | ✅ | ✅ |

Server HEAD = origin HEAD = local HEAD = `1ef60f8`, confirmed by a fresh
`git fetch` + log comparison this session. The server's working tree has
5 modified tracked files and 63 untracked files, all pre-existing,
unrelated AI-backfill/operational work (same 5 files as every prior
session: `etl3/config/settings.py`, `etl3/diagnostics/_limited_smoke_report_ro.py`,
`etl3/diagnostics/_start_ai_backfill.sh`, `etl3/enrichment/ai.py`,
`etl3/enrichment/runner.py`) — untouched throughout this entire
investigation.

## B. Test evidence (re-run this session, not reused from memory)

- `etl3/tests/test_enrichment.py`: full suite passes on deployed code
  except the pre-existing, documented, unrelated AI-retry assertion
  failure.
- `etl3/tests/test_multi_accused.py`: all 34 assertions pass on deployed
  code, including both regression cases for the marker-boundary fix.
- `test_geography_and_domicile` (the one domicile test in
  `test_kb_local.py`): passes against the live KB.
  `test_pg_trgm_and_lookups` in that same file was **not** run this
  session either — it writes/deletes a row in `kb.drug_categories` on
  `dopams_cctns_v2`, exactly what every phase of this investigation has
  been told to avoid running against production.

## C. Migration and feature-flag status (fresh read-only check)

```
Applied: 001 through 017.
NOT applied: 018 (multi-accused split columns + accused_split_audit)
NOT applied: 020 (be_read.accused superseded-row filter)
ETL3_MULTI_ACCUSED_SPLIT_ENABLED: not set anywhere in the server's .env
  or process environment.
```

No `multi_accused_occurrences` table exists under that name anywhere —
confirmed again; that name is obsolete terminology from migration 018's
original, superseded draft (§ already documented in the readiness
report).

**Operational context newly confirmed this session:** a cron job
(`run_daily_when_sources_finished.py`, every 15 minutes) automatically
triggers `etl3/run_phase5_incremental.py` once per completed V1+V2 source
sync slot — this is pre-existing production automation, not something
enabled by this investigation. It performs an **incremental** capture
(`capture_incremental`), i.e. it only processes source rows with a *new*
observation since the last run — it does not re-walk existing,
unchanged rows. `ETL3_AI_ENABLED=True`, mode `backfill`, also pre-existing.
This matters for reconciliation: none of the 27+6 domicile-flagged rows
have a second source observation in `change_log` (each shows exactly one
`initial_observation`, never revised) — so the automatic incremental
pipeline does **not** and will **not** pick these up on its own. They
require the explicit backfill this report evaluates; nothing will
silently self-correct them.

## D. Domicile dry-run impact (re-run twice independently, identical both times)

```
person_enrichment rows: 53182 (unchanged from every prior count)
persons_unified rows:   53197 (unchanged)

nationality-safe candidates (safe to apply — only nationality/
  domicile_classification/domicile_method differ, every other column
  byte-identical): 27
  - 7 are genuine domicile-label corrections (5 -> inter state,
    1 -> native state, 1 -> null)
  - 20 are nationality-text-only canonicalization (Yeman/Yemeni ->
    Yemen, Tanzania canonicalization, Federation of Nigeria ->
    Nigeria, Muscat -> Oman, Russian Federation -> fallback,
    India-echo removed where address alone already decides) with the
    domicile label unchanged

other changes (address_resolution also differs — excluded from this
  backfill by design, not applied, not newly investigated beyond the
  one record already closed out in the source-API investigation): 6
  - 5 are a previously-null slot now resolving (benign improvement)
  - 1 is the Jammu & Kashmir/Srinagar case, independently closed with
    `SOURCE API CONFIRMS CURRENT RESOLUTION` in
    docs/ETL3_ADDRESS_RESOLUTION_SOURCE_API_INVESTIGATION.md,
    re-verified unchanged this session

unchanged: 53149
```

Full per-row before/after detail (with real, unredacted `person_id`
values) is in the protected server log
`/tmp/domicile_rerun_step3.log` on `dopams-etl`, produced by
`etl3/diagnostics/_reprocess_nationality_fix_verified.py` run without
`--commit`. Category breakdown with redacted identifiers is in
`docs/ETL3_DOMICILE_RECONCILIATION_REPORT.md`.

## E. Multi-accused candidate inventory (re-run fresh, identical to prior)

```
total parent candidates:        49795
none (nothing to split):        49660
HIGH confidence (eligible):          8
LOW confidence (never split):      127
expected new rows if enabled:       19
```

Isolated-database test state (`dopams_cctns_v2_multiaccused_test`)
re-checked and confirmed unchanged since it was built:
`accused_unified` = 11 rows, `be_read.accused` = 8 (post-migration-020
view). No production multi-accused data exists anywhere — migration 018
has never been applied to `dopams_cctns_v2`, so no split row, no
`accused_split_audit` row, no superseded flag exists in production.

## F. Exact tables/fields affected by each fix (mapped from the real writer code, not assumed)

| Fix | Writes to | Evidence it owns only these fields |
|---|---|---|
| `e3c9ff0` deterministic address matching | `person_enrichment.address_resolution` (mandal/village tie-break only) | `_fuzzy_district`/`_fuzzy_mandal`/`_fuzzy_village_mandal` are the only functions changed; nothing else in `person_row()`'s output depends on them |
| `d57f727` name cleanup | `person_enrichment.raw_full_name`, `cleaned_full_name`, `cleaned_given_name`, `cleaned_alias`, `cleaned_relative_name` | `clean_person_names()`/`collapse_exact_repetition()` feed only these five columns in `person_row()` |
| `c4516a0` nationality false-positive (name-embedded) | `person_enrichment.nationality`, `domicile_classification`, `domicile_method` (method `name_country` specifically) | `resolve_name_country()`'s only caller inside `person_row()` is the `foreign_name` branch |
| `1c5da12` multi-accused detector | **Nothing in any existing table.** Detection-only; its only caller, `_multi_accused_split()`, is feature-flagged off and has never run against production | Confirmed via `_multi_accused_split`'s own early return when the flag is unset, and via the migration-018 dependency that makes the feature unrunnable regardless |
| `1ef60f8` domicile precedence + Muscat/Oman | `person_enrichment.nationality`, `domicile_classification`, `domicile_method` | Same three columns as `c4516a0`, via the `classify_domicile()`/`_classify_address_slot()` precedence logic and the new `NATIONALITY_TO_COUNTRY` entries |

**`accused_enrichment` is unaffected by every one of these five fixes** —
confirmed by reading `etl3/enrichment/accused_facts.py`'s imports: it
uses only `classify_accused_type` and `resolve_is_ccl` from `rules.py`
(drug-role text classification and CCL/age logic), and nothing from
`address.py` at all. None of the five fixes touch either of those two
functions. No dry-run against `accused_enrichment` was run, because
there is nothing in it that any of these fixes could have changed —
running one would only re-confirm a fact already established by reading
the code, at the cost of another multi-minute full-table pass for no
new information.

**`persons_unified`/`accused_unified` are unaffected** — these are
merger-owned tables; ETL3 enrichment never writes to them (confirmed
architectural invariant, re-confirmed in every prior session's code
reading). The only code that would add rows to them from this
investigation's work is the multi-accused split writer, which — per §C
above, re-confirmed fresh — has never run against production. Nothing
to reconcile.

## G. Reconciliation results, by table

### `person_enrichment`

| | Count |
|---|---|
| Before row count | 53182 |
| After row count | 53182 (no write performed) |
| Rows recomputed (full table, not sampled) | 53182 |
| Rows already correct | 53149 |
| Rows requiring correction (deterministic, safe) | 27 |
| Rows requiring separate investigation (non-deterministic/ambiguous source) | 6 (1 already closed, 5 benign) |
| Rows inserted | 0 |
| Rows superseded | 0 (not applicable to this table) |
| Missing/orphaned rows | 0 (reconfirmed: every `persons_unified` row that should have a `person_enrichment` row has one, and vice versa) |
| Remaining unexplained mismatches | **0** — all 33 non-matching rows (27 + 6) have a full, reproducible explanation in this report and its linked evidence |
| Hash-match rate | 53149/53182 = 99.94% exact; the remaining 33 have a fully understood, reproducible cause, not an unexplained hash drift |
| Unrelated columns verified unchanged | Yes — the dry-run script's `STABLE_KEYS` check (surname, relation_type, gender_source, all four name fields, `address_resolution`) confirms byte-identical values for every one of the 27 "safe" rows; this was independently re-verified a second time with identical results |
| Audit-log evidence | N/A this phase — no write occurred, so no new `change_log`/`enrichment_run_log` rows exist from this report. The writer path that *would* be used (`upsert_rows()`) is the same one already proven correct for the prior 1-row address fix and 18-row nationality fix |
| Rollback readiness | Documented in `ETL3_DOMICILE_RECONCILIATION_REPORT.md` — revert via `change_log`'s logged `old_value`s, same mechanism as every other enrichment correction in this project |

### `accused_enrichment`, `persons_unified`, `accused_unified`

| | Count |
|---|---|
| Before row count | Unaffected — no dry-run needed (§F) |
| After row count | Unchanged (no write performed; none proposed) |
| Remaining mismatches | N/A — out of scope for all five fixes, confirmed by code reading |

### Multi-accused occurrence/supersession tables (`accused_split_audit`, split columns on `accused_unified`)

Do not exist in production (migration 018 unapplied). Nothing to
reconcile. Production verification is explicitly deferred until
migrations 018/020 and the feature flag are separately approved — per
this task's own instruction, enrichment reconciliation is not treated as
authorization to split identities.

## H. Write strategy decision (Step 6C) — targeted updates, not truncate/rebuild

**Decision: targeted, transactional updates via the existing production
writer (`upsert_rows()`), if and when the domicile backfill is approved.
A truncate/rebuild was considered and rejected.**

Reasoning:

- The correction set is 27 rows out of 53,182 (0.05%). Truncating
  `person_enrichment` to "rebuild" it would discard 53,149 already-correct,
  already-audited rows' `change_log` continuity for zero benefit.
- `person_enrichment` is not a pure, side-effect-free derived cache in
  the sense the truncate-safety checklist requires without qualification
  — its `change_log` entries and `input_hash` history are themselves
  part of this project's audit trail, and a truncate would need its own
  staging-and-swap strategy to avoid losing that history, which is pure
  added risk for a correction this small and this well-understood.
- `upsert_rows()` is already hash-gated (only writes a row whose computed
  `input_hash` differs from the stored one) and already logs every
  changed field to `change_log` with `change_classification` —
  exactly the targeted-update mechanism this task's own Step 6C prefers,
  and the same mechanism already used, successfully, for two prior
  corrections in this project (the 1-row address fix, the 18-row
  nationality fix).
- None of the truncate-safety checklist's preconditions were evaluated
  as satisfied, because targeted update is trivially safer and strictly
  preferred for a correction this size — a full safety sign-off for
  truncation was not pursued, consistent with "use a safer targeted
  upsert/update strategy instead" whenever any precondition is unmet or,
  as here, simply unnecessary to invoke.

**No write — targeted or truncate — was performed in this phase.** The
27-row correction remains gated by the explicit approval this report
asks for below; this section states the method that would be used, not
an action taken.

## I. Approved writes performed this phase

**None.** Every number in this report comes from a read-only or
rolled-back-on-exit computation. No `--commit` flag was ever passed. No
migration was applied. No feature flag was changed.

## J. Operations blocked pending approval

1. The 27-row domicile/nationality backfill (§D, §H) — blocked by this
   task's own Step 3 gate: "Do not execute the production backfill until
   the exact candidate set, expected field changes, and rollback
   procedure have been presented for explicit approval." All three are
   now presented, twice independently reproduced. **Awaiting approval.**
2. Applying migration 018 to `dopams_cctns_v2`.
3. Applying migration 020 to `dopams_cctns_v2` (depends on 018).
4. Setting `ETL3_MULTI_ACCUSED_SPLIT_ENABLED=1` anywhere in production.
5. Any production multi-accused split, by any entry point.

## K. Backup and rollback evidence

- **Domicile backfill (if approved):** rollback is reading `change_log`
  for the 27 affected `person_id`s and restoring each logged `old_value`
  — the same mechanism every prior enrichment correction in this project
  already relies on; no separate database backup is needed because
  nothing destructive is proposed.
- **Migrations 018/020 (if approved):** both are purely additive (new
  nullable columns, one new table, one view redefinition); rollback is
  dropping the added columns/table or reverting the view to its
  migration-006/019 definition. Neither alters or drops any existing
  column.
- **No truncate or table rebuild is proposed anywhere in this report**,
  so no snapshot/restore procedure beyond the above is required.

## L. Remaining risks and required actions

1. Obtain explicit approval for the 27-row domicile backfill, or an
   explicit decision not to apply it.
2. Obtain explicit approval for migrations 018 and 020, and for setting
   the multi-accused feature flag, or an explicit decision to keep the
   feature dormant.
3. No other risk was identified this session: code is deployed and
   tested, the server's unrelated operational work remains untouched,
   and every number in this report was independently reproduced at
   least twice.

## Six-step status

| Step | Status |
|---|---|
| 1. Finalize reports, git state, safe artifacts | Reports reviewed and current; no secrets/PII found; git state clean and fully reviewed — **staged for commit, not yet committed, pending your review of the exact diff** |
| 2. Close address-resolution investigation | **VERIFIED** — re-rechecked fresh this session; source text, resolver determinism, and KB mapping all unchanged and reconfirmed |
| 3. Domicile/nationality backfill review | **MISMATCHES FOUND, backfill prepared** — 27-row candidate set reproduced twice; execution **BLOCKED** pending explicit approval |
| 4. Multi-accused splitting and downstream compatibility | **VERIFIED** (code, isolated-DB tests, downstream audit) / **BLOCKED** (production migrations/flag) |
| 5. Deployment and operational validation | **VERIFIED** across all five fixes' code/deployment; migrations 018/020 **NOT VERIFIED as applied** (confirmed not applied) |
| 6. Reconcile existing data using finalized logic | **MISMATCHES FOUND** (the same 27+6, fully explained) for `person_enrichment`; **VERIFIED unaffected** for `accused_enrichment`/`persons_unified`/`accused_unified`; write strategy decided (targeted update) but **not executed — BLOCKED pending approval** |

## Final GO/NO-GO

**NO-GO on any further production write.** Everything that can be
verified without writing to production has been verified, twice where
feasible. Two concrete, fully-evidenced actions are ready for a decision:
the 27-row domicile backfill, and the migration 018/020 + feature-flag
activation for multi-accused splitting. Neither has been executed.
Neither should be inferred as approved from the instruction to complete
this task.

---

## Production execution evidence (2026-10-24, later the same day)

Explicit authorization was given for all three actions. Executed in
order, each gated on the previous step's verification:

1. **Preflight:** confirmed `current_database() = dopams_cctns_v2`,
   confirmed `current_user` has superuser privilege (sufficient for the
   DDL in 018/020), confirmed HEAD still `1ef60f8` with the same 5
   unrelated modified files untouched, recorded baseline counts
   (`person_enrichment` 53182, `persons_unified` 53197, `accused_unified`
   50503), took and verified a full `pg_dump` backup (388 TOC entries,
   242 MB, restorable) before any write.
2. **Domicile backfill:** executed via
   `_reprocess_nationality_fix_verified.py --commit`. Exactly 27 rows
   updated, 0 inserted, all other 53,155 rows confirmed byte-identical
   before commit. Independently re-verified after commit from a separate
   connection: row count unchanged, 27 matching `change_log` rows, spot
   checks match the manifest exactly.
3. **Migration 018:** applied via `run_migration.py`. Verified: all 4
   new `accused_unified` columns and 2 new `persons_unified` columns
   present and nullable; `accused_split_audit` table created; all 6
   expected indexes present; `accused_unified`/`persons_unified` row
   counts unchanged; 0 rows had `derived_from_*` set immediately after
   (clean state, nothing split yet).
4. **Migration 020:** applied via `run_migration.py`. Verified:
   `be_read.accused` row count matched `accused_unified` exactly
   immediately after (true no-op pre-split, as designed); 2 new columns
   present; `be_read.person` confirmed completely unchanged (same row
   count, same 18 columns).
5. **Multi-accused split, run 1:** executed via a new, narrowly-scoped
   harness that calls the real, unmodified
   `etl3.enrichment.runner._multi_accused_split(conn, run_id)` directly
   — not the full `run_enrichment()` (which would have also triggered
   the other 11 unrelated stages, including AI extraction, outside this
   authorization's scope). Result: 8 parents split, 19 rows inserted into
   each of `persons_unified`/`accused_unified`/`accused_split_audit` —
   an exact match to the pre-execution dry-run inventory (8 high, 19
   expected new rows), zero discrepancy.
6. **Multi-accused split, run 2 (idempotency proof):** same harness,
   immediately after. Result: 0 inserted, 0 updated, 19 unchanged across
   all three tables — zero duplicates on rerun, confirmed both by the
   writer's own stats and an independent fresh count.
7. **Feature flag:** `_load_accused_by_crime()`'s own split-awareness
   turned out to be gated behind the same flag this investigation set
   only in-process for the split harness. Re-checked, confirmed the
   AI/enrichment roster builder would otherwise keep treating the
   now-split parents as single, un-split accused indefinitely.
   `ETL3_MULTI_ACCUSED_SPLIT_ENABLED=1` was appended to
   `etl3/config/.env` (the established configuration process) to close
   this gap; re-verified afterward that the roster builder now correctly
   returns the 4 individual children for the record that originally
   exposed the marker-boundary bug, and correctly excludes its
   superseded parent.

### Final verified counts

```
accused_unified:     50503 -> 50522 (+19)
persons_unified:     53197 -> 53216 (+19)
accused_split_audit: 0 -> 19
superseded parents:  8
be_read.accused:     50514 (= 50522 - 8, exact)
person_enrichment:   53182 (unchanged by the split, as designed)
distinct accused_id = total row count; distinct person_id = total row
  count -- no PK collisions anywhere
```

The record that originally exposed the marker-boundary detector bug now
produces 4 children in production (not the pre-fix 3-with-a-garbled-
combined-segment) — confirmed via its `accused_split_audit` rows: 4
plausible individual-length segments, none containing the `"and 4."`
artifact that caused the original bug.

### What was NOT changed

`crime_enrichment` (16950), `arrest_enrichment` (33142),
`accused_enrichment` (50503), and `drug_extractions` (21899) — all
present, all untouched, since no code path in this session's work writes
to any of them. Cron job count unchanged (2 entries). No `.env` value
other than the one new multi-accused flag line was touched.
`ETL3_AI_ENABLED`/`ETL3_AI_MODE` unchanged.

### Revised six-step status

All six steps are now **complete and executed**, not merely prepared:

| Step | Status |
|---|---|
| 1. Finalize reports, git state, safe artifacts | Done (prior session) |
| 2. Close address-resolution investigation | VERIFIED (prior session) |
| 3. Domicile/nationality backfill | **EXECUTED AND VERIFIED** — 27 rows corrected, independently re-confirmed |
| 4. Multi-accused splitting & downstream compatibility | **EXECUTED AND VERIFIED** — migrations applied, split run twice (idempotent), downstream readers confirmed correct |
| 5. Deployment & operational validation | VERIFIED throughout |
| 6. Reconcile existing data | **EXECUTED** — targeted updates applied exactly as planned, no truncate, no unrelated table touched |

### Final GO/NO-GO: GO — executed, verified, no outstanding discrepancy

Both actions previously gated pending approval have been executed under
explicit authorization, each individually verified against its own
pre-stated manifest with zero unexplained discrepancy. The only
remaining, expected (not blocking) follow-up is that the 19 new
`persons_unified`/`accused_unified` rows do not yet have
`person_enrichment`/`accused_enrichment` rows — by design, this happens
on a subsequent enrichment pass (automatic or manual), not as part of
the split writer itself.
