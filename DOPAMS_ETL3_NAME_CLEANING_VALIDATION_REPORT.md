# DOPAMS ETL3 Name Cleaning — Implementation & Validation Report

Implements the improvements identified in `DOPAMS_ETL3_NAME_CLEANING_EDGE_CASE_AUDIT.md`. Scope was held strictly to ETL3 (`etl3/enrichment/names.py`, `etl3/enrichment/multi_accused.py`, `etl3/enrichment/project.py`, their tests, and one new, unapplied migration) — no frontend, backend API, GraphQL, legacy read-path, or production-cutover file was touched, per the task's explicit scope restriction.

All findings below are evidence-based: every regression check was actually run, every dataset number comes from actually executing the real code against the real 51,123-row export, and the one live-database inspection was a strictly read-only query (no writes, no migration execution).

---

## 1. Files Changed

| File | Change | Insertions/Deletions |
|---|---|---|
| `etl3/enrichment/names.py` | Extended `clean_person_names()`, `extract_alias_from_name()`, `extract_relationship_info()`, `_strip_noise()`, `clean_full_name()`; added `extract_named_aliases()`, `extract_role_hint()`, `extract_nationality_hint()`, `classify_invalid_literal()`, `classify_noise_only()`, `detect_review_flags()`, `normalize_mojibake()` | +471 / −12 |
| `etl3/enrichment/multi_accused.py` | Extended `_MARKER_RE` (punctuation + weak-boundary variants); added `_A_CODE_MARKER_RE`, `_build_pack_result()`, `_is_weak_boundary()`; restructured `detect_multi_accused()` to try N-style then A-code markers | +190 / −63 |
| `etl3/enrichment/project.py` | `person_row()` now also returns `name_identified`, `unidentified_reason`, `extracted_role_hint`, `extracted_nationality_hint`, `review_flags` (computed, not yet persisted — see §5) | +24 |
| `etl3/tests/test_enrichment.py` | +18 new test functions covering every new names.py capability; 1 existing test (`test_trailing_hyphen_residue_trimmed` and friends) unchanged | +326 |
| `etl3/tests/test_multi_accused.py` | 1 existing test renamed/narrowed to its still-true assertion; +5 new test functions covering A-code detection, missing-leading-marker guard, `"N)."` variant, weak-boundary detection | +116 / −10 |
| `etl3/migrations/022_person_enrichment_identity_fields.sql` | **New, additive, NOT applied.** 5 nullable columns on `person_enrichment` for the metadata computed above | new file |

No existing production code outside `etl3/` was touched. No `be_read.*` view, no frontend/backend repo file, no database write, no migration execution.

---

## 2. What Changed, and Why (mapped to the audit)

