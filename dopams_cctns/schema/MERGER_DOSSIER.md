# CCTNS V1 + V2 → Unified DOPAMS Database — Merger Planning Dossier

**Status:** Planning document. No production data, schema, or ETL code was modified to produce this. All database statements below are read-only queries against `cctns_v1` (V1) and `cctns-v2` (V2) on `192.168.103.106`.

**Evidence tags used throughout:**
`[CODE]` = read directly from the ETL source. `[DB]` = read-only query against the live database, this session. `[API]` = a live call to a source API, this session. `[INFERRED]` = a reasoned conclusion from `[CODE]`/`[DB]` evidence, not independently confirmed. `[UNKNOWN]` = genuinely not established; do not assume.

**As-of:** 2026-10-01, ~12:47 UTC (18:17 IST). V1 at commit `053d972` (branch `cctns-v1`, HEAD `179fb2a`). V2 at commit `81be39c` (branch `cctns-v2`, HEAD `a05f6c5`).

---

## 1. Executive Summary

V1 (1991–2022-10, 7,305 FIRs, static) and V2 (2022-06–present, 9,535 crimes, growing) are **non-overlapping in time and in case identity** `[DB]` — zero shared `fir_reg_num` across 7,305 V1 FIRs and 9,535 V2 crimes, confirmed by exact join. This is the single most important fact in this dossier: **the merge at the case/crime/accused/arrest/seizure/court level is a union, not a conflict-resolution problem.** There is nothing to reconcile at that level because nothing overlaps.

The real problem is narrower: **the same real person can appear as an accused in a pre-2022 V1 FIR and again in a post-2022 V2 crime, under two completely unrelated, unlinked IDs.** `[DB]` ~300–330 candidate same-person pairs were found via name/father-name/phone matching across ~17,000–33,000 people per side — a low-confidence signal (DOB is ~99% NULL in both systems `[DB]`), not a safe automatic match.

Both source ETLs have active, recently-fixed defects, verified live this session:
- V1's accused dossier previously collapsed distinct records onto a 12-field key (fixed, `[DB]` verified: 17,039 → 34,384 rows) and then, after a stricter fail-closed fix, **froze entirely** because 180 single-day Oracle buffer-overflow windows are permanent and unfixable in the ETL `[DB]`. That freeze was resolved during this session (`[DB]`, run 56: `loaded_with_known_gaps`) by classifying known single-day ORA-06502 failures as accepted and non-blocking, while still fail-closing on anything unexpected.
- V2's window-based incremental checkpoint previously could advance past a failed fetch silently; this is fixed and confirmed deployed and exercised `[DB]` + operator-reported, cross-checked exactly against the live `etl_bookkeeping` table.

**Readiness verdict for Phase 1 (crime/case-scoped union):** both ETLs are in a state where their current databases can be read and unioned safely. No open ETL-level blocker remains for that scope.

**Readiness verdict for persons/identity linking (later phase):** not ready to automate — the matching signal is too weak (no shared ID, near-total DOB nullness) to safely auto-merge. A human-reviewed candidate-link model is required (§16, §9).

---

## 2. Current V1 Architecture `[CODE]`

```
CCTNS V1 API (4 endpoints, no auth, HTTP)
     │
     ├── GET  firdata            → cctns.cctns_fir            (unfiltered, full pull)
     ├── GET  court               → cctns.cctns_court           (unfiltered, full pull)
     ├── GET  accusedetails       → cctns.cctns_accused_details (unfiltered, full pull)
     └── POST Accused (date range)→ cctns.cctns_accused         (7-day chunks, adaptive halving on ORA-06502)
                     │
                     ▼
         Airflow 2.10, LocalExecutor, PM2-managed, host "tganb-db"
         DAG 1: cctns_v1_daily_sync_fir_court_accused_details  (00:30 IST)
         DAG 2: cctns_v1_daily_sync_accused_dossier            (01:30 IST, waits on DAG 1 success via ExternalTaskSensor)
                     │
                     ▼
         Postgres `cctns_v1` — schema `cctns` (business) + schema `airflow` (metadata), same database
```

Entry points: `dags/daily_sync_fir_court_accused_details.py`, `dags/daily_sync_accused_dossier.py` → both call `dags/pipeline_run.py::run_single_entity()`.

**Concurrency control** `[CODE]`: `db/run_lock.py` — a non-blocking `fcntl.flock` on `/tmp/cctns_v1_etl_{entity}.lock` (default dir, overridable via `CCTNS_V1_ETL_LOCK_DIR`). Process-level, host-level. The OS releases the lock automatically on process death — a crash does not leave the lock held, but it does leave the corresponding `cctns_v1_etl_run_log` row stuck in `status='running'` forever, since nothing updates it after the process is gone `[DB]`: 2 such orphaned rows exist (ids 1, 14, from 2026-09-28/09-30). Additional safeguards added this session `[CODE]`: Airflow `max_active_runs=1` on the dossier DAG, and the `ExternalTaskSensor` upstream wait.

