# ETL-3: CCTNS Unified Merger ETL — Implementation Plan

**Status: design record, written before implementation.** ETL-3 has since been built in `etl3/`. Where this plan disagrees with the code or the live `dopams_cctns` database, the code and the Phase 6 readiness report win. Two corrections matter:

- V1 `court_id` and V2 `charge_sheet_updates.id` are the same kind of small integer. They are not a safe global primary key. `chargesheets_unified.charge_sheet_id` is `{source_system}:{module}:{raw id}`.
- `fsl_case_property` is observed and is not merged. Historical `fsl_unified` rows were not deleted.

This document, plus its companions, remains the design investigation. It is not a claim that no code exists.

**Evidence tags:** `[CODE VERIFIED]` `[DATABASE VERIFIED]` `[DOCUMENT VERIFIED]` `[INFERRED]` `[UNKNOWN]`.

**As-of:** 2026-10-01, ~13:15 UTC. V1 `cctns-v1` branch HEAD `179fb2a`, V2 `cctns-v2` branch HEAD `a05f6c5` — re-checked at the start of this pass, unchanged from the immediately preceding revalidation `[CODE VERIFIED]`. Both databases re-queried fresh this pass for the specific gaps the revalidation document had left open (see §2).

**The hard invariant this entire design protects:** V1 and V2 remain independent source-of-truth ETLs, unmodified. ETL-3 reads both, read-only, and is the sole writer to a new, separate Unified DOPAMS database. If ETL-3 stops entirely, V1→V1 DB and V2→V2 DB continue completely unaffected; only the unified DB goes stale, and it catches up safely on restart.

---

## 1. What this plan builds on, and what it re-verified

Three prior documents form the baseline, already reconciled against each other and against live evidence in `MERGER_REVALIDATION.md`:

1. `CCTNS_V1_vs_V2_Column_Comparison_Report.pdf` — field dictionary, unaffected by anything operational.
2. `MERGER_DOSSIER.md` — post-audit architecture (union-not-dedup, Option B, no auto-merge).
3. `MERGER_REVALIDATION.md` — re-verified the above against fresh data, found and corrected two genuine contradictions in the earlier `schema.sql`/`DESIGN.md` draft (auto-merge-on-DOB is unworkable; `accused.person_id NOT NULL` rejects live V2 data), and left two items explicitly open: R10 (V2 provenance coverage beyond `accused`, unconfirmed) and R8/R5 (DOPAMS BE location, unknown).

**This pass does not blindly trust that document either.** Before writing anything, it re-checked:
- Both branch heads — unchanged `[CODE VERIFIED]`.
- **R10, resolved this pass**: queried `information_schema.columns` across all 14 live V2 business tables (not just `accused`). Every one carries `etl_run_id`, `fetched_at`, `source_system`, `source_endpoint`, `date_created`, `date_modified` `[DATABASE VERIFIED]` — full results in `ETL3_SOURCE_COMPATIBILITY_MATRIX.md`. The one table-shaped exception is `file_media_bookkeeping`, which has the provenance columns but no business `date_created`/`date_modified`.
- **A new finding on R8**: querying `pg_roles` on both database connections returned an identical, extensive list of existing application-facing roles (`ndps_admin`/`ndps_analyst`/`ndps_officer`/`ndps_readonly`, `dopamas_admin`/`dopamas_chat_ur`/`dopamasprd_ur`/`dopamasreadonly`/etc., `readonly_user`) `[DATABASE VERIFIED]`. This confirms a real DOPAMS/NDPS application ecosystem operates against this Postgres cluster today — it refines R8 (the app is real) without resolving it (its code/repository is still not found anywhere in this codebase). Also confirms V1 and V2 live on the **same Postgres cluster**, different `dbname`s, not separate servers — relevant to §13 below.
- **V1's incremental mechanism, examined at the column level for the first time**: no V1 business table (`cctns_fir`, `cctns_accused`, `cctns_accused_details`, `cctns_court`) carries a per-row run-id. The only way to know which run touched which row is `cctns_v1_etl_row_action(id, run_id, entity, table_name, record_key, action, action_at)` `[DATABASE VERIFIED]`, joined to the business table by `record_key`. This is structurally different from V2's per-row `etl_run_id` column, and is treated as such throughout this design (§6).

