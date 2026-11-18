# DOPAMS ETL3 — Final Validation Report

Synthesizes the full engagement (audit → implementation → ETL integration/backfill utility → this session's deployment) into one before/after record. All figures are from actual database queries run this session against `dopams_cctns_v2`, not fabricated or carried forward unchecked.

## 1. Before / after — person name enrichment

| | Before this engagement (legacy export baseline, `DOPAMS_ETL3_NAME_CLEANING_EDGE_CASE_AUDIT.md`) | Before this session (flag off, code deployed) | After this session (live, flag on, backfilled) |
|---|---:|---:|---:|
| Records with no invalid-name detection at all | 100% | 100% | 0% |
| Records explicitly flagged `name_identified=false` | Not tracked (placeholder text like "Unknown"/"Absconding" displayed as a name) | Computed but not persisted | **191 persisted, verified** |
| Multi-`@`-alias chain bug | Confirmed present (silent data loss + `@` leaking into name) | Fixed, unit-tested | Fixed, confirmed on live data — 0 idempotency failures |
| Spelled-out `"Alias"` recognized | No | Yes | Yes — live data confirms extraction |
| A-code (`A1.`/`A-2:`) multi-accused packs | Never split, not even flagged | Detected, `labels[0]==1` guard added | **2 real A-code packs split live** (Malluvarasa/Jajimoggala; Bishan Singh/Karnati/Badari) |
| `"N)."` punctuation variant | Invisible | Fixed | **1 real 4-person pack split live** (Divi Sampath et al.) |

## 2. Data-quality improvement, measured on real data (this session)

```
54,520 person_enrichment rows now carry identity metadata (0 did before)
15,890 rows have an objectively different (cleaned) name than before
   191 rows now correctly show no name instead of a placeholder
    30 rows have a role word separated out of the display name
     5 rows have a nationality word separated out of the display name
    13 new person/accused occurrences created from 5 previously-unsplit,
       genuinely multi-person CCTNS records
     0 new occurrences created from the 139 records correctly judged too
       ambiguous to split
```

## 3. Raw-name preservation — 100%, all rows

```
54,520 / 54,520 person_enrichment.raw_full_name values verified byte-identical
  to their source persons_unified.full_name value. 0 mismatches.
```
This is the strongest, most direct evidence against the one failure mode that would matter most here: the enrichment process altering or losing the original source text. It does not, anywhere, in the live dataset.

## 4. Idempotency — proven on live production data, twice

```
Run A (post-split, before the follow-up enrichment pass):
  not_yet_processed=13, stale_update=0, already_current=54,507
Run B (final, after the follow-up pass):
  not_yet_processed=0, stale_update=0, already_current=54,520
```
Zero duplicate accused/person occurrences, zero duplicate enrichment rows, zero unintended identity changes — verified by exact row-count reconciliation across `persons_unified`, `accused_unified`, `person_enrichment`, and `accused_split_audit` (see the companion backfill execution report §3), not inferred from the write-stats alone.

## 5. Database integrity checks

| Check | Result |
|---|---|
| PK collisions (`accused_unified`) | 0 — `distinct accused_id = total row count` (51,837 = 51,837) |
| PK collisions (`persons_unified`) | 0 — `distinct person_id = total row count` (54,529 = 54,529) |
| Every new split child's `crime_id` matches its parent's | 13/13 |
| Pre-existing 8 splits (19 children) intact, unduplicated | Confirmed — `write_stats` shows `unchanged: 19` for persons/accused/audit on the real execute run |
| Unrelated tables untouched | `crime_enrichment` (17,348), `accused_enrichment` (51,815), `drug_extractions` (37,914) — present, row counts consistent with no write from this session |
| Migration 022 schema | 5 nullable columns, correct types, confirmed via `information_schema.columns` |
| Migration 018 (re-checked this session) | Still applied; split columns and `accused_split_audit` intact and unaffected by migration 022 |

## 6. Test coverage

| Suite | Pass rate | Notes |
|---|---|---|
| `test_enrichment.py` | 49/50 | 1 pre-existing, unrelated failure (AI-retry timing — confirmed present before any of this engagement's changes via `git stash` comparison in the implementation phase); 1 test fixed this session (stale ambient-environment assumption, not a code defect) |
| `test_multi_accused.py` | 40/40 | |
| `test_name_enrichment_backfill.py` | 12/12 | |
| Full `etl3/tests/` (24 other files) | Same pre-existing pass/fail pattern as the unmodified baseline | No new failures introduced by any change in this engagement |

No test was skipped, excluded, or silently reclassified. Every failure above is named.

## 7. Regular ETL verification

Confirmed (see the Final Deployment Report §6 for detail) that `etl3/enrichment/project.py:person_row()`, `etl3/enrichment/runner.py:build_person_row_safe()`/`_person_enrichment()`/`_multi_accused_split()` are the **same, single, unduplicated** implementation used both by the regular pipeline and by the standalone backfill — proven by the live execution itself using exactly these functions, not a parallel implementation.

## 8. Backfill / regular-ETL separation — re-verified

```
grep -rn "run_name_enrichment_backfill" etl3/ --include="*.py" \
  | grep -v "tests/test_name_enrichment_backfill.py\|run_name_enrichment_backfill.py:"
```
→ zero matches, re-confirmed after this session's execution. The backfill script was invoked exactly 6 times this session (1 initial dry-run from the prior task, 1 fresh dry-run, 2 person executes, 1 multi-accused execute, 2 closing dry-runs) — every single time by explicit, individual command from this session, never from `run_enrichment()`, a scheduler, or any automatic trigger.

## 9. What this validates, and what it does not

**Validated:** the name-cleaning, alias/relationship/role/nationality extraction, invalid-name detection, and multi-accused splitting logic is now live, correct, idempotent, and non-destructive against the real, full, 54,000+-record production dataset — not a sample, not a copy.

**Not validated by this session** (out of scope, per the task's own restriction): any frontend, backend API, GraphQL, or `be_read.*` read-path behavior. The live frontend/backend still reads the legacy `firs_mv`/`accuseds_mv` views per `etl3/docs/PHASE_7_DOPAMS_CUTOVER_READINESS.md` — none of today's writes are visible to end users until that separate cutover work happens. This session's deployment target was exclusively ETL3's own tables.