**Config** `[CODE]`: `.env` at `cctns-v1/CCTNSV1_DAILY_ETL_RUN/.env`. `PG_HOST=192.168.103.106`, `PG_DATABASE=cctns_v1`, `PG_ETL_SCHEMA=cctns`, `PG_AIRFLOW_SCHEMA=airflow`, `ACCUSED_FULL_PULL_START_DATE=01-01-2002`. API URLs point at `103.164.200.184` (masked host; full value withheld). `CCTNS_REQUEST_TIMEOUT_SECS` default 300 (raised from 60 this session).

---

## 3. Current V2 Architecture `[CODE]` + operator-confirmed

```
CCTNS V2 API (13 endpoint groups, x-api-key auth, two base URLs :3000 / :3001)
                     │
      master_etl.py --pure-cctns --env prod   (file-lock /tmp/master_etl.lock)
      cron: 05:30 / 11:30 / 17:30 / 23:30 IST, config=input.cctns-pure.txt   [operator-confirmed]
      Address resolution and brief_facts_ai are NOT in this config — do not run in production.
                     │
      ~27 ordered steps: hierarchy → crimes → class_classification → case_status →
      accused → persons → properties → IR → disposal → arrests → chargesheets →
      update_chargesheet → fsl_case_property → file discovery/download
                     │
                     ▼
         Postgres `cctns-v2` — schema `public` only, 17 tables, no foreign-key constraints
         (one new exception added this session: UNIQUE(crime_id, accused_seq_no) on arrests)
```

**Incremental mechanism** `[CODE]`, confirmed live this session `[DB]`: per-module watermark in `etl_bookkeeping(kind='run_state')`, each module computing its own start cursor as `GREATEST(MAX(date_created), MAX(date_modified))` from its own table. A window-guard (`etl_window_guard.py`, commit `f2ec7c0`) runs date windows sequentially, writes a `<module>__replay_from` floor row *before* any window in the run commits, and only clears it on full success — confirmed exercised live (`hierarchy__replay_from` caught mid-run this session). A half-open-`toDate` fix (commit `826266d`) corrects a bug where list endpoints that filter `[fromDate, toDate)` were silently dropping the last inclusive day, including same-day windows — confirmed by a positive test (operator-reported, cross-validated: same-day windows now return non-zero rows).

**FK-retry queue** `[CODE]`: `etl_fk_retry_queue.py`, table `etl_bookkeeping(kind='fk_retry')`, max 5 attempts, then permanently capped (not deleted, stays visible).

---

## 4. V1 Database Inventory `[DB]`, refreshed this session

| Table | Rows | PK | Unique | FK |
|---|---|---|---|---|
| `cctns.cctns_fir` | 7,305 | `fir_reg_num` | — | — |
| `cctns.cctns_accused` (dossier) | 34,384 | `accused_id` (bigint, local sequence) | `natural_key` = MD5 of all 140 columns | `fir_reg_num` → `cctns_fir` ON DELETE CASCADE |
| `cctns.cctns_accused_details` | 20,198 | `accused_id` (bigint) | `natural_key` = MD5 of 11 fields | `fir_reg_num` → `cctns_fir` ON DELETE CASCADE |
| `cctns.cctns_court` | 7,531 | `court_id` (bigint) | `natural_key` = MD5 of 7 fields | `fir_reg_num` → `cctns_fir` ON DELETE CASCADE |
| `cctns.cctns_v1_audit_log` | 336 | `id` | — | field-level old/new, non-key UPDATE only |
| `cctns.cctns_v1_etl_run_log` | 56 | `id` | — | per-run summary |
| `cctns.cctns_v1_etl_row_action` | 69,539 | `id` | — | per-row insert/update log |
| `cctns.cctns_v1_failed_fetch_window` | 180 | `id` | `(entity, window_start, window_end)` | durable OPEN/RESOLVED ledger, added this session |
| `airflow.*` | 48 tables | — | — | Airflow 2.10 internal metadata, separate schema, same DB |

**Orphans:** 0 across all three child tables `[DB]` (FK-enforced; confirmed by independent join, not just trusting the constraint). **Duplicate natural keys:** 0 `[DB]` (unique constraint holds). **Stale-duplicate rows** (old version retained alongside a new one because any field change produces a new MD5 key): 81 dossier, 1 accused_details, 5 court `[DB]` — these are not orphans or corruption, they are intentional retained history, just unflagged as such.

---

## 5. V2 Database Inventory `[DB]`, refreshed this session