Nothing else was found to have changed. Current entity counts (V1: 7,305 FIR / 34,384 accused / 20,198 details / 7,531 court; V2: 9,535 crimes / 32,867 accused / 32,790 persons / 32,862 arrests / 7,061 chargesheets, plus the smaller V2-exclusive tables in the compatibility matrix) are carried forward from the revalidation pass, taken minutes earlier in the same session, not re-queried a third time.

---

## 2. Architecture

```
   SOURCE-OF-TRUTH ETLs (unchanged, unmodified, independent)

   CCTNS V1 ETL ──writes──▶ cctns_v1 DB
   CCTNS V2 ETL ──writes──▶ cctns-v2 DB

                     │                    │
                     │ READ ONLY          │ READ ONLY
                     ▼                    ▼
              ┌──────────────────────────────────┐
              │              ETL-3                │
              │   CCTNS Unified Merger ETL        │
              │                                   │
              │  source ingestion (§5/§6)          │
              │  → *_source (append-only)          │
              │  → current-state compute (§7)       │
              │  → identity linking (§9, never auto) │
              │  → change_log (§11)                │
              │  → reconciliation (§16)             │
              └──────────────────┬────────────────┘
                                 │ WRITE ONLY
                                 ▼
                      dopams_unified DB
                  (6 table families — ETL3_UNIFIED_SCHEMA.sql)
```

ETL-3 reads: V1/V2 business tables, plus each source's own bookkeeping (`cctns_v1_etl_run_log`, `cctns_v1_etl_row_action`, `cctns_v1_failed_fetch_window`; V2's `etl_bookkeeping`) — all read-only, purely to discover what's new and to seed the gap ledger.

ETL-3 writes: `dopams_unified` only. Never `cctns_v1`, never `cctns-v2`, never either source's checkpoint/bookkeeping tables.

If ETL-3 stops: V1 and V2 continue exactly as they do today (confirmed empirically this session — both have shipped multiple independent fixes with zero cross-impact on each other, and neither has ever depended on a third consumer existing). `dopams_unified` simply stops advancing until ETL-3 resumes, at which point §12 (idempotency) and §8 (cursor) guarantee a safe catch-up with no gaps and no duplicates.

---

## 3. Entity mapping (complete — all entities, not just crimes/persons/accused)

| Unified entity | V1 source | V2 source | Transformation | Relationship | Dedup rule |
|---|---|---|---|---|---|
| `crimes_unified` | `cctns_fir` | `crimes` | Direct field mapping per the PDF's 37-column dictionary | 0 overlap — plain union | None needed (no overlap) |
| `persons_unified` | `cctns_accused_details` (person_code + identity fields) | `persons` | Direct field mapping | Candidate overlap only, via `identity_links`, never merged | Never auto-merged — see §9 |
| `accused_unified` | `cctns_accused` (dossier) + `cctns_accused_details` | `accused` | Field mapping; V1's natural-key churn collapsed to current+history | 0 overlap at crime level | V1 stale-duplicate collapse (§10); V2 NULL-`person_id` rows kept, flagged unlinked, never dropped |
| `arrests_unified` | fields embedded in `cctns_accused_details` | `arrests` | Synthesize V1 arrest sub-entity from dossier fields | 0 overlap | None beyond source-level (0 duplicates confirmed both sides) |
| `chargesheets_unified` | `cctns_court` | `chargesheets` + `charge_sheet_updates` | V1 flat row; V2 split. Both land in one table. The unified primary key is namespaced because V1 `court_id` and V2 update `id` collide as raw numbers. Case identity does not overlap. | 0 case overlap; raw ids collide | Namespace the primary key |
| `seizures_unified` | drug fields embedded in `cctns_accused` dossier row | `mo_seizures` | Synthesize one seizure per V1 dossier row; V2 already one-to-many via its own table | 0 overlap | None |
| `properties_unified` | — (no V1 equivalent) | `properties` | Direct | V2-exclusive | None |
| `fsl_unified` | — (no V1 equivalent) | `fsl_case_property` | Observed only. Not merged. Historical unified rows, if present, are not deleted. | V2-exclusive, intentionally excluded | None |
| `disposal_unified` | partially — `court_disposal_type`/`court_disposal_dt` fields fold into `chargesheets_unified`, not a separate V1 entity | `disposal` | Direct for V2; V1 has no standalone disposal entity | V2-exclusive as a separate table; V1's disposal concept lives inside its chargesheet row | None |
| `interrogation_unified` | V1's 60 `INT_*` relative fields fold into `accused_source.payload` JSONB, not a separate entity | `interrogation_reports` | Direct for V2 | V2-exclusive as a standalone entity; V1's equivalent data is present but structurally embedded, not dropped | None |
| `hierarchy_unified` | — (no V1 equivalent — V1 has no organizational hierarchy codes at all, confirmed in the original audit) | `hierarchy` | Direct | V2-exclusive | None |