| Audit finding | Fix | Verified by |
|---|---|---|
| G-2/G-3: multi-`@` alias chain silently dropped every alias past the second, and leaked a literal `@` into `cleaned_full_name` | `extract_alias_from_name()` now collects every `@`-delimited segment; the chain is stripped from the working name regardless of whether a separate `alias` column value already exists (fixed again mid-implementation — see §4) | `test_multi_alias_at_chain_preserves_every_alias`; full-dataset idempotency check (0 failures) |
| Spelled-out `"Alias"`/`"AKA"`/`"A.K.A."`/`"Also Known As"` (127 records, more common than `@`) never recognized | New `extract_named_aliases()`, same chain/dedup semantics as the `@` extractor | `test_spelled_out_alias_word_extraction` |
| Spelled-out relationship phrases (`wife of`, `mother of`, `father of`, `son of`, `daughter of`, `care of`, `h/o`) unhandled; a dangling marker with nothing after it was never stripped | `extract_relationship_info()` and `clean_full_name()`'s strip both extended via one shared marker list; a marker is now stripped whenever found, independent of whether a relative name follows | `test_spelled_out_relationship_markers`, `test_dangling_relationship_marker_stripped_even_without_relative_name` |
| A relationship-only record (`"Wife of Amzad"`) was displayed as if the relationship sentence were the accused's own name | When nothing survives after relationship extraction, `cleaned_full_name` is `None`/`name_identified=False`/`unidentified_reason="relationship_only"` instead of falling back to the sentence | `test_relationship_only_record_marked_unidentified_not_invented` |
| Bare numeric list prefix (`"02."`, `"1)"`) never stripped; A-code colon variant (`"A-1:"`, `"A5:"`) never stripped; `"Accused Person:"` label never stripped | New patterns in `_strip_noise()` | `test_bare_numeric_list_prefix_stripped`, `test_accused_code_variants_stripped`, `test_accused_person_label_stripped` |
| Only 3 hardcoded parenthetical phrases stripped; `"Supplier"`, `"Main Peddler"`, `"Customer"`, etc. survived | `extract_role_hint()` — whole-parenthetical-content match only, deliberately never touching a bare (unparenthesized) role word | `test_role_phrase_in_parens_extracted_and_stripped` |
| A role-only value (`"02. Medical Shop Owner"`) displayed as if it were a name | `classify_noise_only()`'s whole-value role-phrase check | `test_role_only_whole_value_marked_unidentified` |
| Nationality words never stripped from the name at all | `extract_nationality_hint()` — parenthetical-only, reuses `address.py`'s own `NATIONALITY_TO_COUNTRY` table so the two can never drift apart; a **bare** nationality-shaped word (`"Nepal Krishna"`, `"Khali of Nigeria"`) is deliberately never touched | `test_nationality_in_parens_extracted_but_bare_word_never_touched` (also re-confirms "China China" → "China" still works — the exact real accused name that makes bare-word stripping unsafe) |
| No invalid/placeholder-literal detection anywhere (`"Unknown"`, `"Nil"`, `"N/A"`, a literal `"Absconding"`/`"Unknown Person"` standing in for the name) | `classify_invalid_literal()` + `classify_noise_only()` | `test_invalid_literal_names_marked_unidentified` |
| `yrs?` didn't match spelled-out `"Years"`; `r/o` required whitespace immediately after it (failed on `"R/O."`); no `Occ:` strip rule | 3 regex fixes in `_strip_noise()` | `test_spelled_out_age_and_occupation_label_stripped` |
| Unlabeled bare phone number never stripped; **found during dataset validation**, the existing `"Ph.No.:"` pattern didn't cover the simpler `"Ph:"` label | Generic 10-digit strip + new `ph\.?:\s*\d+` pattern | `test_bare_phone_number_stripped_from_name` |
| Mojibake (`Â`, smart quotes/dashes) never normalized | `normalize_mojibake()` | `test_mojibake_normalized` |
| Non-exact duplicate trailing token (`"A. Lokesh Lokesh"`) correctly never auto-collapsed, but had zero review signal | `detect_review_flags()` — adds a flag, never rewrites the name | `test_review_flags_are_non_destructive_signals_only` |
| `"N)."` punctuation (close-paren then period) invisible to the marker regex | `_MARKER_RE`'s punctuation class widened to `[.)]{1,2}` | `test_n_dot_paren_punctuation_variant_now_detected` |
| Space-separated numbered lists (no comma at all) completely invisible, not even flagged | New "weak boundary" alternative in `_MARKER_RE`; always capped at `"low"` confidence, never auto-split | `test_space_separated_numbered_list_flagged_low_not_lost`, `test_weak_boundary_never_promotes_to_high_even_when_otherwise_perfect` |
| A-code packs (`A1.`, `A-2:`) never treated as split evidence at all, even for review | New `_A_CODE_MARKER_RE` + shared `_build_pack_result()` pipeline, same 2+-marker/sequential/plausibility guards already proven safe for the plain style | `test_a_code_markers_split_at_high_confidence_when_unambiguous` |
| **New guard, found while implementing the above:** a pack whose FIRST marker isn't label 1 (`"Bishan Singh, A2. ..., A3. ..."`) would have silently dropped the unmarked leading person if auto-split | `_build_pack_result()` requires `labels[0] == 1` to reach `"high"`; otherwise `"low"` with `missing_leading_marker`, segments still surfaced as candidates | `test_a_code_missing_leading_marker_downgrades_to_low_not_lost` |