| Table | Rows | PK | Unique | FK (enforced) |
|---|---|---|---|---|
| `crimes` | 9,535 | `crime_id` | `fir_reg_num` not constrained (verified unique in data: 0 dup groups) | none |
| `accused` | 32,867 | `accused_id` | none enforced | none |
| `persons` | 32,790 | `person_id` | — | none |
| `arrests` | 32,862 | `id` (uuid) | **`(crime_id, accused_seq_no)` — added this session, confirmed live, 0 dup groups** | none |
| `chargesheets` | 7,061 | `id` (uuid) | `charge_sheet_id` | none |
| `charge_sheet_updates` | 6,163 | `id` | `update_charge_sheet_id` | none |
| `disposal` | 470 | `id` | `(crime_id, disposal_type, disposed_at)` | none |
| `mo_seizures` | 3,534 | `mo_seizure_id` | — | none |
| `properties` | 7,646 | `property_id` | — | none |
| `fsl_case_property` | 2,003 | `case_property_id` | — | none |
| `interrogation_reports` | 19,497 | `interrogation_report_id` | — | none |
| `hierarchy` | 816 | `ps_code` | — | — |
| `file_media_bookkeeping` | 165,864 | `id` (uuid) | 3 partial indexes | — |
| `etl_bookkeeping` | 4,850 | `id` | `(kind, module_name)` for checkpoint/run_state; `(kind, module_name, record_key)` for failure/fk_retry | — |

**Orphans** `[DB]`, refreshed: `accused→crimes` 0, `accused→persons` 0, `arrests→crimes` 0, `chargesheets→crimes` 0, `mo_seizures→crimes` 0, `properties→crimes` 0, `disposal→crimes` 0. **One non-zero:** `interrogation_reports→persons` = 11 orphans (person_id set but not present in `persons`). No FK constraint exists to prevent this class of orphan on any table except the new arrests unique index.

---

## 6. Schema Comparison

| | V1 | V2 |
|---|---|---|
| Normalization | None — flat wide tables, 60 `INT_*` relative columns on the dossier row | Arrays/JSONB for repeating groups, separate `persons`/`accused` tables |
| IDs | Local bigint sequences (dossier/details/court) + `fir_reg_num` (source, stable) | Mongo-style ObjectId strings, source-generated, stable |
| FK enforcement | Full — 3 FKs, `ON DELETE CASCADE` | None, except the one new arrests unique index |
| Change detection | Full-row MD5 natural key → new row on any change, old row retained | In-place NULL-preserving upsert, single current row |
| History | Accidental, via retained stale rows; partial field-level audit log (non-key fields only) | None |
| Bookkeeping | `run_log` + `row_action` + `audit_log` + `failed_fetch_window` (4 tables) | Single consolidated `etl_bookkeeping` (4 `kind` values) |

---

## 7. Data Quality Comparison `[DB]`

| | V1 | V2 |
|---|---|---|
| Orphans | 0 (enforced) | 0 of 8 checked relationships; 11 IR→persons (unenforced) |
| Duplicate business keys | 0 | 0 (crimes `fir_reg_num`; arrests `(crime_id, accused_seq_no)` as of this session) |
| Unflagged historical duplicates | 81+1+5 = 87 rows, growing with every FIR status change | None (upsert model has no equivalent) |
| Bounded, unfixable source gaps | 180 single-day ORA-06502 windows, 2002–2022, now in a durable ledger | 1,150–1,152 permanently-capped FK-retry rows (90 chargesheets, 168 updated_chargesheet, 892–894 fsl); 1,627 address-resolution failures, frozen since the address step stopped running in prod on 2026-09-29 |

---

## 8. Entity Mapping

| V1 | V2 | Same entity? |
|---|---|---|
| `cctns_fir` | `crimes` | Same concept. Zero row overlap. |
| `cctns_accused` (dossier) + `cctns_accused_details` | `accused` + `persons` (split) | Partially equivalent — V1 packs person/seizure/accused into one row; V2 separates them. |
| (arrest fields inside `cctns_accused_details`) | `arrests` | Partially equivalent — V2 has explicit 41A/apprehended/absconding/died flags V1 lacks structured columns for. |
| (drug fields inside dossier) | `mo_seizures`, `properties`, `fsl_case_property` | Different structure, same general concept. |
| `cctns_court` | `chargesheets`, `charge_sheet_updates`, `disposal` | Partially equivalent — V1 is one flat table, V2 splits by lifecycle stage. |
| — | `hierarchy` | V2-only. |
| — | `interrogation_reports` | V2-only, no V1 equivalent beyond scattered `INT_*` relative columns. |
| (`attach_path`/`dms_file_name` strings) | `file_media_bookkeeping` | No shared identity scheme. |

No pair is a `DIRECT_EQUIVALENT`.

---

## 9. Canonical Key Analysis