**Nothing V2-exclusive is discarded.** GPS coordinates, 41A/CCL/absconding/apprehended flags, IO details, telecom/financial intelligence fields, and the drug supply-chain graph all have a home in the `*_unified`/`*_source` tables' field lists or JSONB payload — per the explicit instruction not to drop V2-only information merely because V1 has no equivalent.

---

## 4. Unified database design

Full DDL in `ETL3_UNIFIED_SCHEMA.sql`. Summary of the six-family split and why each exists:

| Family | Mutability | Purpose |
|---|---|---|
| **SOURCE / OBSERVATION** (`crimes_source`, `accused_source`, etc.) | Append-only, never updated/deleted | The raw evidence trail — every row either source ever produced, with full provenance. This is what makes replay and audit possible without re-reading the source again |
| **CURRENT UNIFIED STATE** (`crimes_unified`, `accused_unified`, etc.) | Computed, replaced not blindly upserted | One row per real unified entity, reflecting the freshest evidence per the current-state algorithm (§7) |
| **IDENTITY** (`identity_links`) | Insert candidates, update only via human review | Cross-source person matching, confidence-scored, never auto-confirmed |
| **HISTORY** (`change_log`, `bulk_event_exclusions`) | Append-only | Deterministic diff-based change tracking, with an explicit, extensible mechanism for excluding known administrative bulk events (seeded with 2026-08-24, not hardcoded as a one-off) |
| **CONTROL PLANE** (`consolidation_cursor`, `consolidation_run_log`) | Mutable, ETL-3-owned only | Tracks what ETL-3 itself has processed — entirely separate from either source's own checkpoints |
| **RECONCILIATION / GAP TRACKING** (`source_gap_ledger`, `reconciliation_run_log`) | Append-only / periodic snapshot | Makes known incompleteness visible rather than silently absent |

Every table carries `source_system`/`source_record_id` provenance; nothing in this schema discards where a fact came from.

---

## 5. V1 ingestion design

Per `ETL3_SOURCE_COMPATIBILITY_MATRIX.md`, V1 offers no per-row run-id column. The mechanism:

1. Read `cctns_v1_etl_run_log` for runs with `id > consolidation_cursor.last_processed_source_run_id` **and** `status` in a success state (`loaded`, `loaded_with_known_gaps` — explicitly **not** `extract_failed`/`extract_partial_failed`, since those runs by definition did not durably commit new data).
2. For each such run, read `cctns_v1_etl_row_action WHERE run_id = <that run's id>` to get the exact `(table_name, record_key, action)` set it touched.
3. Join `record_key` back to the relevant business table (`cctns_fir.fir_reg_num`, `cctns_accused.accused_id`, etc.) to fetch the current row content as of this read.
4. Write one `*_source` row per touched record, carrying `source_run_id = cctns_v1_etl_run_log.id`, `source_fetched_at = cctns_v1_etl_run_log.started_at` for that run.
5. Also read `cctns_v1_failed_fetch_window` (full table, not incremental — it's small, 180 rows) each pass, to keep `source_gap_ledger` current.

V1's natural-key churn (§10) is **not** resolved at ingestion time — every observation, including every churned duplicate, is written to `*_source` faithfully. Collapsing happens only at current-state computation (§7), where it belongs, keeping the source-observation layer a true, unedited record of what V1 actually produced.

---

## 6. V2 ingestion design

Symmetric, but simpler, because every V2 business table already carries `etl_run_id` directly `[DATABASE VERIFIED]`:

1. For each of the 14 business tables (list in the compatibility matrix), query `WHERE etl_run_id NOT IN (already-processed set)` — tracked via `consolidation_cursor.last_processed_source_run_id` per `(V2, module)`.
2. Since V2 has no single global "run" the way V1's Airflow DAG does (it's a sequence of 22 ordered steps within one `master_etl.py` cycle, each potentially its own `etl_run_id`), the cursor is tracked **per module**, not globally — consistent with `consolidation_cursor`'s `(source_system, source_module)` primary key.
3. Write one `*_source` row per fetched record, carrying the row's own `etl_run_id`/`fetched_at`/`source_system`/`source_endpoint` directly — no synthesis needed, unlike V1.
4. `date_modified` is read and stored but is **not** used as the change-detection signal on its own for `accused` (§7's quiet-staleness handling) — `etl_run_id`/`fetched_at` (i.e., "was this row re-observed at all") is the primary signal; `date_modified` is one of the fields diffed for `change_log`, not the trigger for re-reading.

---

## 7. Current-state computation model

For each unified entity, per `(source_system, source_record_id)`:

1. Gather all `*_source` rows for that identity, newest `source_modified_at`/`source_created_at` first.
2. The newest wins and becomes (or updates) the `*_unified` row — but only if it is actually newer than whatever `current_as_of` the existing unified row already has (**stale-write prevention** — an older observation arriving after a newer one, task scenario, is recorded in `*_source` and diffed into `change_log` as a real fact, but does not regress the current-state row).
3. No "last write wins" by wall-clock arrival time — only by the source's own asserted timestamp. This directly avoids the undocumented "last write wins via `ON CONFLICT DO UPDATE`" pattern in the original `schema.sql`, which the task explicitly says not to repeat.
4. For `accused_unified` specifically: `person_id` is resolved from the paired `persons_source`/`persons_unified` row by `(source_system, source_record_id)` match only — **never** by cross-source identity (that's `identity_links`' job, and it never auto-resolves). If V2's source row has `person_id IS NULL` (78 confirmed live rows), the unified row is written with `person_id = NULL` and `unlinked_person_flag = TRUE` — not rejected, not synthesized.

---

## 8. Cursor design (the central design decision)

**Explicitly not** `WHERE fetched_at > last_cursor` — this was the task's own explicit warning, and the reasoning holds: V2's `accused` table's `date_modified`/`fetched_at` can both fail to move on a real status change (the arrest-timestamp-coupling risk, §2), so a naive timestamp poll inherits that blind spot one layer up, silently.

**The actual cursor, per source:**

```
V1:  consolidation_cursor(source_system='V1', source_module=<entity>)
       .last_processed_source_run_id = highest cctns_v1_etl_run_log.id,
       status IN ('loaded','loaded_with_known_gaps'),
       fully processed by ETL-3 (Family 1 writes + Family 2 recompute +
       Family 4 change_log all committed)

V2:  consolidation_cursor(source_system='V2', source_module=<table>)
       .last_processed_source_run_id = highest etl_run_id seen for that
       table, fully processed (same commit guarantee)
```

Both are **independent of, and never write to,** the source's own checkpoint mechanism (V1's `cctns_v1_etl_run_log`/Airflow XCom state; V2's `etl_bookkeeping(kind='run_state')` watermarks). ETL-3's cursor only ever moves forward based on what ETL-3 itself has durably written to `dopams_unified` — this is what makes it safe for ETL-3 to be down for an arbitrary period and catch up correctly afterward (§12).

**Why this cannot miss a change:** both sources' own run/row logs are the same mechanism the source ETL itself uses to know what it touched — V1's `cctns_v1_etl_row_action` is written by the V1 ETL's own upsert path `[CODE VERIFIED, prior session's read of pipeline_run.py]`, and V2's `etl_run_id` is stamped by its own ETL on every row it touches. ETL-3 is reading the same ground truth each source ETL already trusts about itself, not re-deriving a weaker signal from business timestamps.

---

## 9. Person identity model

Unchanged in substance from `MERGER_REVALIDATION.md` §8, restated here as an ETL-3 responsibility:

- `persons_unified`: plain union, one row per `(source_system, source_record_id)`, **never merged**.
- `identity_links`: candidate generation using the fields actually populated at usable rates — name (near-100% both sides), father/relative name (92.0% V1 / 78.6% V2), mobile/phone (79.4% V1 / 82.5% V2). **DOB is read and stored for display but never used as a match input** — 0.79%/0.24% population makes it statistically useless as a discriminator and would, if weighted, simply reward the rare rows that happen to have it.
- States: `candidate` → (human review, out of ETL-3's scope) → `confirmed` or `rejected`. ETL-3's own candidate-generation code path has **no** mechanism to write `confirmed` — that transition requires a `reviewed_by` value, which only a human-facing review tool (not ETL-3 itself) would ever populate.
- Candidate generation is a **separate, independently schedulable job**, not part of the main per-run consolidation pass — it can run on its own cadence (e.g., nightly) since person-matching is comparison-heavy and not time-critical the way crime/accused ingestion is.
- **Confirmed this pass, concretely**: candidate generation must run **after** V1's stale-duplicate collapse (§10 below) and against `persons_unified`, not against raw source rows. A fresh row-level re-run this session (phone+name-token tier: 2,303 raw pairs / 1,088 V1 rows; name+father tier: 653 pairs / 524 V1 rows — full detail in `MERGER_REVALIDATION.md` §7) found roughly 3× more V1-side matches than an earlier same-session estimate, and the gap traces directly to V1's duplicate rows each counting separately at the raw-row level. Running candidate generation before the collapse would hand a human reviewer a queue inflated by the same artifact this document already designs around elsewhere — an avoidable mistake worth stating explicitly here, not just in the revalidation doc.

---

## 10. V1 stale-duplicate strategy

Confirmed this session: 3 of 20,128 `person_code`s in `cctns_accused_details` have more than one `accused_id` (one has 69), and V1's broader natural-key design means any field edit on a dossier row produces an entirely new row+ID rather than an in-place update.

**Algorithm:**
1. Group `accused_source` rows (for `source_system='V1'`) by the pre-MD5-fix logical key: `fir_reg_num` + normalized name + normalized father/relative name + (mobile or DOB, whichever populated).
2. Within each group, the row with the latest `source_created_at`/`source_modified_at` becomes the `accused_unified` current row.
3. Every other row in the group becomes a `change_log` entry, `entity='accused'`, diffed field-by-field against the row immediately before it in `source_created_at` order (not against the final current row — this preserves the actual sequence of edits, not a collapsed before/after).
4. The original `accused_id` for every row, current or historical, is retained in `*_source.payload` and in `change_log.source_run_id`/the row's own `*_source` entry — fully traceable back to the exact V1 row that produced it.
5. **Reproducibility**: because this groups on a deterministic key and orders on a deterministic timestamp, re-running this algorithm against the same `*_source` data always produces the same result — it is pure, not order-of-arrival-dependent.

---

## 11. History / change-log design

```
*_source (raw observations)
      │
      ▼