---

## 3. Full-Dataset Validation (all 51,123 records, real production text)

Run via `clean_person_names()` and `detect_multi_accused()` directly against `Full Name`/`Alias` from `DOPAMS_Criminal_Profiles_2026-11-18_16-47.xlsx` — no database involved, nothing written anywhere. As established in the audit, this export reflects the **legacy, pre-ETL3** pipeline (production cutover to ETL3's own tables has not happened), so this validates what ETL3 *would* produce against real, unfiltered production text, not a re-check of something ETL3 already touched.

### 3.1 Name cleaning

| Metric | Count | % of 51,123 |
|---|---:|---:|
| Processed (non-null result) | 51,123 | 100% |
| `cleaned_full_name` changed from raw | 586 | 1.15% |
| `cleaned_full_name` unchanged (already clean, or correctly left alone) | 50,537 | 98.85% |
| **Identified** (`name_identified=True`) | 50,950 | 99.66% |
| **Unidentified** (`name_identified=False`) | 173 | 0.34% |
| — reason: `invalid_literal` | 164 | |
| — reason: `relationship_only` | 4 | |
| — reason: `alias_only` | 3 | |
| — reason: `role_only` | 2 | |
| Alias extracted (any) | 5,220 | 10.21% |
| — records with 2 aliases | 5 | |
| — records with 3 aliases | 1 | |
| Relationship extracted | 6 | |
| Role hint extracted (parenthetical) | 30 | |
| Nationality hint extracted (parenthetical) | 5 | |
| Has a non-destructive review flag | 1,970 | 3.85% |
| — `duplicate_token` | 1,967 | |
| — `initial_only_name` | 3 | |

**Safety checks (every one of the 51,123 records, both directions):**
- **Raw-preservation check:** `raw_full_name` byte-identical to the source value fed in — **0 failures**.
- **Idempotency check:** feeding a record's own `cleaned_full_name` back through `clean_person_names()` a second time produces the identical result — **0 failures** (after fixing 2 issues found mid-validation; see §4).

### 3.2 Multi-accused detection

| Confidence | Count | % |
|---|---:|---:|
| `none` (single name, nothing to split) | 50,974 | 99.71% |
| `high` (safe to auto-split) | 11 | 0.02% |
| `low` (flagged for review, never auto-split) | 138 | 0.27% |

Method/warning breakdown for the 149 non-`none` records:

| Method / warning | Count |
|---|---:|
| `numbered_pack_v1` (high confidence, `N.`/`N)` style) | 11 |
| `unnumbered_candidate` (comma/`&`/`and`, flagged low) | 133 |
| `numbered_pack_ambiguous` — `missing_leading_marker` | 3 |
| `numbered_pack_ambiguous` — `weak_space_boundary` | 1 |
| `numbered_pack_ambiguous` — `non_sequential_marker_numbers` | 1 |

No `a_code_pack_v1` (high-confidence A-code) result occurred in this particular export — every real A-code pack found (S.No. 63, 68) has an unmarked leading accused and correctly stays `low` via the new `missing_leading_marker` guard; S.No. 985 has a gapped A-1/A-3 sequence and correctly stays `low` via `non_sequential_marker_numbers`. The A-code `high`-confidence path (`test_a_code_markers_split_at_high_confidence_when_unambiguous`) is confirmed working via direct unit tests with the exact shapes the audit's recommendation targets (`"A1. Person A, A2. Person B"`); this dataset simply doesn't happen to contain a clean, gap-free, from-1 real example of it.

### 3.3 Representative multi-accused examples (before → after)

**High confidence — auto-split-eligible (segments shown, not yet written anywhere):**

| S.No. | Raw | Segments |
|---:|---|---|
| 4 | `1) Abhishek Kumar, 2) Golla Srinivasulu` | `Abhishek Kumar` / `Golla Srinivasulu` |
| 10 | `1). Divi Sampath, 2). Syed Ameer Ali, 3). Bebri Koushal Naik, 4). Kattimani Mandira` | 4 segments (newly detected — `"N)."` fix) |
| 18 | `1. Karrothu Chandra, 2. Borrothu Prakash , 3. Maddula Venkata Shiva Shekar and 4. Manthri Shiva Sai` | 4 segments |
| 24 | `1) N.Nagesh, 2) Lakkaraju Tirumal Rao` | `N.Nagesh` / `Lakkaraju Tirumal Rao` |
| 25 | `1. Revelli Bhudevi and 2. Revelli Pochavva` | `Revelli Bhudevi` / `Revelli Pochavva` |

**Low confidence — newly surfaced for review (previously silent `"none"`):**

| S.No. | Raw | Candidates | Why not auto-split |
|---:|---|---|---|
| 9 | `1. Bontha Chandramouli 2. Bathula Santhosh Reddy 3. Thodusu Mallesh 4. K.Anil` | 4 segments | `weak_space_boundary` — no comma/`and` separator at all |
| 63 | `Bishan Singh, A2. Karnati Sharath Kumar, A3. Badari Sai Reddy` | `Karnati Sharath Kumar` / `Badari Sai Reddy` | `missing_leading_marker` — "Bishan Singh" has no A-code; auto-splitting from A2 would have silently dropped them |
| 68 | `Dudekula Moulali, A2. Rajesh, A3. Boddu Omkar and A4. Subramanya Raj Kumar` | `Rajesh` / `Boddu Omkar` / `Subramanya Raj Kumar` | `missing_leading_marker` |
| 985 | `Accused Person: A-1: Md.Khaleel , A-3: G.Prakash All` | — | `non_sequential_marker_numbers` — A-2 is missing |
| 144 | `Hari, Nagulakatta Zaheerabad` | `Hari` / `Nagulakatta Zaheerabad` | `unnumbered_list_not_auto_split` — known false-positive-prone shape (locality, not a 2nd person); documented limitation, see §6 |

### 3.4 Representative name-cleaning examples (random sample, not cherry-picked)

Pulled via `random.seed(42)` over all 586 changed records, for an unbiased spot-check beyond the audit's own curated cases:

| S.No. | Raw | Cleaned |
|---:|---|---|
| 7 | `1. Avula Yesu` | `Avula Yesu` |
| 1419 | `Ajay Alias Ajay Sharma` | `Ajay` |
| 8086 | `Bunty Bro, R/O: Habsiguda, Ph: 7330891814` | `Bunty Bro` |
| 8190 | `Carolina Cynthiya Harrison (Consumer) Harrison` | `Carolina Cynthiya Harrison Harrison` (role stripped; trailing duplicate surname correctly left alone + flagged, see §2) |
| 17755 | `Karthik Ayan-6302248653` | `Karthik Ayan` |
| 41359 | `Shail Sanghi-7995232555, 9550009475` | `Shail Sanghi` |
| 48612 | `Varun Lanco, Ph: 7075475557` | `Varun Lanco` (found broken mid-validation, fixed — see §4) |
| 899 | `Absconding` | `None` (`name_identified=False`, `unidentified_reason="invalid_literal"`) |
| 47706 | `Unknown Person` | `None` (`invalid_literal`) |

---

## 4. Issues Found and Fixed *During* Validation (self-correction, not guesswork)

Full-dataset validation is only useful if it's actually used to catch real bugs — it caught three, all fixed and re-verified before this report was written:

1. **Age-regex ordering bug:** the bare `"N years"` pattern ran before the labeled `"Age: N years"` pattern and partially consumed the digits first, leaving a dangling `"Age."` fragment (`"Aditya Kumar , Age. 25 Years, ..."` → `"Aditya Kumar , Age"` instead of `"Aditya Kumar"`). Fixed by reordering the two regex lines. Caught by the `test_spelled_out_age_and_occupation_label_stripped` unit test, before the full-dataset run.
2. **Alias-chain-with-pre-existing-alias bug:** when a record's `alias` field already had a value (from the `Alias` column), the new `@`/`"Alias"`-chain extraction was skipped entirely — so the chain was never *stripped* from the name either, leaving `"Mannem Abhishek Alias Chandu"` unchanged on a first pass (but correctly cleaned on a second pass with no alias param — an idempotency failure). Caught by the full-dataset idempotency check (5 failures), fixed by always stripping the chain and only conditionally *assigning* `ali`, re-verified at 0 failures.
3. **`"Ph:"` (no `"No"`) label gap:** `"Varun Lanco, Ph: 7075475557"` → `"Varun Lanco, Ph"` (dangling label) because the existing pattern required the literal word `"No"` between `"Ph"` and the colon. Caught by manually spot-checking a random sample of changed records (not by an automated check), fixed with a new pattern, re-verified.
4. **Empty-parens-then-double-space bug**, exposed by fix #3: removing a phone number from inside its own parens (`"(Ph: 9346843200)"`) left bare `"()"`, whose removal ran *after* the whitespace-collapse step and so left a double space behind — a second idempotency failure. Fixed by reordering the empty-parens cleanup to run before the whitespace collapse.

After all four fixes: **0 destructive-check failures, 0 idempotency failures, across all 51,123 records, confirmed by the final validation run.**

---

## 5. Migration Readiness Assessment

### 5.1 `018_multi_accused_occurrences.sql` — **already applied**

The audit report stated this migration was "not applied anywhere in this repository" — that was correct as a reading of the repository's own history at the time, but a **direct, read-only inspection of the live `dopams_cctns_v2` database** (via `connections.get_unified_connection(readonly=True)`, `SELECT` only, no writes) during this implementation shows it has since been applied:

```
_migrations table: 018_multi_accused_occurrences.sql applied 2026-10-24 01:06:33 UTC
accused_unified has: derived_from_accused_id, split_segment_index, split_parser_version, split_superseded_at
persons_unified has: derived_from_person_id, split_segment_index
accused_split_audit table exists, with the expected columns and a unique index on (parent_accused_id, segment_index) WHERE status='active'
```

The feature has also already been **exercised successfully** at least once: 19 real split-child rows exist in `accused_unified`/`accused_split_audit`, covering 8 distinct parent records, all matching the exact high-confidence `N.`/`N)` shapes this implementation's tests target (e.g. `"1) Abhishek Kumar, 2) Golla Srinivasulu"` → two real children, confirmed by direct query). **No action needed on this migration — it is done, safe, and already working in production data.**

What is still true from the audit: `ETL3_MULTI_ACCUSED_SPLIT_ENABLED` is an explicit opt-in flag (not left permanently on), so whether it's enabled for any given run is an operational decision outside this task's scope — this report only confirms the migration and the code path both work when it is.

### 5.2 `022_person_enrichment_identity_fields.sql` — **new, prepared, NOT applied**

Adds 5 nullable columns to `person_enrichment`: `name_identified BOOLEAN`, `unidentified_reason VARCHAR(30)`, `extracted_role_hint VARCHAR(50)`, `extracted_nationality_hint VARCHAR(50)`, `review_flags TEXT[]`.

- **Preconditions:** none beyond `person_enrichment` existing (confirmed present, 54,507 rows, read-only).
- **Schema compatibility:** every new column is nullable, `ADD COLUMN IF NOT EXISTS` — purely additive, cannot conflict with any existing row, column, constraint, or index.
- **Existing records:** unaffected — a backfill is not required; every existing row simply gets `NULL` in the new columns until the (separate, not-yet-made) decision to also update `runner.py`'s upsert column list and re-run enrichment.
- **Uniqueness/FK relationships:** none introduced.
- **Rerun behaviour:** `ADD COLUMN IF NOT EXISTS` makes the migration itself safely rerunnable.
- **Rollback:** `ALTER TABLE person_enrichment DROP COLUMN IF EXISTS <each column>` — trivial, no data in any other table references them.
- **Exact command, if and when approved:**
  ```
  python etl3/migrations/run_migration.py 022_person_enrichment_identity_fields.sql
  ```
  Expected impact: 5 new, always-`NULL`-until-wired, nullable columns on `person_enrichment`. No row is modified. No other table, view, or consumer is affected. **This was NOT run as part of this task** — it is a file in the repository, awaiting separate review and explicit approval, exactly as instructed.
- **Deliberately not yet wired to a writer:** `project.py:person_row()` already computes all 5 values on every call (visible to tests and any offline script right now), but `runner.py`'s `_person_enrichment()` upsert column list was **not** extended to include them — doing so before the migration is applied would make every `_person_enrichment` run fail with "column does not exist" on literally every row. The two steps (apply migration, extend the column list + move the 5 assignments in `person_row()` to before the `input_hash` line so a future genuine change to one of them is detected) are documented together in the migration file's own header and in `project.py`'s inline comment, so whoever flips this on has the exact two-step sequence in front of them.

### 5.3 Observation (no action taken — out of scope)

While doing the read-only inspection above, `be_read.person`/`be_read.accused` (the backend-read-contract views also living in `etl3/migrations/`) were found to **not** expose `cleaned_full_name` — a separate migration, `019_backend_read_contract_cleaned_names.sql`, that would have added it was never applied (likely lost to a filename collision with an unrelated `019_drug_finding_status.sql`, which *was* applied). This is a `be_read.*` / application-read-path concern, explicitly out of this task's scope ("do not modify... application database read paths") — noted here only so it isn't mistaken for something this task was supposed to fix.

---

## 6. Remaining Limitations (by design, not oversight)

- **Bare (unparenthesized) nationality/geography words are never stripped from a name.** Confirmed unsafe: `"China"` (via `address.NATIONALITY_TO_COUNTRY`'s own `"china"` entry) is a real accused's given name in this exact dataset. Only the clearly-bounded parenthetical form is touched.
- **A-code packs still require the first marker to be label 1 to auto-split.** A real pack with an unmarked leading accused (`"Bishan Singh, A2. ..., A3. ..."`) is deliberately never auto-split — it is surfaced as a `low`-confidence candidate instead, so a human decides, rather than this code guessing whether "Bishan Singh" belongs in the split at all.
- **Comma-separated name + trailing locality** (`"Hari, Nagulakatta Zaheerabad"`) is still flagged as a 2-person candidate even though it's really one person plus an address fragment — the existing `_unnumbered_candidates` heuristic has no way to distinguish a place name from a person name using text alone. Fixing this would mean cross-referencing the `District`/`Address` source fields, which `multi_accused.py`'s pure, DB-agnostic `detect_multi_accused(raw_name: str)` signature doesn't currently receive — a larger, separate change not attempted here.
- **Mixed-style or garbled marker text** (e.g. `"Jeevan Prajapathia-2. Jithen Prajapathi, A-3. Shanthabai"`, where a marker is glued onto the previous word with no boundary at all) is not reliably parseable and correctly stays `"none"` rather than guessing.
- **Trailing duplicate tokens** (`"A. Lokesh Lokesh"`, 1,967 records) are flagged (`review_flags`) but never rewritten — intentionally conservative, since `collapse_exact_repetition` only ever collapses an exact *whole-value* tile, not a single trailing repeat, to avoid guessing which word is the "real" one.
- **The new identity-metadata fields are not yet persisted to the database** — see §5.2. They are fully computed, tested, and validated against the real dataset, but wiring them into the live schema is a follow-up step gated on migration approval, by design.
- **Deduplication (`etl3/identity/person_matching.py`) was not touched**, per explicit instruction. It still reads raw `persons_unified.full_name`, not `person_enrichment.cleaned_full_name` — unaffected by anything in this implementation, confirmed by code inspection, not changed.

---

## 7. Completion Checklist

| Item | Status | Evidence |
|---|---|---|
| All identified name-cleaning bugs addressed | ✅ | §2 table maps every audit finding to a fix; 3 additional bugs found and fixed during validation (§4) |
| Multiple `@` alias extraction fixed | ✅ | `test_multi_alias_at_chain_preserves_every_alias`; 0 idempotency failures across 51,123 records |
| Spelled-out `Alias` extraction implemented | ✅ | `test_spelled_out_alias_word_extraction`; 6 real records with 2+ aliases now correctly captured |
| Relationship extraction improved | ✅ | `test_spelled_out_relationship_markers`, `test_dangling_relationship_marker_stripped_...`; 6 real records extracted |
| Occupation and nationality handling implemented | ✅ | `test_role_phrase_in_parens_extracted_and_stripped`, `test_nationality_in_parens_extracted_but_bare_word_never_touched`; 30 role hints + 5 nationality hints extracted from real data |
| Invalid-name detection implemented | ✅ | `test_invalid_literal_names_marked_unidentified`, `test_role_only_whole_value_marked_unidentified`, `test_relationship_only_record_marked_unidentified_not_invented`; 173 real records correctly marked unidentified |
| A1/A2/A3 detection improved | ✅ | `test_a_code_markers_split_at_high_confidence_when_unambiguous`, `test_a_code_missing_leading_marker_downgrades_to_low_not_lost` |
| Multi-accused splitting validated | ✅ | §5.1 — migration 018 confirmed already applied and already exercised (19 real split children) via read-only live-DB inspection |
| Same-crime relationship preserved | ✅ | Unchanged — `build_split_rows()` in `accused_split.py` was not modified; `crime_id` is carried straight from parent, confirmed by reading the (untouched) code |
| Ambiguous records preserved unchanged | ✅ | `test_weak_boundary_never_promotes_to_high_even_when_otherwise_perfect`, every `"low"`-confidence path in `_build_pack_result`; 138 real records correctly left unsplit |
| Raw names preserved | ✅ | Raw-preservation check, 0 failures across 51,123 records (§3.1) |
| Idempotency verified | ✅ | 0 failures across 51,123 records, after fixing the 2 issues it caught (§3.1, §4) |
| Unit and regression tests passed | ✅ | 41/42 in `test_enrichment.py` (1 pre-existing, unrelated-to-this-task failure confirmed present on the unmodified codebase too — AI retry call-count timing), 40/40 in `test_multi_accused.py`; zero regressions in the other 24 test files in `etl3/tests/` (confirmed by running every one, before and after, on the unmodified vs. modified code) |
| Full Excel dataset validation completed | ✅ | §3 — all 51,123 records processed, stats and representative examples above |
| Migration readiness documented | ✅ | §5 — 018 already applied (confirmed live), 022 prepared with exact apply command, not executed |
| Production ETL execution status documented | ✅ | No ETL run, backfill, or migration was executed against any database by this task. The one live-database access was a read-only `SELECT`-only inspection for the migration-readiness assessment the task itself asked for; the connection was opened via the existing `get_unified_connection(readonly=True)` helper, which enforces a Postgres-level read-only session independent of application discipline. |

**No production records were modified. No migration was executed. No commit was pushed. No file outside `etl3/` and this report/the audit report was touched.**