| Entity | V1 identity | V2 identity | Shared key? |
|---|---|---|---|
| FIR/Crime | `fir_reg_num` (varchar13, `ps_code(7)+yy(2)+seq(4)`, source-stable) `[DB]` | `crime_id` (ObjectId) + `fir_reg_num` (same scheme, 9,519/9,523 conforming `[DB]` from earlier audit) | **`fir_reg_num` uses the same scheme in both, but 0 values are shared** `[DB]` — confirmed by exact join, not inferred |
| Person | `person_code` (18-digit, prefixed by its FIR number, confirmed `[DB]`: 20,128/20,128 start with their own fir_reg_num) | `person_id` (ObjectId), confirmed scoped to exactly one crime in prior audit (32,748/32,748) | **None.** No reuse of either scheme across systems. |
| Accused | local bigint `accused_id` (V1, churns on every MD5 key change) | `accused_id` (ObjectId, stable) | None |

**Cross-system identity does not exist for any entity.** `[DB]`. Where the same real person/FIR exists in both systems, the only usable signal is attribute matching: name, father/relative name, phone, address, station+year (for FIR, moot since no overlap exists). Do not invent a canonical cross-system key — none is supported by evidence.

---

## 10. V1/V2 Overlap Analysis `[DB]`

**FIR/Crime:**
```
V1 only: 7,305        V2 only: 9,535        Both: 0
```
Confirmed by exact join on `fir_reg_num`, both directions. At every one of 120 police stations present in both systems' 2022 data, V2's sequence numbers start strictly after V1's maximum for that station `[DB]`, and V2's dates start after V1's last FIR date for that station — a clean sequential handover, not an overlap.

**Person (approximate, via attribute matching, not canonical ID):**
- V1 `accused_details` × V2 `persons`, matched on normalized (name, father/relative name): 326 shared key-pairs, 428 V2 rows and 421 V1 rows touching them.
- Matched on 10-digit phone: 283 shared numbers, 334 V1 rows with a phone match *and* at least one shared name token.

These are candidate overlaps, not confirmed identity — see §9.

**Everything else** (accused/arrest/seizure/court/property/chargesheet): scoped to crime/FIR, which never overlaps, so these entities inherit zero overlap by construction.

---

## 11. Conflict Analysis

Because there is no case-level row overlap, **there are no field-level conflicts to resolve for crime/FIR/accused/arrest/seizure/court/property between V1 and V2.** This section would ordinarily hold a conflict table; it is empty by evidence, not by assumption — confirmed via the join in §10.

The only place a genuine conflict *could* exist is between a V1 person row and a V2 person row that the §9/§10 matching flags as the same real individual — and even there, "conflict" isn't the right frame, because these are two independent observations of the same person at different times from different systems, not two versions of the same record. See §15 for how to handle disagreement without inventing precedence.

---

## 12. Accused / Arrest Deep Analysis

**V1** `[DB]`: the dossier's natural key includes mutable fields (`arrest_surrender_dt`, the arrest-relevant address fields, etc.). An ABSCONDING→ARRESTED-style change produces a brand-new row; the old row is retained, unflagged. Confirmed live this session with a real example: FIR …0405 moved status, and both `cctns_accused_details` and `cctns_court` ended up with one stale row each alongside the new current one.

**V2** `[DB]`, this session, exact counts:
- 32,337 of 32,860 accused rows have `date_created` **and** `date_modified` identical to their parent crime's (no independent API date ever existed).
- **523 of 32,860** have `date_created` matching the crime but `date_modified` *older* than the crime's — meaning the crime moved forward for some unrelated reason after this accused row's own last real change.
- 0 rows have a newer accused-level `date_modified` than their crime.
- Operator-reported, code-level mechanism: the `/accused` re-fetch window is gated by **arrest-row timestamps**, not accused's own. A status change that doesn't move the arrest timestamp is not guaranteed to be re-fetched by a later window, because the resume cursor tracks accused's own (crime-derived) dates.

**Classification:** V1 accused/arrest status: can be **stale** (old row persists, visible via the audit comparison in §4) but never silently wrong — the current row is identifiable by the ledger/trigger logic. V2 accused/arrest status: **unverifiable** in the strict sense — a single current row exists, but there is no proof it reflects the latest real-world state if the triggering timestamp never moved. This is a materially different risk than V1's: V1's problem is visible (extra rows), V2's is invisible (a row that looks current but might not be).

**Arrests uniqueness** `[DB]`, confirmed this session: a race condition (check-then-insert under concurrent workers) previously allowed one duplicate `(crime_id, accused_seq_no)` pair. Fixed via `INSERT ... ON CONFLICT DO NOTHING` plus a real unique index — confirmed deployed, confirmed 0 duplicate groups now.

---

## 13. Historical Data Analysis