current-state computation (§7)
      │
      ▼
diff against PRIOR current-state value, per field
      │
      ├─ falls inside a bulk_event_exclusions window?
      │      → change_classification = 'administrative_bulk_excluded'
      │
      ├─ no actual value difference (pure re-observation)?
      │      → change_classification = 'etl_reobservation_no_diff'
      │        (still logged — this is itself useful signal for R2's
      │         staleness detection, see §17)
      │
      └─ real difference, not excluded
             → change_classification = 'business_change'
```

`bulk_event_exclusions` is seeded with the confirmed 2026-08-24 V2 event (8,288/9,535 crimes and 28,460/32,860 accused touched in one day, 92% of that in a single UTC hour — established in the prior audits, not re-derived this pass) but is explicitly a registrable table, not a hardcoded date check in code — any future bulk/administrative event (either source) can be added without a code change.

---

## 12. Idempotency and failure recovery

Walking the task's explicit scenarios:

| Scenario | What happens |
|---|---|
| ETL-3 run succeeds halfway, then crashes | Everything for the in-flight source run is inside one database transaction per `(source_system, source_module, source_run_id)`; an incomplete transaction rolls back entirely (standard Postgres semantics); `consolidation_cursor` is only advanced after full commit, so a restart simply reprocesses the same run from scratch |
| Same V1 source run processed twice | `*_source` tables are `UNIQUE(source_system, source_record_id, source_run_id)` — the second attempt's inserts are no-ops (`ON CONFLICT DO NOTHING`); current-state recompute against identical input produces identical output; no new `change_log` rows (nothing differs) |
| Same V2 source run processed twice | Same mechanism, mirrored |
| Same record observed twice within different runs | Not a duplicate — two genuinely distinct `*_source` rows (different `source_run_id`), correctly representing two separate observations; current-state computation naturally picks the newer one |
| Older record arrives after newer | Stale-write prevention (§7, step 2) — recorded, diffed into `change_log`, does not regress `*_unified` |
| V1 ETL succeeds, ETL-3 is down | V1's own run log/row-action tables accumulate normally, untouched by ETL-3's absence; `consolidation_cursor` simply doesn't advance; on restart, ETL-3 discovers and processes every run it missed, oldest-first, each still idempotent individually |
| V2 ETL succeeds, ETL-3 is down | Same, mirrored |
| A known source gap later becomes available | The source's own ledger (V1: `cctns_v1_failed_fetch_window` row flips `OPEN`→`RESOLVED`; V2: a retried FK row eventually resolves) is picked up as an entirely normal new source run on ETL-3's next pass — no special-case code needed |
| Duplicate execution (e.g., two ETL-3 instances running concurrently by operator error) | Not explicitly lock-protected in this design pass — **flagged as an open item for implementation** (§18/open decisions): recommend a simple advisory lock (`pg_advisory_lock`) on `dopams_unified` for the duration of a consolidation run, analogous to V1's own `fcntl.flock` run-lock pattern, which has already proven itself this session |

---

## 13. Security boundary

Per the task's explicit instruction, enforced at the database grant level, not application code alone. Confirmed this session that V1 and V2 are on the **same Postgres cluster** (identical `pg_roles` listing from both connections), so role separation must be explicit per-database, not assumed from network isolation.

**Design (not executed — no roles have been created):**

```
etl3_v1_reader   — GRANT CONNECT ON DATABASE cctns_v1; GRANT SELECT ON ALL TABLES
                   IN SCHEMA cctns TO etl3_v1_reader; explicitly NO INSERT/UPDATE/
                   DELETE/TRUNCATE/ALTER grants anywhere
