# DOPAMS ETL3 Accused Reconciliation — Implementation Report

**Scope:** implements the safeguards identified in `CCTNS_V2_ACCUSED_API_CHANGE_AUDIT.md`. Covers the 11-item implementation task (duplicate-split prevention, unnumbered-candidate safety, subsequent-run reconciliation, source-identity protection, placeholder-name handling, historical change tracking, consolidation-pipeline investigation, migration/application-integration verification, regression tests) plus one narrowly-scoped, explicitly-authorized application fix in the separate DOPAMS-BE repo, plus a final pre-production safety review (section 0.1) and a post-review trim before commit (section 0).
**What was NOT done, by design:** no production database write, no production migration execution, no production ETL run, no production service restart, no global name-based person merging, no weakening of `detect_multi_accused`'s confidence rules, no blind application of migration 019, no rebuild of `be_read.*` materialized views.
**Date:** 2026-11-20 (original implementation), updated 2026-11-20 (final safety review), closed 2026-11-24 (trimmed before commit — see §0 below).

---

## 0. Closing Status (2026-11-24) — supersedes the review-queue design described below

**This feature shipped in a trimmed form.** Everything from §3 onward (including §0.1's own safety review) describes the ORIGINAL design: a persisted `accused_split_review` table with a `resolved_split`/`resolved_matched`/`dismissed` human-review workflow. That design was never committed. Before commit, it was cut down to just the deterministic withhold-on-overlap logic, because §13 item 3 of this same report's own safety review (below) had already identified the exact reason the removal is correct: **no one was designated to operate the review queue** — no UI, no assigned process, nothing would ever have acted on an open row except the automatic `auto_cleared` path. A stateful table nobody triages is worse than no table.

### What actually shipped (current source of truth: read the code, not this report)

- `etl3/merger/accused_overlap.py` — **unchanged** from §4.1 below: `name_overlap_score()` / `find_crime_overlaps()`, same thresholds (`TOKEN_MATCH_RATIO=0.78`, `OVERLAP_SCORE_THRESHOLD=0.60`), same evidence-only discipline (no DB access, no merge, ever).
- `etl3/merger/accused_split.py` — `build_split_rows(parent, raw_full_name, existing_crime_accused=None)` returns the **original 4-tuple** (`person_rows, accused_rows, audit_rows, parse_result`), not the 5-tuple described below. An overlapping segment is withheld exactly as §4.1/§5 describe, but instead of a `review_rows` entry it calls a new `_log_overlap_withheld()` — one `logging.warning()` line with parent/crime/segment/matched-accused/basis/score — and nothing is persisted. Removed entirely (never shipped): `resolved_split_segments`, `review_slot_id`, `_build_overlap_review`, `_build_low_confidence_review`, low-confidence-pack persistence, `existing_open_reviews`, `diff_cleared_reviews`, `clear_reviews`, `write_review_rows`, `load_resolved_split_segments`. **Kept exactly as described:** `unsupersede_parent_if_set` and the single-name-shrink reconciliation (§4.1.3) — that fix never depended on the review table.
- `etl3/enrichment/runner.py` — `_multi_accused_split()` keeps `_load_existing_crime_rosters()` and the overlap-withhold wiring; every review-queue read/write call is gone. Stats renamed `parents_fully_held_for_review`→`parents_fully_held_as_overlap`, `segments_held_for_review`→`segments_withheld_as_overlap`; `segments_unblocked_by_resolved_split`/`reviews_auto_cleared`/`review_stats` removed.
- **Deleted, never committed:** `etl3/migrations/023_accused_split_review.sql`, `etl3/diagnostics/_list_open_accused_split_reviews.py`, `etl3/diagnostics/_validate_accused_overlap_review.py`. Migration 023 will never need to be applied anywhere.
- `etl3/tests/test_multi_accused.py` / `test_enrichment.py` — updated to the 4-tuple signature; the `resolved_split_segments`-override test replaced with a direct withhold-and-log test.

Sections §3–§7, §10 (items 1 and 4), §11, §12, and §13 below are kept as the historical record of the investigation that led to this decision — they describe the pre-trim design, not what is in the repository today.

### Post-trim verification (read-only against live `dopams_cctns_v2`; zero writes, same as every finding in this report)

1. **Diff review** — confirmed the matching/threshold logic is byte-for-byte unchanged from the pre-trim version; only persistence (the table/workflow) was removed.
2. **FIR 869/2026 live-state recheck** — re-queried `accused_unified`/`persons_unified` for crime `6a359f414d1e611f530f007e`: still 9 rows (1 packed parent, 4 pre-existing CCTNS-native near-duplicates, 4 split children). Every child's `derived_from_accused_id`/`crime_id`/`split_segment_index` is intact and unchanged. **Still unresolved** — this trim does not retroactively fix anything already written.
3. **Dry-run against live data** — ran the shipped `build_split_rows()` read-only against all 51,096 live `accused_unified` candidates (not just previously-known cases), with each parent's real same-crime roster wired in exactly as `_multi_accused_split()` would. Non-overlapping segments still split normally (e.g. "Jadav Prabhu s/o Narayana" / "Jadav Vasantha d/o Raju" — 2/2 built, 0 withheld), confirming the guard doesn't over-withhold.

   **This surfaced a materially larger finding than FIR 869/2026 alone.** Of the 13 live high-confidence multi-accused packs system-wide (V1 + V2 combined), **7 have at least one segment that duplicates an existing, separately-coded native accused on the same crime** — 17 already-created accused rows in total:

   | Parent | Crime | Segments withheld | Origin |
   |---|---|---:|---|
   | `6a35a0541103855a721a342c` (FIR 869/2026, V2) | `6a359f414d1e611f530f007e` | 4/4 | This engagement's 2026-11-18 backfill |
   | `V1:ed28b7db2bd3989cd1d563adfe3040f3` (Bishan Singh/Karnati/Badari) | `2022022141092` | 3/3 | This engagement's 2026-11-18 backfill |
   | `V1:5707b813b1318dbbf5419c662d08f520` (Mohammed Layaq/Javeed) | `2022022130811` | 2/2 | This engagement's 2026-11-18 backfill |
   | `V1:10a7a071d7ee724d0779193ea78b310e` (Malluvarasa/Jajimoggala) | `2022011140267` | 2/2 | This engagement's 2026-11-18 backfill |
   | `V1:09031b25290f14650aad93b03d30491f` (Revelli Bhudevi/Pochavva) | `2032036120104` | 2/2 | Pre-existing split, predates this engagement |
   | `V1:63f05b586c548ec6af3353d6695ffcb8` (N.Nagesh/Lakkaraju Tirumal Rao) | `2023067130005` | 2/2 | Pre-existing split, predates this engagement |
   | `V1:a4c2aa446a2cf5155e4156e6c58cfb9e` (Bhaidas Moithe family, partial) | `2032018110133` | 1/3 | Pre-existing split, predates this engagement |

   Only 1 of this engagement's 5 2026-11-18 splits (Jadav Prabhu/Vasantha) has no overlap and remains clean. The earlier full rescan (`investigation/reports/CCTNS_V2_COMBINED_TO_SEPARATE_ACCUSED_INVESTIGATION.md`) that found "exactly 1 combined/packed record system-wide" was **V2-only** — it never checked V1, where most of this actually lives.

### Updated deployment blocker (supersedes §13 item 3 below)

§13 item 3 ("no one is designated to operate the review queue") is **resolved by removal** — there is no queue left to operate. In its place:

**7 crimes / 17 accused rows need a separate, evidence-based human reconciliation decision**, independent of this code change: the table above, plus FIR 869/2026. This is data cleanup, not a code defect — the shipped guard prevents new duplicates going forward; it does not retroactively undo what earlier runs (this engagement's 2026-11-18 backfill, and at least one earlier, unrelated split pass) already wrote. Scope and own this separately.

---

## 0.1. Final Pre-Production Safety Review (addendum, pre-trim — historical record)

Four items were independently re-verified before this work is considered deployment-ready. All four surfaced real findings; three resulted in code changes, one in a documentation correction.

| # | Item | Finding | Outcome |
|---|---|---|---|
| 1 | Parent lifecycle (complete vs. partial overlap) | The report's "Before/After" narrative (old §9) incorrectly claimed the combined parent stops being visible once the fix ships. The code was already correct — it only marks a parent `split_superseded_at` the first time it produces a real child; a parent whose segments are **completely** withheld for review is never superseded. The report text was wrong, not the code. | **Documentation fixed** (§9); behavior verified end-to-end against both complete- and partial-overlap scenarios, including a direct replica of the exact SQL DOPAMS-BE's `PROFILE_FROM`/`CRIME_LINKS` use to decide visibility (new checks in `_validate_accused_overlap_review.py`). No code change — the existing asymmetry (supersede only on a real split) is the correct, safe behavior and is now explicitly documented as intentional. |
| 2 | Fuzzy matching (Indian names) | Expanded `test_accused_overlap.py` with initials, common surnames, aliases, relative-name markers, the Telugu "Pedda/China" elder-younger naming convention, and reordered 3-token names. All correctly avoid auto-merging (nothing in this module ever merges anything regardless of score). Found two **documented false-negative** patterns: an initial in place of a full name (e.g. `"S. Venkatesh"` vs `"Srinivas Venkatesh"`), and the extremely common `"Mohd."` abbreviation (`"Mohd. Yusuf"` vs `"Mohammed Yusuf"`, token ratio 0.667 < 0.78 threshold). | **10 new tests added**, all passing. False negatives documented as an accepted tradeoff (favors fewer review-queue false positives); threshold **not** changed — that was already empirically tuned against real FIR-869 data and retuning without equivalent real-data validation was judged out of scope for this pass. |
| 3 | Single-name reconciliation | Confirmed real gap (previously flagged, unfixed, in old §10.1): when a previously-split pack's source text shrinks to a single remaining name, `detect_multi_accused` correctly stops recognizing it as a pack at all (confidence `"none"`) — but the old reconciliation loop skipped the supersede/diff step entirely whenever `parse_result["segments"]` was empty, so the stale real children were never retired, **and** the parent itself stayed hidden (`split_superseded_at` set from the earlier split) even though it was now the only correct representation of that accused — a real, user-visible regression (the accused would disappear from DOPAMS-BE entirely). | **Fixed.** `detect_multi_accused`'s 2-marker minimum was **not** touched. New `_load_parents_with_active_children`/`unsupersede_parent_if_set` retire the stale children (never deleted) and make the parent visible again. Verified end-to-end, including idempotent reruns. |
| 4 | Review queue consistency | `accused_split_review.status='resolved_split'` (a human deciding a withheld segment IS a distinct person) had **no code path that ever acted on it** — the segment would be re-withheld forever regardless of the human's decision, making that status value dead. No read-only way existed to inspect the open backlog. | **Fixed.** New `load_resolved_split_segments`/`build_split_rows(resolved_split_segments=...)` honor a `resolved_split` decision on every subsequent run. `resolved_matched`/`dismissed` deliberately remain inert (correct — both mean "do not create a child"). New read-only `etl3/diagnostics/_list_open_accused_split_reviews.py` lists the open backlog with suggested resolution SQL; it never writes anything. |

All four items were verified with real code execution (unit tests + disposable-database end-to-end runs), not just read-through. See §4, §8, and §10 below for details; §13 lists the resulting deployment blockers.

---

## 1. Audit Finding (recap)

FIR 869/2026 (crime `6a359f414d1e611f530f007e`) carried, on CCTNS V2 itself: four independently-coded accused records for four real people, **plus** a fifth, uncoded record whose linked person's `full_name` packed all four names together with explicit numbering. ETL3's multi-accused split feature parsed that fifth record and created **four more** rows — a third representation of the same four people, on top of the two CCTNS V2 already had.

## 2. Root Cause

`etl3.merger.accused_split.build_split_rows()` had no way to check whether a parsed segment already matched an existing accused record on the **same crime**. The detector (`detect_multi_accused`) correctly identifies a high-confidence numbered/coded pack; the split writer correctly creates deterministic, auditable, never-deleted children for it — but neither step ever looked sideways at the rest of the crime's accused roster before deciding to create those children. A second, independent root cause for the same underlying symptom class: `change_log`'s `administrative_bulk_excluded` classification has existed in the schema since migration 001 but was **never wired into any code path**, so a 2026-08-24 bulk administrative timestamp event could be misread as genuine business activity by anything trusting `change_log` at face value.

## 3. Files Changed

**New:**
- `etl3/merger/accused_overlap.py` — pure-function name-overlap scoring (no DB).
- `etl3/migrations/023_accused_split_review.sql` — additive-only, not applied to any real database by this work.
- `etl3/tests/test_accused_overlap.py` — 16 pure-function unit tests.
- `etl3/diagnostics/_validate_accused_overlap_review.py` — disposable-DB end-to-end validation (31 checks).
- `etl3/diagnostics/_register_known_bulk_events.py` — operator script, dry-run/execute, not executed against production by this work.
- `etl3/diagnostics/_reclassify_bulk_excluded_change_log.py` — operator script, dry-run/execute, not executed against production by this work.
- `etl3/diagnostics/_list_open_accused_split_reviews.py` (safety-review addendum) — read-only inspector for the `accused_split_review` backlog; never writes, works whether or not migration 023 is applied.
- This report.

**Modified:**
- `etl3/merger/accused_split.py` — `build_split_rows()` gains `existing_crime_accused` parameter and a 5th return value (`review_rows`); new `review_slot_id`, `_build_overlap_review`, `_build_low_confidence_review`, `existing_open_reviews`, `diff_cleared_reviews`, `clear_reviews`, `write_review_rows`. **Safety-review addendum:** `build_split_rows()` gains a `resolved_split_segments` parameter (honors a human's `resolved_split` decision); new `unsupersede_parent_if_set`, `load_resolved_split_segments`.
- `etl3/enrichment/runner.py` — new `_load_existing_crime_rosters()`; `_multi_accused_split()` rewritten to compute and act on crime-level overlaps and to persist low-confidence candidates. **Safety-review addendum:** new `_load_parents_with_active_children()`; `_multi_accused_split()` further extended to load/apply `resolved_split` overrides and to reconcile a pack that has shrunk to a single remaining name (retire stale children, un-supersede the parent).
- `etl3/merger/current_state.py` — `UnifiedBatchWriter` now consults `bulk_event_exclusions` when classifying a change.
- `etl3/migrations/023_accused_split_review.sql` (safety-review addendum) — `resolved_split`'s status-column comment updated to document the new automatic follow-through; no schema/column/constraint change, still not applied anywhere.
- `etl3/tests/test_multi_accused.py` — 11 call sites updated for the new `build_split_rows` return arity; 3 new assertions. **Safety-review addendum:** +1 new test (`resolved_split_segments` override, pure function).
- `etl3/tests/test_accused_overlap.py` (safety-review addendum) — +10 new tests: common surnames, common given names, the Telugu Pedda/China naming convention (and its spelling-drift counter-example), reordered 3-token names, alias markers, relative-name (`S/O`) markers, and two documented false-negative cases (initials, `"Mohd."` abbreviation).
- `etl3/diagnostics/_validate_accused_overlap_review.py` (safety-review addendum) — +20 new checks: DOPAMS-BE visibility verification (complete and partial overlap, querying the exact `PROFILE_FROM`/`CRIME_LINKS` predicate), the `resolved_split` human-override scenario end-to-end, and the single-name-reconciliation scenario end-to-end (all with idempotent-rerun checks).
- `etl3/tests/test_enrichment.py` — 2 call sites updated; new assertions for the low-confidence review row.
- `etl3/diagnostics/_validate_multi_accused_split.py` — migration list now excludes `019_backend_read_contract_cleaned_names.sql` (see section 6.3 — this was blocking the script entirely, independent of this work).
- `etl3/docs/DAILY_PIPELINE_ROLLOUT.md` — added the new test file and both disposable-DB scripts to the pre-enable checklist.
- `CCTNS_V2_ACCUSED_API_CHANGE_AUDIT.md` — corrected the 82-vs-150 "discrepancy" (it was never a discrepancy; see section 5 below).
- `D:\Dopams\Dopams app\DOPAMS-BE\src\schema\criminal-profile\services\unified-profile.ts` — `PROFILE_FROM`/`CRIME_LINKS` now exclude `split_superseded_at IS NOT NULL` rows; `CrimeLink` gains `derived_from_accused_id`/`split_segment_index`. Confirmed with a full-project `tsc --noEmit` (exit 0, zero errors).

## 4. Implementation Details

### 4.1 Crime-level overlap detection (items 1, 2, 3)

`etl3/merger/accused_overlap.py` provides `name_overlap_score(name_a, name_b)` and `find_crime_overlaps(segments, existing_accused)`. Reuses `etl3.identity.person_matching._norm_name`/`_tokens` (already proven, already the project's own normalization standard) and the project's existing fuzzy-matching precedent (`difflib.SequenceMatcher`, already used in `etl3/merger/ps_enrichment.py` and at the same order of magnitude as the `SIM_*` thresholds in `etl3/enrichment/address.py`) — no new dependency.

`build_split_rows()` now takes an optional `existing_crime_accused` list (caller-scoped: every OTHER non-split, non-superseded accused on the SAME `crime_id`, excluding the parent itself). For each parsed segment: an overlap withholds that segment from `person_rows`/`accused_rows`/`audit_rows` and produces a `review_rows` entry instead; a segment with no overlap is built exactly as before. Omitting the parameter reproduces the exact pre-existing behavior (verified: all 41 pre-existing `_validate_multi_accused_split.py` checks pass unchanged).

`etl3.enrichment.runner._multi_accused_split()` now batch-loads every crime's current non-split/non-superseded roster once per run (`_load_existing_crime_rosters`), passes the relevant slice to each parent's `build_split_rows()` call, and writes any `review_rows` via the new `write_review_rows()`. A previously-open review row that is **not** reproduced this run (the conflicting record was itself retired, or the text changed enough to clear it) is auto-closed via `diff_cleared_reviews`/`clear_reviews` — the exact same set-diff discipline `diff_superseded_children` already established for split children — and the segment is free to become a real child in that same run if it still parses.

Low-confidence packs (item 2) are **not** auto-split — `detect_multi_accused`'s confidence rules are untouched. What changed is persistence: a `low_confidence_candidate` review row is now written (previously, this information was silently recomputed and discarded every run).

### 4.1.1 Parent lifecycle: complete vs. partial overlap (safety-review finding 1)

`_multi_accused_split()` marks a parent `split_superseded_at` (hiding it from DOPAMS-BE and the AI roster) **only the first time `accused_rows` is non-empty** — i.e. only once it has actually produced at least one real split child. This is intentional, and the asymmetry between the two overlap cases is correct, not a bug:

- **Complete overlap** (every segment withheld, `accused_rows` empty): the parent is **never** superseded. Its own `accused_unified` row stays exactly as visible as CCTNS V2 itself reported it. Superseding it here would be an implicit identity decision ("this pack record is redundant with the 4 existing accused") that no human has made yet — exactly the kind of automatic deduplication item 11 forbids. For FIR 869/2026 specifically, this means the pre-existing 5-row duplication (4 independently-coded accused + the combined-pack record) that CCTNS V2 itself already carries is **unchanged** by this fix — what the fix eliminates is ETL3 adding a *sixth-through-ninth* row on top of it. Resolving the original 5-row duplication is exactly what the review queue (`crime_level_name_overlap` rows, `status='open'`) exists for.
- **Partial overlap** (at least one segment is genuinely new, `accused_rows` non-empty): the parent **is** superseded, same as any other successful split. Its combined-pack text has been decomposed — part of it into a real new child, part of it withheld pending review — so the parent's own undifferentiated row is correctly retired. The withheld segment's identity is represented only by whichever existing accused it overlaps (pending human confirmation) or not at all if that match is later dismissed; see §10 for this residual gap.

Both cases were re-verified end-to-end in `_validate_accused_overlap_review.py` against a direct replica of the exact predicate DOPAMS-BE's `PROFILE_FROM`/`CRIME_LINKS` use (`split_superseded_at IS NULL`): the complete-overlap scenario shows all 5 original rows still visible and the pack's own `person_id` still counted once in `PROFILE_FROM`'s `no_of_crimes` (never merged into any of the 4 existing people); the partial-overlap scenario shows the parent's row gone, the 2 pre-existing accused visible, and the 1 new child visible — 3 visible rows total, exactly matching what a human should see.

### 4.1.2 Human review follow-through: honoring `resolved_split` (safety-review finding 4)

Before this addendum, `accused_split_review.status='resolved_split'` had no effect on any later run — a human marking a withheld segment "this IS a distinct person, split it" would see it re-withheld forever, since `build_split_rows()` recomputed the overlap from name text alone every time, with no way to know a human had already decided otherwise. This made that status value, and the review queue's ability to actually unblock anything, non-functional.

Fixed via `etl3.merger.accused_split.load_resolved_split_segments(conn)` (batched: `parent_accused_id -> {segment_index, ...}`, one query, called once per run) and a new `resolved_split_segments` parameter on `build_split_rows()`. A segment named in this set is built through the **ordinary, unmodified** split path even though `find_crime_overlaps` still reports the same overlap evidence — the identity decision was already made by a human when they set the status; this only lets the pipeline's default behavior (build the segment) proceed instead of being blocked. `resolved_matched` (human confirms the segment IS the same person as the existing accused) and `dismissed` (human decided no action needed) are deliberately **not** included — both correctly mean "keep withholding, create nothing," which is already what happens without this change.

### 4.1.3 Single-name reconciliation (safety-review finding 3, closes old §10.1)

Previously-flagged, unfixed gap: when a 2-segment pack's source text shrinks to a single remaining name (e.g. `"1) A, 2) B"` → `"A"`), `detect_multi_accused` correctly drops to `confidence="none"` (its 2-marker minimum is never weakened to "fix" this) — but the old reconciliation loop returned early whenever `parse_result["segments"]` was empty, before ever running the supersede/diff step. Two consequences, confirmed live: the stale real children (for the names that no longer appear) were never retired, **and** the parent itself stayed hidden (`split_superseded_at` set from its earlier, successful split) even though it was now the sole correct representation of this accused — meaning the accused could disappear from DOPAMS-BE entirely.

Fixed with two new pieces, neither touching `detect_multi_accused`: `_load_parents_with_active_children(conn)` (one batched query, so the per-parent cost is paid only for the rare parent that actually has children, not every candidate) and `etl3.merger.accused_split.unsupersede_parent_if_set(conn, parent_accused_id)` (the exact reverse of `supersede_parent_if_unset`, idempotent). When a parent that has active children stops parsing as a pack at all, its children are retired via the same `supersede_children` path every other reconciliation case uses (never deleted), and the parent is un-superseded — visible again, exactly like an ordinary never-split accused. Verified end-to-end including a third, fully idempotent rerun.

### 4.1.4 Expanded fuzzy-match validation (safety-review finding 2)

`etl3/tests/test_accused_overlap.py` was expanded with representative Indian-naming patterns: common surnames shared by different people (`"Ramesh Kumar"`/`"Suresh Kumar"`), a common given name shared by different people (`"Mohammed Ali"`/`"Mohammed Aslam"`), the Telugu "Pedda"/"China" (elder/younger) naming convention that distinguishes two different people despite an otherwise-identical name (correctly **not** flagged) versus its genuine spelling-drift counter-example `"Chinna"`/`"China"` (correctly flagged), reordered 3-token names, an alias marker (`"Pappu @ Rajesh"`) overlapping a plain name, and a relative-name suffix (`"Ramesh S/O Krishna"`) still correctly flagging on the shared given name. All ten new cases pass, and in every case the only possible outcome is evidence (a `basis`/`score` pair) that a caller may route to the review queue — nothing in this module has ever had a code path capable of merging or linking an identity automatically, by construction (`name_overlap_score`/`find_crime_overlaps` take no database connection and perform no writes).

Two **documented false negatives** were found and are intentionally not fixed in this pass: an initial standing in for a full name (`"S. Venkatesh"` vs `"Srinivas Venkatesh"`, score 0.5) and the extremely common `"Mohd."` abbreviation (`"Mohd. Yusuf"` vs `"Mohammed Yusuf"`, score 0.5 — `"mohd"`/`"mohammed"` scores 0.667 on the per-token ratio, below the 0.78 `TOKEN_MATCH_RATIO`). Both are accepted tradeoffs of the existing, empirically-tuned threshold (favoring fewer false-positive review-queue entries over catching every possible duplicate) — lowering the threshold to catch these was not attempted, since it was not re-validated against a real, larger sample of production names and risks materially more review-queue noise for a dataset this size. This is a real, residual gap worth tracking operationally (see §13), not a correctness defect in what was built.

### 4.2 Historical change tracking (item 6)

`etl3.merger.current_state.UnifiedBatchWriter.__init__` now loads `bulk_event_exclusions` once per instance; `add()`'s classification step checks the new window against `incoming_ts` and `source_system` before labeling a change `business_change`, using the already-reserved `administrative_bulk_excluded` enum value. This affects only **future** observations. Two new operator scripts (dry-run by default, `--execute --confirm` required) let an operator register the documented 2026-08-24 window and, separately, correct the `change_classification` (never `old_value`/`new_value`) of already-written rows that fall in it. Neither script was run in `--execute` mode against `dopams_cctns_v2` by this work.

### 4.3 DOPAMS-BE fix (item 8, authorized scope)

Confirmed independently that DOPAMS-BE's live `/criminal-profile` resolver reads `persons_unified`/`accused_unified`/`person_enrichment.cleaned_full_name` directly via `services/unified-profile.ts`, never `be_read.*` or the legacy `criminal_profiles_mv` — so migration 019 fixes nothing there and was correctly left unapplied and unimplemented. The real, proven gap: that service's two central, widely-reused SQL fragments (`PROFILE_FROM`, used at 13 call sites; `CRIME_LINKS`, used at 3) had no awareness of `split_superseded_at`, so a split parent and its children rendered as separate, undifferentiated accused. Fixed at both shared definitions (not at each of the 16 call sites individually) — `PROFILE_FROM`'s inner crime-count subquery and `CRIME_LINKS`'s base query now both filter `split_superseded_at IS NULL`; the three call sites that appended their own `WHERE` after `${CRIME_LINKS}` were changed to `AND` (two of them additionally parenthesized, since SQL's `AND`/`OR` precedence would otherwise silently re-admit a superseded row whenever the `OR` branch matched). `CrimeLink`'s TypeScript type and `CRIME_LINKS`'s `SELECT` list both gained `derived_from_accused_id`/`split_segment_index` so split lineage is available at the service-row level — **not** yet threaded through to a public GraphQL field, which is a separate, larger product/schema decision left to your team.

## 5. Matching and Review Rules

Per the task's explicit instructions, nothing here ever merges or auto-links an identity:

- **Exact normalized match** or **exact token-set match (reordered)** → always withheld + recorded (`match_basis` = `exact_normalized_name` / `exact_token_set_reordered`).
- **Strong fuzzy similarity** (token-level bipartite match, `≥0.60` average coverage, each token pair compared at `SequenceMatcher` ratio `≥0.78`) → withheld + recorded (`strong_name_similarity`). Both thresholds were tuned empirically against the real FIR 869/2026 token pairs (same-person pairs scored 0.667–1.0; every tested different-person pair scored below 0.60) — see `etl3/tests/test_accused_overlap.py` and `etl3/merger/accused_overlap.py`'s own inline evidence comment.
- **Below threshold** → no action; the segment proceeds through the normal split path unaffected.
- **Different source accused IDs are never merged.** The only action an overlap triggers is withholding automatic child creation and recording the evidence (matched `accused_id`, `match_basis`, `match_score`) in `accused_split_review`, `status='open'`, for a human to resolve via `resolved_split` / `resolved_matched` / `dismissed`. No code path ever **sets** those three statuses automatically — a human always sets them, out of band, directly against the database (there is no resolution UI; see §10).
- **`resolved_split` IS now automatically acted upon** (safety-review addendum, §4.1.2) — once a human sets it, the segment is allowed to split on the next run. This is not an exception to "no code path ever decides identity automatically": the decision was already made by the human; the code only stops blocking its own default (otherwise-identical) behavior. `resolved_matched`/`dismissed` trigger no further action (correct: no child should ever be created for either).
- **Identical names do not prove identical people** (the 150 `invalid_literal` placeholder names and the "absconding"/"Tony" cases in the test suite are deliberate counter-examples); **different names do not prove different people** (the `Tony` vs `Tony Kumar` ambiguous-partial-name test deliberately flags for review rather than auto-dismissing). Also verified against representative Indian-naming patterns (§4.1.4): common surnames and common given names shared by different people do not overlap; the Telugu "Pedda"/"China" elder-younger convention correctly does not overlap despite an otherwise-identical name.
- **Inspecting the open backlog:** `python -m etl3.diagnostics._list_open_accused_split_reviews` (read-only; never writes) lists every open row grouped by reason, with the matched existing accused's name and the raw segment text for context, plus the exact `UPDATE` statement an operator would run to resolve it. It works whether or not migration 023 has been applied (prints a message and exits cleanly if the table doesn't exist yet).

## 6. Database Impact

**Live `dopams_cctns_v2` database: zero writes performed by this work.** Every finding above involving a live-DB number came from a read-only connection (`etl3.db.connections.get_unified_connection(readonly=True)`), the same pattern the original audit used.

### 6.1 New table (not yet applied anywhere live)

`accused_split_review` (migration 023) — one row per (parent, segment-or-whole-pack) slot, upserted via the existing `upsert_rows` helper (same mechanism `accused_split_audit` already uses), so a repeated detection is a true no-op and changed evidence updates the same row rather than creating a new one.

### 6.2 Disposable-database validation (created and dropped by this work; never touched a real database)

- `_validate_multi_accused_split.py` (pre-existing, 41 checks) — re-run after my changes: **41/41 pass**, zero regressions.
- `_validate_accused_overlap_review.py` (new, 31 checks) — **31/31 pass**: FIR 869/2026 pattern (complete overlap, 4 withheld, 0 new accused rows, idempotent rerun), partial overlap (1 real child + 2 withheld), overlap-resolves auto-clear-and-create, the exact brief-text unnumbered example (low-confidence, persisted, zero fabricated identities), no enrichment contamination, and missing-accused (3→2 segments, dropped child superseded not deleted, kept children untouched).

### 6.3 A pre-existing migration-chain defect, discovered (not introduced) while building the disposable-DB tests

`019_backend_read_contract_cleaned_names.sql` and `020_backend_read_contract_split_filter.sql` are mutually incompatible: 020's `CREATE OR REPLACE VIEW be_read.accused` cannot carry forward 019's added columns, and Postgres refuses with "cannot drop columns from view" when both are applied in sequence. Confirmed this blocked `_validate_multi_accused_split.py` outright (before any of my changes) the first time it was run against the current repo state. Since migration 019 was independently confirmed (background investigation, this task's own item 8) **never applied on the real server**, excluding it from the disposable-DB migration list mirrors production's actual state and is the only way either validation script can reach migration 018 or later at all. This is a real, pre-existing defect in the migration chain, not something this report's changes caused — flagged for your team; not fixed here (fixing `be_read.*` view migrations was explicitly out of scope).

## 7. Migration Impact

`023_accused_split_review.sql`: purely additive (one new table, two new indexes, two new FKs into `accused_unified`). No existing column, constraint, index, or row is touched. Requires migration 018 already applied (same precondition the split feature as a whole already has). **Not applied to `dopams_cctns`/`dopams_cctns_v2`** — an operator must review and run it via `etl3/migrations/run_migration.py`, same process as every other migration in this repo.

## 8. Test Results

| Suite | Checks | Result |
|---|---|---|
| `etl3/tests/test_multi_accused.py` | 41 (+1 safety-review) | 41/41 pass |
| `etl3/tests/test_accused_overlap.py` | 26 (+10 safety-review) | 26/26 pass |
| `etl3/tests/test_enrichment.py` | 50 | 49/50 pass — 1 failure (`ai retry`) is pre-existing and unrelated (AI drug-extraction retry-count assertion in `etl3/enrichment/ai.py`, untouched by this work; confirmed by reading the failing test, which exercises `extract_with_retry`/`_ScriptedClient`, nothing related to accused/split/overlap logic) |
| `etl3/diagnostics/_validate_multi_accused_split.py` (pre-existing, disposable DB) | 41 | 41/41 pass — re-verified after the safety-review changes, zero regressions |
| `etl3/diagnostics/_validate_accused_overlap_review.py` (disposable DB) | 51 (+20 safety-review) | 51/51 pass |
| DOPAMS-BE `tsc --noEmit` (full project) | — | 0 errors — re-verified after the safety-review pass (no DOPAMS-BE code was changed this pass; the file was independently re-read and confirmed consistent with the report) |

**Running total: 209 automated checks** (41+26+49+41+51, counting the one pre-existing/unrelated failure) **across 5 suites, 208 passing**, plus a clean full-project TypeScript build. 31 of these checks (10 in `test_accused_overlap.py`, 1 in `test_multi_accused.py`, 20 in `_validate_accused_overlap_review.py`) were added by this safety-review pass specifically to exercise the four items in §0.1.

### 8.1 Regression-scenario mapping (the 18 required scenarios + FIR 869/2026)

| # | Scenario | Where it's covered |
|---|---|---|
| 1 | Combined record with existing separately-coded accused | `test_accused_overlap.py::test_complete_overlap_fir_869_pattern`; `_validate_accused_overlap_review.py` FIR-869 scenario |
| 2 | Complete overlap | same as above |
| 3 | Partial overlap | `test_accused_overlap.py::test_partial_overlap_only_matching_segments_withheld`; `_validate_accused_overlap_review.py` partial-overlap scenario |
| 4 | Exact duplicate names, different source IDs | `test_accused_overlap.py::test_exact_match`, `test_strongest_match_wins_when_multiple_candidates_score` |
| 5 | Same names, different people | `test_accused_overlap.py::test_different_people_sharing_one_common_token_do_not_overlap` |
| 6 | Unnumbered comma-separated names | `_validate_accused_overlap_review.py` low-confidence scenario, using the task's own literal example text |
| 7 | Alias vs. multiple accused | pre-existing `test_enrichment.py::test_regular_etl_aliases_not_treated_as_separate_accused` (re-run, still passes; unaffected by this change) |
| 8 | Repeated identical observations | pre-existing `_validate_multi_accused_split.py` idempotent-rerun scenario; new script's own idempotent-rerun assertions |
| 9 | Name correction | pre-existing `_validate_multi_accused_split.py` correction scenario (re-run, still passes) |
| 10 | Name reordering | pre-existing `_validate_multi_accused_split.py` reordering scenario (re-run, still passes); `test_multi_accused.py::test_diff_reordering_supersedes_both_instead_of_guessing` |
| 11 | Additional accused | `_validate_accused_overlap_review.py` partial-overlap scenario (segment 5, "Brand New Person") |
| 12 | Missing accused | `_validate_accused_overlap_review.py` missing-accused scenario (3→2 segments) |
| 13 | Blank/placeholder names | `test_accused_overlap.py::test_placeholder_names_compare_like_ordinary_text`, `test_blank_or_none_never_matches_anything` |
| 14 | Partial API response | not independently testable at this layer (CCTNS V2 exposes no page/partial marker) — nearest real guarantee is the existing, re-verified `build_person_row_safe` per-record isolation (`test_regular_etl_failed_record_isolated_others_continue`) |
| 15 | Multiple FIR relationships | `test_accused_overlap.py::test_crime_scoping_is_the_callers_responsibility_not_this_functions` (documents the boundary); live-DB finding in the original audit (0 V2 persons observed under >1 crime) |
| 16 | Existing split-child idempotency | `_validate_accused_overlap_review.py` idempotent-rerun assertions (zero additional rows of any kind) |
| 17 | Historical parent and child preservation | `_validate_accused_overlap_review.py` missing-accused scenario (dropped child exists, superseded not deleted) + pre-existing correction/reordering scenarios |
| 18 | No cross-person enrichment contamination | `_validate_accused_overlap_review.py`'s dedicated enrichment-contamination check; pre-existing correction scenario's "superseded child's own prior person_enrichment row still exists" check |
| — | FIR 869/2026 structural pattern, anonymized | `_validate_accused_overlap_review.py`'s first scenario, in full |
| — | Safety-review: DOPAMS-BE visible-row verification (complete + partial overlap) | `_validate_accused_overlap_review.py`'s two "DOPAMS-BE visibility check" blocks |
| — | Safety-review: human `resolved_split` decision honored on the next run | `_validate_accused_overlap_review.py`'s "human resolves a review as 'resolved_split'" scenario; `test_multi_accused.py::test_build_split_rows_resolved_split_override_unblocks_a_withheld_segment` |
| — | Safety-review: pack shrinks to a single remaining name | `_validate_accused_overlap_review.py`'s "pack shrinks to a single remaining name" scenario (closes old §10.1) |

## 9. Before/After Examples

**FIR 869/2026, before this work:** 9 `accused_unified` rows for 4 real people (1 combined parent + 4 split children + 4 pre-existing CCTNS-native duplicates), all rendered as separate rows by DOPAMS-BE.

**After this work (once migration 023 is applied and the flag is enabled on a server running this code):** re-running `_multi_accused_split` on an equivalent fixture produces **zero** new split-child rows for the four overlapping segments and **four** `accused_split_review` rows instead (`status='open'`, each naming the pre-existing accused it overlaps and why) — net **5** accused-looking rows instead of 9 (the 4 pre-existing CCTNS-native duplicates PLUS the combined-pack record itself, exactly as CCTNS V2 originally reported them), flagged for human reconciliation rather than silently tripled.

**Correction (safety-review finding 1, §4.1.1):** an earlier draft of this report incorrectly stated that DOPAMS-BE would stop showing the combined parent as a 5th row once this fix ships. That is wrong, and was a documentation error, not a code bug — the code was already correct. Because every one of the parent's 4 segments is withheld (not split), the parent is **never** marked `split_superseded_at` (that only happens the first time a parent produces at least one real child — see §4.1.1) — so it remains exactly as visible to DOPAMS-BE as CCTNS V2 itself reported it, pending human review. The fix's actual, verified effect is narrower and still correct: it stops ETL3 from adding a 6th-through-9th row on top of the 5 CCTNS V2 already has; it does not retroactively hide or merge the pre-existing 5-row duplication, because doing so without a human decision would itself be exactly the kind of automatic deduplication this task forbids. Resolving the underlying 5-row duplication is what the `accused_split_review` queue (and, once a human sets `resolved_split`/`resolved_matched`, the automatic follow-through added in §4.1.2) is for.

By contrast, in a **partial**-overlap case (at least one segment is genuinely new), the parent genuinely IS superseded — see §4.1.1's partial-overlap verification for the exact visible-row count in that case.

**The task brief's own illustrative example** ("Abid Ali, Altamas, Pradeep, Nandu, Uday", unnumbered): before and after this work, still correctly **not** auto-split (confirmed unchanged, per the explicit instruction not to weaken confidence rules) — but now produces a durable `accused_split_review` row (`reason='low_confidence_candidate'`) with the candidate segments preserved for a human reviewer, instead of being silently recomputed and discarded on every run.

## 10. Remaining Limitations

1. ~~A pack that shrinks to a single remaining name is not reconciled.~~ **Fixed in the safety-review addendum — see §4.1.3.** `detect_multi_accused`'s 2-marker minimum was not touched; the reconciliation loop now retires stale children and un-supersedes the parent in this case, verified end-to-end.
2. **The `V2:` accused_id prefix inconsistency is not fixed** (item 4) — confirmed via grep that zero code paths anywhere in `etl3/` parse `accused_id` by splitting on `:`, so the inconsistency is cosmetic in the schema's own comments, not functionally depended upon. Changing it now would require rewriting the primary key of 34,453+ live V2 `accused_unified` rows and every FK/text reference (`arrests_unified`, `accused_split_audit`, the new `accused_split_review`, `change_log.unified_id` — the last not FK-enforced, so a rename would silently orphan historical rows without a dedicated migration). Not performed; documented as the task instructed.
3. **The consolidation pipeline's scheduling gap is not fixed** (item 7) — this is an operational/infrastructure question requiring access to the `dopams-etl` host this task does not have. See the runbook below.
4. **`accused_split_review` rows are never auto-resolved — but `resolved_split` is now automatically acted upon once a human sets it** (safety-review addendum, §4.1.2) — this closes the "dead status value" gap, but is not a resolution UI. `resolved_matched`/`dismissed` remain correctly inert (no child should ever be created for either). `low_confidence_candidate` rows still have **no** automatic follow-through at all — there is no mechanism for a human to feed a corrected segmentation back into the detector, which would be a materially larger feature (manual parse override) than this pass's scope. A human-facing review UI/workflow (beyond the new read-only `_list_open_accused_split_reviews.py` inspector) does not exist yet and was not built (out of scope — the task asked for the data model and safe-guard, not a review UI).
5. **DOPAMS-BE's split-lineage columns are not yet exposed as public GraphQL fields** — available at the service-row level (`CrimeLink.derived_from_accused_id`/`split_segment_index`) for a future, explicitly-scoped schema change.
6. **The 172 other (crime, name) duplicate-name groups** found in the original audit were not individually reviewed — only FIR 869/2026 was confirmed as a genuine duplicate; most of the rest are plausibly coincidental common names, but this was not exhaustively verified.
7. **Two documented fuzzy-matching false negatives** (safety-review addendum, §4.1.4): an initial in place of a full name, and the common `"Mohd."` abbreviation for `"Mohammed"`/`"Mohammad"`, both score below the overlap threshold and so would NOT be flagged for review if they occurred in a real combined-pack segment. Not fixed this pass (retuning the threshold without re-validating against a larger real sample risks more false-positive noise); a residual gap worth tracking operationally.
8. **A partial-overlap segment's identity has no fallback if its match is later dismissed.** When a segment is withheld because it overlaps an existing accused, and that overlap is later marked `resolved_matched` or `dismissed` (not `resolved_split`), no new child is ever created for it — by design, since neither status means "split this." If a human later changes their mind after dismissing a match (decides it should have been `resolved_split` after all), simply updating the status row to `resolved_split` is sufficient and will be picked up on the next run (§4.1.2) — no limitation there. The actual residual gap is narrower: if the **parent itself** was already superseded (partial-overlap case, §4.1.1) by the time of that later decision, the segment's child is still created correctly (the parent being superseded does not block `_load_parent_accused_for_split` from re-processing it — confirmed by that function's own `WHERE` clause carrying no `split_superseded_at` filter), so this is not actually a gap in practice; recorded here only because it was explicitly checked, not assumed.

## 11. Production Deployment Steps (for an authorized operator — not performed by this work)

1. Confirm migration 018 is already applied on the target database (it already is, per the original audit).
2. Review and apply `etl3/migrations/023_accused_split_review.sql` via `etl3/migrations/run_migration.py`.
3. Deploy this commit (the code changes in `etl3/merger/accused_split.py`, `etl3/enrichment/runner.py`, `etl3/merger/current_state.py`) to whichever host actually runs the scheduled/triggered enrichment pipeline.
4. Confirm `ETL3_MULTI_ACCUSED_SPLIT_ENABLED=1` is effective on **that** host specifically — the original audit found this checkout's `.env` value may not reflect the real deployed application server's actual commit/config; resolve the open `git log`/`git describe` question on the `dopams-etl` host (section 7's remaining-items list) before trusting the flag's literal value there.
5. Run `etl3/diagnostics/_validate_accused_overlap_review.py` once against that server's own Postgres instance (it is self-contained and disposable — creates and drops its own database, 51 checks) before the first real enrichment run with this code.
6. Optionally, once comfortable: `python -m etl3.diagnostics._register_known_bulk_events --dry-run` then `--execute --confirm` to register the 2026-08-24 window; then `python -m etl3.diagnostics._reclassify_bulk_excluded_change_log --dry-run` then `--execute --confirm` to correct the (confirmed, via live dry-run during this work) 11 affected `change_log` rows. Both steps are optional, independent of the split fix, and reversible in the sense that nothing is deleted.
7. For DOPAMS-BE: deploy the `unified-profile.ts` change through its normal release process (not performed by this work). No schema/migration/restart is required on that side — it is a pure query-text change.
8. Resolve the consolidation-pipeline scheduling gap (section 7) separately — this is an infrastructure/operations task, not a code deployment.
9. Operationally, after the flag is live: periodically run `python -m etl3.diagnostics._list_open_accused_split_reviews` (read-only) and have a designated person actually resolve the backlog (set `resolved_split`/`resolved_matched`/`dismissed` directly against the database — there is no UI). A `resolved_split` decision takes effect automatically on the next enrichment run; the other two statuses are terminal and require no further action.

## 12. Rollback Procedure

Because nothing was applied to any production system, "rollback" is almost entirely "do not take the steps in section 11":

- If migration 023 was applied and needs undoing: `DROP TABLE accused_split_review;` (it has no inbound FKs from any other table — it only has outbound FKs to `accused_unified`) — safe at any time since nothing else ever reads it yet.
- If the code was deployed and needs reverting: revert the three modified ETL3 files (`accused_split.py`, `runner.py`, `current_state.py`) to their pre-this-work state; `build_split_rows`'s call sites in `test_multi_accused.py`/`test_enrichment.py` would need their own revert alongside it (they're paired with the signature change). The safety-review addendum's additions (`resolved_split_segments` param, `unsupersede_parent_if_set`, `load_resolved_split_segments`, `_load_parents_with_active_children`) are all inside those same three files — no separate revert step.
- The new `etl3/diagnostics/_list_open_accused_split_reviews.py` is read-only and never writes anything — there is nothing to roll back for it; deleting the file (if desired) has no data effect.
- If `_register_known_bulk_events.py --execute` was run: `DELETE FROM bulk_event_exclusions WHERE description LIKE 'Administrative/maintenance bulk timestamp update%';` (one row).
- If `_reclassify_bulk_excluded_change_log.py --execute` was run: the affected rows' `change_classification` can be set back to `'business_change'` by re-running the same JOIN query with the assignment reversed — no `old_value`/`new_value` was ever touched, so no data is at risk either way.
- If a human has already set some `accused_split_review.status` to `resolved_split`/`resolved_matched`/`dismissed` and the code is then rolled back to before §4.1.2: those statuses simply stop being read again (no automatic follow-through runs), but the rows and their `reviewed_by`/`reviewed_at` audit trail are untouched — nothing to clean up.
- If the DOPAMS-BE change was deployed and needs reverting: revert `unified-profile.ts` to its pre-this-work state (a single-file, query-text-only change — no migration, no schema, no restart needed either direction).

---

## 13. Final Deployment Blockers

Everything in this list requires an action, access, or decision this work could not perform or make unilaterally. Nothing below is a code defect; each is either an operational dependency or an explicit decision reserved for the client/operator.

1. **Migration 023 is not applied anywhere.** Blocks the entire feature (split, overlap review, the new `resolved_split` follow-through, and the new read-only inspector) — none of it can run against a real database until an operator reviews and applies `etl3/migrations/023_accused_split_review.sql` (requires migration 018 already applied, which the original audit confirmed is true in production).
2. **The deployed commit / flag state on the actual `dopams-etl` host is unconfirmed.** The original audit found the consolidation pipeline had not run successfully since 2026-11-06, with an unresolved conflict between "the deployed commit predates this feature" and "the split flag was already live from an earlier rollout." This safety review did not have host access and could not resolve it. **Blocks:** trusting `ETL3_MULTI_ACCUSED_SPLIT_ENABLED`'s live effective value on that host, and therefore safely enabling the daily pipeline timer per `DAILY_PIPELINE_ROLLOUT.md`'s own activation gate.
3. **No one is designated to operate the review queue.** The fixes in this report make the queue data model complete and `resolved_split` actionable, but there is still no UI and no assigned human process for triaging `accused_split_review`'s open backlog (use `_list_open_accused_split_reviews.py` in the meantime). Without this, open rows accumulate but nothing acts on them except the automatic `auto_cleared` path.
4. **The two historical bulk-event operator scripts have not been run against `dopams_cctns_v2`.** Both are dry-run-verified and reversible, but executing them against the real database is explicitly an operator decision this work does not make.
5. **The `V2:` accused_id prefix, the migration-019/020 incompatibility, and the 172 unreviewed duplicate-name groups remain open**, each requiring either a dedicated migration-impact project, a fix to unrelated `be_read.*` view migrations, or a manual audit pass — none of which this task was authorized or scoped to perform.
6. **DOPAMS-BE's `unified-profile.ts` change has not been deployed** through that repo's normal release process — it is implemented and typechecked clean, but releasing it is a separate action.

None of these blockers are new as of this safety review except #1 and #3, which follow directly from this review's own findings (migration 023 must exist before any of today's fixes can run anywhere real; the `resolved_split` follow-through is only useful once someone is actually resolving reviews).

---

## Summary

**Implemented:** crime-level overlap detection before any split-child creation (the FIR 869/2026 fix); durable persistence of low-confidence pack candidates; a `bulk_event_exclusions`-aware change classifier (forward-looking only); two safe not-yet-executed operator scripts for the historical bulk-event correction; a narrowly-scoped DOPAMS-BE query fix that stops showing split parents and children as duplicate rows; a final pre-production safety review that (a) corrected a documentation error about parent visibility, (b) closed the single-name-reconciliation gap, (c) made `resolved_split` human decisions actually take effect, and (d) added a read-only review-queue inspector; 208 passing automated checks (of 209 total — 1 pre-existing, unrelated failure) across 5 test suites plus a full clean TypeScript build.

**Not implemented, by explicit instruction or informed judgment:** any production write/migration/execution/restart; the `V2:` id-prefix change; a fix for the pre-existing migration-019/020 incompatibility; a review UI for `accused_split_review` (only a read-only inspector); public GraphQL exposure of split lineage; retuning the fuzzy-match threshold to catch the two documented false-negative patterns (initials, `"Mohd."`).

**Needs your confirmation before going further:**
1. Whether to actually run the two bulk-event operator scripts against `dopams_cctns_v2` (both are ready, dry-run-verified, and reversible).
2. Who has access to the `dopams-etl` host to resolve the consolidation-pipeline scheduling question (section 7/§13.2) — this task could not reach it.
3. Who will be designated to actually triage the `accused_split_review` backlog using the new read-only inspector, now that `resolved_split` decisions take effect automatically (§13.3).
4. Whether/when to expose `derived_from_accused_id`/`split_segment_index` as public GraphQL fields in DOPAMS-BE.
5. Whether the 172 unreviewed (crime, name) duplicate groups from the original audit warrant a manual review pass.
6. Whether the two documented fuzzy-matching false-negative patterns (§4.1.4/§10.7) are common enough in this dataset to justify re-tuning the match threshold against a larger real sample.