| | V1 | V2 |
|---|---|---|
| Can reconstruct a past state? | Partially — via retained stale rows (no timestamp telling you which was "current" at time T, but the field-level `audit_log` records old→new for non-key field changes) | No — pure upsert, no history table, no audit log |
| Change signal | Full-row content hash | `date_modified` (per-table, not always independently meaningful for accused — see §12) |
| Known unreliable history event | — | **2026-08-24:** 8,288/9,535 crimes (86.9%) and 28,460/32,860 accused (86.6%) show `date_modified`='2026-08-24', with 7,623 of those crimes (92% of that day's touches) landing in the single 09:00 UTC hour `[DB]`, exact counts, confirmed this session. This is administrative/maintenance activity, not 87% of all cases genuinely changing in one day. |

---

## 14. Known Gaps and Limitations — carry forward explicitly, do not fill or discard

**V1:**
- **180 single-day ORA-06502 windows**, 2002–2022, permanent (Oracle-side buffer limit on the source, not fixable in this ETL) `[DB]`, now in `cctns.cctns_v1_failed_fetch_window` as durable `OPEN` rows, one row per (entity, window_start, window_end), spanning 15 distinct years, all currently at `attempt_count=2`.
- 27 FIRs predate the dossier's 2002-01-01 pull start and can never have dossier rows by design.
- 87 stale-duplicate rows (§4), growing by a few each time a FIR status changes.

**V2:**
- **1,150 permanently-capped FK-retry rows** (90 chargesheets, 168 updated_chargesheet, 892 fsl_case_property, all at `attempt_count=5`, the configured max), plus up to 2 more FSL rows that could still resolve — ceiling 1,152 `[DB]`, exact.
- **1,627 persons with unresolved address/domicile data** (1,620 `kb_and_llm_rejected`, 7 `insufficient_geo_signal`), frozen since 2026-09-29 — not because retries exhausted, but because the address-resolution step **does not run at all** in the live `--pure-cctns` production config `[operator-confirmed, cross-validated against etl_bookkeeping timestamps]`. This is a dark feature, not a backlog that will ever shrink under current configuration.
- 11 interrogation-report rows reference a `person_id` not present in `persons`.
- The accused/arrest-timestamp coupling in §12 (523 rows at identified risk, mechanism is structural so the risk applies to future rows too, not just these 523).

**Design requirement this implies:** the unified database must carry a mechanism that represents "this source is known-incomplete for this record/window," not silently present a complete-looking dataset. See §16 (`source_gap_ledger`).

---

## 15. Source Precedence Matrix

| Entity | Field | V1 | V2 | Canonical rule | Reason |
|---|---|---|---|---|---|
| Crime/FIR | any | present | present (different cases) | **No rule needed** | 0 row overlap — nothing to arbitrate |
| Accused/Arrest | any | present | present (different cases) | **No rule needed** | same |
| Person | name/DOB/address/phone, when §10 flags a candidate cross-system match | V1 value | V2 value | **UNRESOLVED — retain both, do not pick a winner** | DOB is ~99% null in both; no reliable per-field modification timestamp in V1; auto-resolving would risk merging two different real people. Surface both to a human reviewer (§16, §9). |
| Accused status (within V2 alone) | `is_arrested`/`is_absconding`/etc. | n/a | "latest fetched" | **Not fully trustworthy as "latest real state"** | §12 — the re-fetch trigger can miss a status change that doesn't move the arrest timestamp. Flag, don't silently trust. |
| Case status vocabulary | `fir_status` (V1, 30 values) vs `case_status` (V2, 3 values: PT/UI/Disposal) | raw value | raw value | **Store both raw; reuse DOPAMS-BE's existing normalization mapping** (`ACCUSED_STATUS_MAPPING.md`, the `case_status` ILIKE patterns already in `firs/services/index.ts`) rather than inventing a new one | The BE already has this logic in production; don't duplicate/diverge it |
| V1 version history | any field, dossier/details/court | multiple rows per logical entity (stale + current) | n/a | **Most recent `updated_at`/`created_at` row is current; older rows become `change_log` entries, not discarded** | This is exactly what V1's own design already produces — the unified layer should formalize it, not fight it |

**No global "V1 wins" or "V2 wins" rule is justified by evidence, and none is proposed.**

---

## 16. Target Unified DOPAMS Data Model

### Canonical entities and provenance

Every row in every unified table carries: `source_system` (`V1`|`V2`), `source_record_id`, `source_endpoint_or_table`, `source_created_at`, `source_modified_at` (nullable — V1 has none reliable), `fetched_at`, `etl_run_id`.

```
crimes_unified              — plain union, (source_system, source_record_id) composite key
accused_unified              — plain union, same key pattern, FK to crimes_unified
arrests_unified / seizures_unified / properties_unified / court_unified / media_unified
                              — plain union, same pattern

persons_unified               — plain union of source person rows (NEVER merged/overwritten)
identity_links                — (person_a, person_b, match_basis, confidence_score, status)
                                 status ∈ {candidate, confirmed, rejected}; human-reviewed only

change_log                    — (source_system, source_record_id, entity, field, old_value,
                                  new_value, observed_at, source_run_id); populated by comparing
                                  each incoming row against what the consolidation ETL last wrote

source_gap_ledger             — (source_system, module, gap_type, gap_key, first_seen_at,
                                  status); seeded from cctns_v1_failed_fetch_window and the
                                  relevant etl_bookkeeping rows in V2 (§14) so the unified DB
                                  never silently appears complete
```

### Why each exists
- **Plain-union core tables**: §10 proved there's nothing to reconcile at this level; a matching/conflict layer here would be solving a problem that doesn't exist.
- **`persons_unified` + `identity_links` as two separate things**: §9/§12 proved identity is low-confidence and high-stakes; collapsing rows is irreversible and wrong matches are worse than missed ones. Links are proposed, never auto-applied.
- **`change_log`**: §13 — neither source gives trustworthy point-in-time history; V1 fakes it with duplicate rows (87 and growing), V2 has none. Building this once, centrally, is cheaper and safer than asking either source ETL to change its semantics.
- **`source_gap_ledger`**: §14 — both sources have permanent, bounded, already-enumerated gaps (180 V1 windows, ~1,150 V2 FK-retry rows, 1,627 V2 address rows). The unified DB must expose "this record set is known-incomplete here," not hide it.

---

## 17. Initial Migration Strategy

1. **Snapshot, don't stream, for the first load.** Read `cctns_v1` and `cctns-v2` as of a fixed point; record that point in each source's own bookkeeping terms (`cctns_v1_etl_run_log.id` high-water mark; V2 `etl_bookkeeping(kind='run_state')` watermarks) so the first incremental run afterward has an exact resume boundary.
2. **Load order:** `hierarchy` (V2 reference) → `crimes_unified` (both sources, plain union) → `accused_unified`/`arrests_unified`/seizure/property/court-family tables (FK to `crimes_unified`) → `persons_unified` (plain union, unlinked) → `media_unified`.
3. **Deduplication:** none needed at the crime/accused/arrest/seizure/court level (§10). At the person level: none performed automatically — load all rows, run the candidate-matching batch (§9) afterward, populate `identity_links` as `candidate` only.
4. **History construction:** for V1, collapse the known stale-duplicate pattern into `change_log` entries during the first load (group by the pre-MD5-fix logical key — fir_reg_num + name + father + DOB-or-mobile — order by `created_at`, keep latest as current, rest as `change_log`). For V2, there is no history to construct; `change_log` starts empty and accumulates from here forward. **Exclude 2026-08-24 from this first-load comparison baseline** (§13) — it is a single administrative event, not a day of real business-state changes, and including it would seed `change_log` with ~8,288 + ~28,460 spurious entries.
5. **Known-gap seeding:** copy `cctns_v1_failed_fetch_window` and the relevant `etl_bookkeeping` rows into `source_gap_ledger` verbatim — don't re-derive them.
6. **Validation before cutover:** row counts per source table match what the consolidation ETL actually wrote (§19); spot-check a sample of V1 stale-duplicate groups collapsed correctly into `change_log`.
7. **No production V1/V2 database is modified by any of this** — per the standing rule, and because nothing above requires it; everything is read-only against the sources.

---

## 18. Incremental Synchronization Options

| | Option A: ETLs write unified DB directly | Option B: consolidation ETL reads both source DBs, writes unified DB | Option C: CDC (Debezium-style) | Option D: DB triggers |
|---|---|---|---|---|
| Correctness | Couples two independently-evolving codebases to a third schema; every V1/V2 fix risks breaking the unified write path | Decoupled — V1/V2 keep evolving independently, already proven this session (6+ fix cycles on each, zero coordination needed with a hypothetical third consumer) | Correct in principle, but neither source DB has logical replication configured or evidenced, and V1's natural-key-on-every-field-change pattern would flood a CDC stream with "updates" that are really just duplicate-row insertions | Triggers on tables with no FK integrity (V2) and a content-hash key that changes on every field edit (V1) would fire constantly and fragile; explicitly excluded by the task's own ground rules |
| Historical preservation | Hard — would need V1/V2 ETL authors to also understand unified-DB history semantics | Easy — one place builds `change_log`, independent of source ETL internals | Possible but CDC captures row-level changes, not the same thing as the `change_log` model in §16 | Same problem as CDC, worse coupling |
| Failure recovery | A unified-DB outage blocks V1/V2's own ingestion too | A unified-DB outage only pauses consolidation; V1/V2 keep ingesting into their own DBs unaffected (proven: both did exactly this independently throughout this session, including through multiple live failures) | Depends entirely on broker/connector reliability, unevidenced here | Trigger failures can block the source write itself — highest coupling risk |
| Idempotency | Must be built per-source-ETL | Built once, centrally, keyed on `(source_system, source_record_id)` | Depends on offset/exactly-once guarantees, unevidenced | Hard to make idempotent without the source table's own cooperation |
| Operational complexity | Lower short-term, higher long-term (two codebases, one shared concern) | One new, small, focused service | New infra (broker, connectors) neither source currently has | Lowest new infra, highest fragility given current schemas |
| Database coupling | Tight | Loose — read-only against both sources | Tight to DB internals (WAL/log mining) | Tightest |

---

## 19. ETL-3 vs CDC vs Direct Unified Writes — explicit answer

**Do we need a third ETL?** Not in the sense the rules warn against (a parallel *extraction* pipeline hitting the CCTNS APIs a third time) — that would triple the load on fragile source APIs (V1's Oracle backend is already the whole problem in §4/§12/§14) for no reason, since both sources already extract correctly.

What *is* needed, and what Option B actually is, is **a read-only consolidation service** — it extracts nothing from CCTNS; it only reads two already-populated Postgres databases and writes a third. It is not "ETL-3" in the problematic sense the rules are warning against (a competing extractor), it's the union/history/gap-tracking layer that neither V1 nor V2 was ever designed to be, and retrofitting that responsibility into either existing ETL would recouple two codebases that have spent this entire session proving they're healthier decoupled (every fix cycle this session — V1's dossier key, V1's fail-closed/known-gap logic, V2's window guard, V2's arrests uniqueness — shipped and was verified independently, with zero cross-impact).

**Recommendation: Option B.**

---

## 20. Recommended Architecture

```
cctns_v1 (read-only)          cctns-v2 (read-only)
        │                              │
        └──────────────┬───────────────┘
                        ▼
          Consolidation service (new, read-only on both sources)
             reads each source's own bookkeeping (run_log / etl_bookkeeping)
             to know what's new since its last successful pass
                        ▼
          dopams_unified (new database)
             ├── crimes_unified, accused_unified, arrests_unified, ... (plain union)
             ├── persons_unified (plain union, never merged)
             ├── identity_links (candidate/confirmed/rejected, human-reviewed)
             ├── change_log (SCD2-style, built once centrally)
             └── source_gap_ledger (180 V1 windows + ~1,150+1,627 V2 rows, seeded then live)
                        ▼
                DOPAMS application
```

Neither source database is ever written to by this service.

---

## 21. Failure & Replay Strategy

- The consolidation service's own cursor per source table is `max(fetched_at)` already written into `dopams_unified` for that `(source_system, table)` pair — it never needs to touch V1/V2's internal bookkeeping to resume, only to *discover new work* (read-only).
- Idempotent writes: `INSERT ... ON CONFLICT (source_system, source_record_id) DO UPDATE ... WHERE <payload> IS DISTINCT FROM EXCLUDED` — the same pattern both V1 (`db/upsert.py`) and V2 (every entity ETL) already use successfully; reuse it rather than inventing a new one.
- A failed consolidation run must not advance its own cursor past the failure point — same `begin/release` floor pattern V2's `etl_window_guard.py` already proved works this session (confirmed live, run-exercised, not just unit-tested).
- Replaying the same source window twice is safe by construction (idempotent upsert + `change_log` writes keyed on the actual old/new values, so a replay that produces no real diff writes nothing new to `change_log`).

---

## 22. Reconciliation Strategy

Per source, per table, on a schedule:

```
source_row_count          vs   unified_row_count (same source_system)
source_only (in source, missing from unified)
unified_only (exists in unified, no longer in source — flag, don't delete; neither source signals deletes, §14 risk register entry)
source_gap_ledger open count vs known baseline (180 for V1; ~1,150+1,627 for V2) — alert only if it GROWS beyond baseline
identity_links candidate count / confirmed count / rejected count
```

Metrics to expose: `records_received`, `records_inserted`, `records_updated`, `records_unchanged`, `records_unresolved` (orphan FK against `crimes_unified`/`persons_unified`), `source_gap_count` (vs. the fixed §14 baseline), `sync_lag` (now − latest successfully-consolidated `fetched_at` per source).

---

## 23. Monitoring & Alerting

- Alert if `source_gap_ledger` open count for V1 exceeds 180, or for V2 exceeds the ~1,150/1,627 baselines — that's a *new* problem, not the known ones.
- Alert if the consolidation service's own cursor stalls (no new `fetched_at` observed from a source for longer than that source's own expected cadence — V1: ~24h; V2: ~6h, per the confirmed cron).
- Reuse the `alerts.py` module that just landed on the V1 branch as a starting point/pattern rather than building parallel alerting — not yet reviewed in this dossier `[UNKNOWN — worth reading before building new alerting]`.

---

## 24. Cutover Plan

1. Stand up `dopams_unified`, run the consolidation service continuously for a validation window alongside the existing DOPAMS BE (which keeps reading its current source), comparing counts per §22.
2. DOPAMS BE adds read access to `dopams_unified` for net-new surfaces first (e.g., a cross-case person search spanning V1+V2) — lowest risk, no existing behavior to regress.
3. Only after that's stable, migrate existing BE queries (currently against materialized views `firs_mv`/`accuseds_mv`/`criminal_profiles_mv`/etc. — `[UNKNOWN]` which actual database backs the live BE; this needs to be confirmed before this step, not assumed) one module at a time.
4. V1 and V2's own databases and ETLs are untouched throughout — cutover only changes what the BE reads from.

---

## 25. Rollback Plan

Because the consolidation service never writes to V1/V2, rollback at every stage is: point the BE's queries back at whatever it was reading before. `dopams_unified` can be dropped and rebuilt from the sources at any time without any source-side risk, since it's a read-only derivative of two databases that remain the system of record.

---

## 26. Risks

| # | Risk | Evidence | Severity |
|---|---|---|---|
| R1 | Person identity cannot be automated | §9 — DOB ~99% null both sides, no shared ID | High, but mitigated by design (never auto-merge) |
| R2 | V2 accused/arrest status can be silently stale | §12 — 523 rows with the timestamp-coupling pattern confirmed, mechanism is structural (applies to future rows too) | Medium-high, invisible failure mode |
| R3 | V1's dossier is now unfrozen but still has a permanent 180-window gap | §14, confirmed `[DB]` this session | Low — bounded, documented, ledgered |
| R4 | V2's address/domicile enrichment is fully dark in production | §14 — confirmed the step doesn't run at all, not just backlogged | Medium — affects any unified field that expects domicile data |
| R5 | DOPAMS BE's actual database target is unconfirmed | `[UNKNOWN]`, carried from prior audits | Blocks §24 step 3 planning until resolved |
| R6 | No FK enforcement in V2 | §5 | Low today (0 orphans found except the 11 IR rows) but nothing prevents new ones |
| R7 | V1 stale-duplicate rows keep growing | §4, 87 and counting | Low per-row, needs the `change_log` collapse logic in §17 before persons work starts |

---

## 27. Open Decisions — need a person, not an ETL, to answer

- Confidence threshold for surfacing an `identity_links` candidate to an investigating officer.
- Whether V1's 27 pre-2002 FIRs and the 180 permanently-gapped windows need any manual backfill effort outside the ETL (e.g., direct source-DB query by CCTNS V1's own team), or are accepted as permanently out of reach.
- Whether V2's address/domicile enrichment should be turned back on in production, built fresh at the consolidation layer, or accepted as permanently out of scope.
- Who owns `dopams_unified` operationally.
- What DOPAMS-BE's actual `DATABASE_URL` target is — required before §24 can be sequenced for real.

