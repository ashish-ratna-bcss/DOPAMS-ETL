# DOPAMS ETL3 — Final Deployment Report

**Executed 2026-11-18, explicitly authorized this session, against the live `dopams_cctns_v2` production database.** This completes migration 022, identity-enrichment activation, and the historical backfill described in the three prior reports (`..._EDGE_CASE_AUDIT.md`, `..._VALIDATION_REPORT.md`, `..._ETL_INTEGRATION_REPORT.md`, `..._BACKFILL_REPORT.md`).

## 1. Environment identification (done before any write)

- Target: `dopams_cctns_v2`, host `192.168.103.106`, reachable directly from this session via `etl3/config/.env` and confirmed reachable from the production application server (`dopams-etl`, user `eagle`, path `/home/eagle/dopams-cctns-ai`) via the **same** `UNIFIED_PG_*` settings.
- Confirmed **production**, not a disposable copy: `docs/ETL3_MULTI_ACCUSED_SPLIT_READINESS_REPORT.md` records a prior session's explicit "PRODUCTION EXECUTION" against this exact database (migrations 018/020, 19 split rows, 2026-10-24). This session's row counts (54,516 persons before this work) are consistent with continued live ingestion since that date (53,197 → 54,516 persons over ~3.5 weeks).
- The production application server's deployed code is on an **older commit** (`3d8419c`) that predates every change from this engagement's prior phases. No code was pushed or deployed to that server this session (not requested; git push/deploy is a distinct action from migration/flag/backfill execution). The server's own scheduled ETL3 runs will continue using the pre-this-engagement cleaning logic until a separate, explicit deployment step occurs.

## 2. Migration 022 — applied

| Check | Result |
|---|---|
| Already applied before this session? | No — confirmed via `_migrations` table read before acting |
| Idempotent/guarded? | Yes — `run_migration.py` checks `_migrations` first and refuses a repeat apply; migration SQL itself uses `ADD COLUMN IF NOT EXISTS` |
| Destructive operations? | None — 5 new nullable columns (`name_identified BOOLEAN`, `unidentified_reason VARCHAR(30)`, `extracted_role_hint VARCHAR(50)`, `extracted_nationality_hint VARCHAR(50)`, `review_flags JSONB`) on `person_enrichment` only |
| Pre-migration backup | Targeted `pg_dump -Fc` of the 4 at-risk tables + `change_log` (`person_enrichment`, `persons_unified`, `accused_unified`, `accused_split_audit`), verified restorable via `pg_restore -l`, saved at `/home/eagle/backups/dopams_cctns_v2_pre_migration022_tables_2026-11-18.dump` (103 MB) on the server. *(A full-database `pg_dump` was attempted first, matching the 018/020 precedent exactly, but was aborted after 25+ minutes at 16 GB — this database is far larger than the 242 MB the 018/020 backup covered, almost entirely from data this change never touches. The targeted backup covers the complete, exact blast radius of migration 022 and this backfill.)* |
| Applied | `python etl3/migrations/run_migration.py 022_person_enrichment_identity_fields.sql` — `[OK]`, recorded in `_migrations` at `2026-11-18 14:38:21 UTC` |
| Post-migration row count unchanged | `person_enrichment`: 54,507 before and immediately after (0 rows touched by the DDL itself) |
| Migration 018 compatibility re-check | Confirmed still applied, still intact: `accused_unified`/`persons_unified` split columns present; `accused_split_audit` had 19 rows (8 parents) before this session's backfill — unaffected by migration 022 |

## 3. Feature-flag activation

| Flag | Local `etl3/config/.env` | Server `etl3/config/.env` (`dopams-etl:/home/eagle/dopams-cctns-ai`) |
|---|---|---|
| `ETL3_NAME_IDENTITY_FIELDS_ENABLED` | **Added** (`=1`) | **Added** (`=1`) — inert until the server's code is deployed (see §1) |
| `ETL3_MULTI_ACCUSED_SPLIT_ENABLED` | **Added** (`=1`), to match server | Already `=1` from the 2026-10-24 rollout — unchanged |

Neither flag is hardcoded in source — both remain `os.environ.get(..., "0")`-gated in `etl3/enrichment/runner.py`, exactly as before. No source file was changed to add these flags.

**Backward compatibility verified:** `etl3/tests/test_enrichment.py`'s `test_regular_etl_identity_metadata_flag_gates_hash_only` exercises `person_row(..., persist_identity_metadata=False)` directly and confirms the 5 fields are still computed and returned, just excluded from `input_hash` — disabled mode (for any OTHER deployment that hasn't applied migration 022 yet) remains exactly as before.