etl3_v2_reader   — same pattern against cctns-v2, schema public
etl3_writer      — full DML on dopams_unified only; no grants whatsoever on
                   cctns_v1 or cctns-v2
```

This mirrors the existing read-only role pattern already present on this cluster (`readonly_user`, `dopamasreadonly`, `ndps_readonly` — confirmed to exist this session, though their exact grant scope was not inspected and should not be assumed identical to what ETL-3 needs). **Recommendation, not yet executed:** verify via `information_schema.role_table_grants` that `etl3_v1_reader`/`etl3_v2_reader` have zero write grants, as part of Phase 2 of the implementation phases — this is a cheap, mechanical check that should gate moving past that phase.

---

## 14. Performance and scale

Current volumes, all `[DATABASE VERIFIED]` this session or the immediately preceding one: largest V1 table 34,384 rows (`cctns_accused`), largest V2 table 165,864 rows (`file_media_bookkeeping`), next largest 32,867 (`accused`). Total across both sources, all tables: well under 500,000 rows.

At this scale, no exotic infrastructure is warranted. Design constraints:
- **Batched reads**: `*_source` ingestion reads in bounded batches (e.g., by `source_run_id`, naturally bounded since a single V1 Airflow run or V2 module cycle touches a few thousand rows at most, per the counts above) — never a full unbounded `SELECT *` into application memory.
- **Transaction size**: one transaction per `(source_system, source_module, source_run_id)` — bounded by construction, since that's already the unit the source ETL itself batches by.
- **Parallelism**: V1 and V2 ingestion (§5/§6) are fully independent and can run concurrently; within a source, different modules/entities can also run concurrently (e.g., V2's `crimes` and `hierarchy` ingestion don't depend on each other).
- **Initial migration** (§15) is the one case that touches full table scans — still well within single-digit-GB territory at current volumes; no special partitioning or streaming infrastructure is justified yet. Re-evaluate if any source table grows past roughly 10x its current size.

---

## 15. Initial migration

Dependency order, derived from the actual FK structure confirmed this session (not assumed):

1. Schema creation (`ETL3_UNIFIED_SCHEMA.sql`, empty).
2. `hierarchy_source`/`hierarchy_unified` — V2 reference data, no dependency on anything else.
3. `crimes_source`/`crimes_unified` — both sources, dependency root for every entity below.
4. `persons_source`/`persons_unified` — plain union, no linking yet.
5. `accused_source`/`accused_unified`, `arrests_source`/`arrests_unified` — depends on 3+4; V1 stale-duplicate collapse (§10) happens here.
6. `seizures_unified`, `chargesheets_unified`, `properties_unified`, `fsl_unified`, `disposal_unified`, `interrogation_unified` — each depends only on `crimes_unified` (and, for `interrogation_unified`, optionally `persons_unified`).
7. `identity_links` candidate generation — depends on 4 only, independently schedulable, never blocks 5/6.
8. `source_gap_ledger` seeding — depends on schema creation only, no dependency on 2–7.
9. Validation (§16).
10. Establish `consolidation_cursor` starting values (the highest source run/record already migrated, per module).
11. Switch to incremental mode (§8).

All of this reads V1/V2 read-only; nothing writes back to either source.

---

## 16. Validation plan

```
per entity, per source:
  source_count (from the source's own table, read-only)
    vs  *_source row count (for that source_system)
    vs  *_unified row count
    vs  missing (in source, not yet in *_source)
    vs  duplicate (*_source rows that should have collapsed but didn't)
    vs  orphan FK rows (*_unified rows whose crime_id/person_id FK target is missing)
    vs  source provenance completeness (every *_source row has a non-null source_run_id)
    vs  consolidation_cursor consistency (matches the highest source_run_id actually present in *_source)
    vs  change_log consistency (every *_unified update has a corresponding change_log entry, or is explained by 'etl_reobservation_no_diff')
    vs  identity_links consistency (every row has status in candidate/confirmed/rejected, zero auto-confirmed)
```

Sample-level reconciliation, concretely, per major entity: re-identify the specific known data points already established this session (the 69-row V1 churn outlier; the 3-row V2 NULL-`person_id` cluster on crime `66544fae884cf131822968f5`; the exact 7,305/9,535/0-overlap crime counts) and confirm the migrated `dopams_unified` data reproduces them exactly, not approximately.

---

## 17. Monitoring

Extends `MERGER_REVALIDATION.md` §24 with ETL-3-specific signals:

- `consolidation_cursor.last_processed_at` staleness per `(source_system, source_module)`, thresholds matched to each source's real cadence (V1 ~daily; V2 four times daily, confirmed `[CODE VERIFIED]`).
- `source_gap_ledger` growth beyond the known baselines (180 V1 / ~1,150 V2 FK-retry / 1,627 V2 address / 78 V2 unlinked-accused).
- `change_log` rows with `change_classification='etl_reobservation_no_diff'` for a given `(entity, unified_id)` with no `business_change` row for an unusually long span, **while the entity's parent (crime) keeps advancing** — this is the concrete, buildable detector for R2's quiet-staleness risk that neither source can detect about itself today.
- `identity_links` candidate count drift — a sudden spike could indicate a data-quality regression in name/phone fields at either source.

---

## 18. Rollback

Unchanged in principle from the revalidation: because ETL-3 never writes to V1/V2, `dopams_unified` can be dropped and fully rebuilt from the two sources at any time — every row in it is a read-only derivative, and `*_source` is itself reconstructible from each source's own run/row logs for as long as those logs retain history.

**Retention, checked this pass**: V1's `cctns_v1_etl_row_action`/`cctns_v1_etl_run_log` both start 2026-09-28 (`[DATABASE VERIFIED]` — oldest `action_at`/`started_at` in both tables); V2's `etl_bookkeeping` starts 2026-09-25 (`failure` rows) / 2026-09-28 (`run_state` rows). Neither shows a gap or any sign of rotation mid-range — the earliest timestamps line up with when this session's audits introduced these specific bookkeeping mechanisms (V1's row-action/run-lock logging and V2's run_state watermarks were both part of the fix cycle this session tracked), not with a rolling-window prune of older entries. **Conclusion: there is no evidence of log pruning; the short history is because the mechanism is new, not because older entries were deleted.** This means a `dopams_unified` rebuild can safely replay everything these tables have ever recorded, but cannot reach further back than late September 2026 via this specific mechanism — which is immaterial for V2 (whose business data itself only starts mid-2022 and is already fully re-readable directly from the business tables regardless of bookkeeping age) and immaterial for V1 (whose business data goes back to 1991, but a *first* migration reads the business tables directly, not through the row-action log — the row-action log only matters for *incremental* detection going forward from whenever ETL-3 first runs).

---

## 19. Phased implementation

See `ETL3_IMPLEMENTATION_PHASES.md` for the full, independently-verifiable 17-phase breakdown (Phase 0 through Phase 16/cutover). Summary of the ordering logic: security boundary (Phase 2) before any ingestion code runs; V1 and V2 ingestion (Phases 3/4) in parallel since they share no code path; simplest entity (`crimes`, Phase 5) proven before the hard one (`accused`, Phase 7, which carries the V1 collapse algorithm and V2 NULL-handling); gap-ledger seeding (Phase 11) and identity-linking (Phase 10) both independently schedulable, not gating the main entity chain; cutover (Phase 16) explicitly blocked on the separate, non-ETL-3 investigation task of locating DOPAMS BE (Phase 0.5).

---

## 20. Open items surfaced by this design pass

**Resolved since first written:**

- **V2's per-module `etl_run_id` semantics** — checked directly. `etl_run_id` is shared across modules within one `master_etl.py` cycle, not assigned per-module — e.g., run `ade2abac-a816-4eb6-ba6e-1f5d81314efc` appears on both `crimes` (40 rows, 12:13:34 UTC) and `accused` (74 rows, 12:13:42 UTC) from the same 2026-10-01 cycle `[DATABASE VERIFIED]`. `consolidation_cursor`'s per-`(source_system, source_module)` grain is still correct to keep, but §6's cursor tracks "highest `etl_run_id` **fully processed for that module**," not a module-unique run sequence. One stray `persons` row was observed with an empty-string `etl_run_id` and null `fetched_at` — small, not investigated further, worth a data-quality note in Phase 3.
- **Source log retention** (§18) — checked directly. No evidence of pruning; V1's `cctns_v1_etl_row_action`/`cctns_v1_etl_run_log` and V2's `etl_bookkeeping` both start in late September 2026 because that's when these specific bookkeeping mechanisms were introduced this session, not because older rows were deleted. Full reasoning in §18.
- **Candidate person-match sizing** — re-run this pass at the raw accused-row level; found materially higher counts than an earlier same-session estimate, traced to V1's duplicate-row inflation rather than a data change. Confirms (doesn't just assume) that candidate generation must run post-collapse, post-`persons_unified` — detail in §9 and `MERGER_REVALIDATION.md` §7.

**Still open:**

- **R8 (escalated, not resolved)**: DOPAMS BE's repository/deployment remains unlocated; the existing `ndps_*`/`dopamas_*` role set confirms it's real, not where it lives.
- **Concurrent-execution locking for ETL-3 itself** (§12) — no mechanism chosen yet; recommend reusing V1's own proven `fcntl`-style run-lock pattern, adapted to Postgres advisory locks since ETL-3 will likely run as a scheduled job rather than a long-lived daemon.