---

## 28. Implementation Phases

| Phase | Scope | Status |
|---|---|---|
| 0 | V1 run-lock, V2 checkpoint/arrests fixes | **Done, verified live this session** |
| 1 | `crimes_unified` — plain union | Not started |
| 2 | Case-scoped children (`accused_unified`, `arrests_unified`, seizure/property/court family) | Not started |
| 3 | `persons_unified` (source rows only, no linking) | Not started |
| 4 | `identity_links` candidate generation + review queue | Not started |
| 5 | `media_unified` | Not started |
| 6 | BE cutover | Not started, blocked on R5 |

---

## 29. Detailed Implementation Checklist (Phase 1–2 start)

- [ ] Confirm DOPAMS BE's actual database target (R5) before any cutover planning proceeds
- [ ] Stand up `dopams_unified` schema: `crimes_unified`, provenance columns, `source_gap_ledger`
- [ ] Seed `source_gap_ledger` from `cctns_v1_failed_fetch_window` (180 rows) and V2 `etl_bookkeeping` fk_retry/failure rows (~2,777 rows across chargesheets/updated_chargesheet/fsl/address)
- [ ] Build the consolidation service's own per-source-table cursor (`max(fetched_at)` already written) — do not touch V1/V2 bookkeeping tables except to read
- [ ] Load `crimes_unified` as a plain union, verify count = 7,305 + 9,535 = 16,840 exactly, zero collisions on `(source_system, source_record_id)`
- [ ] Extend to case-scoped children, same pattern
- [ ] Build `change_log` population logic, test against a V1 FIR with known stale-duplicate rows (e.g., FIR …0405) to confirm it collapses correctly and excludes 2026-08-24 from the V2 baseline comparison
- [ ] Load `persons_unified` as a plain union (no linking yet)
- [ ] Build the §9/§10 candidate-matching batch as a separate, independently-schedulable job, writing only `candidate` rows to `identity_links`