## 4. Identity-enrichment persistence — verified with real data

Post-backfill (§ of the companion execution report), queried directly:

```
person_enrichment rows with name_identified populated: 54,520 / 54,520 (100%)
name_identified = false: 191
unidentified_reason breakdown: invalid_literal 189, role_only 2
extracted_role_hint populated: 30
extracted_nationality_hint populated: 5
```

Real examples confirmed non-destructive and correct:
```
raw_full_name='N/A N/A'                        -> cleaned_full_name=NULL, name_identified=false, reason=invalid_literal
raw_full_name='Unknown person R/o: Odisha'      -> cleaned_full_name=NULL, name_identified=false, reason=invalid_literal
raw_full_name='Unknown'                         -> cleaned_full_name=NULL, name_identified=false, reason=invalid_literal
```
`raw_full_name` preserved byte-for-byte against `persons_unified.full_name` in **all 54,520** rows checked — 0 mismatches.

## 5. Multi-accused — verified with real data

8 pre-existing split parents (19 children, from 2026-10-24) confirmed intact and **not duplicated** by this session's run (`write_stats.persons.unchanged: 19`). 5 genuinely new parents split, producing 13 new children, each verified to link to its parent's exact `crime_id` (see the backfill execution report for the full list). 0 PK collisions (`distinct accused_id = total row count`).

## 6. Regular ETL3 verification

Per the constraint "do not trigger a full production ETL run merely for validation," the regular pipeline's own functions (`build_person_row_safe`, `person_row`, `upsert_rows`, `_multi_accused_split`) were exercised via the standalone backfill script and direct unit tests — not via `run_enrichment()`'s full stage list (which also runs AI/drug/chargesheet/etc. enrichment, out of scope here).

| Requirement | Evidence |
|---|---|
| Processes new/updated records with latest cleaning | `test_regular_etl_person_row_uses_latest_name_cleaning_end_to_end`, `test_regular_etl_reenriches_on_name_change` (unit) + every one of the 54,520 real rows processed this session (live) |
| Persists all 5 identity fields when flag enabled | §4 above — real data, not simulated |
| Multi-accused detection, separate occurrences, correct crime linkage | §5 above — real data |
| Preserves aliases/relative names/raw names | Unit tests unchanged from the integration report; raw-name check in §4 (live, 100%) |
| Isolates individual record failures | `build_person_row_safe` unit-tested; live run reported `failed: 0` across 54,520+13 records |
| Does not invoke the historical backfill automatically | Unchanged from the integration report — `grep` confirms no reference anywhere in `runner.py`/scheduler code |

Repeated processing confirmed non-duplicating: the live idempotency dry-run after full execution shows `not_yet_processed: 0, stale_update: 0` (see backfill execution report §4 for the full before/after/idempotency sequence).

## 7. Regression test results

| Suite | Result |
|---|---|
| `etl3/tests/test_enrichment.py` | 49/50 pass. The 1 failure (`ai retry`) is the same pre-existing, unrelated AI-retry call-count timing test confirmed present on the unmodified codebase before any of this engagement's changes (re-confirmed via `git stash` comparison in the implementation phase). One test (`test_regular_etl_identity_metadata_flag_gates_hash_only`) needed a one-line fix this session: it asserted the ambient flag defaulted to off, which stopped being true the moment this session deliberately turned it on in `.env`; fixed to test the mechanism directly via `person_row()`'s own parameter, independent of ambient environment state. |
| `etl3/tests/test_multi_accused.py` | 40/40 pass |
| `etl3/tests/test_name_enrichment_backfill.py` | 12/12 pass |
| Full `etl3/tests/` suite (24 other files) | Identical to the pre-existing baseline established in the implementation phase — no new failures |

No test was silently skipped or excluded; every failure above is named and attributed.

## 8. Remaining limitations

1. **Server code not deployed.** The flag is set on the server but inert until someone pushes/pulls the code. The regular, server-scheduled ETL3 pipeline will keep producing pre-this-engagement output until that separate deployment happens.
2. **13 new split-child records have no `accused_enrichment` row yet** (domicile/address/role/CCL classification, AI brief-facts) — by design, identical to the 2026-10-24 precedent's own documented note: a later `_accused_enrichment`/AI pass picks these up as ordinary new `accused_unified` rows, not this session's job.
3. **139 low-confidence multi-accused candidates remain unsplit**, correctly, pending human review (never auto-split by design).
4. **191 records are now explicitly `name_identified=false`** — correct, not a defect, but worth surfacing to anyone building UI/reports on top of this data: these records previously displayed a placeholder/role word as if it were a real name, and now correctly show no name at all.
