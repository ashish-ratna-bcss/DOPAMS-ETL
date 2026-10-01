# CCTNS V1 + V2 → Unified DOPAMS — Revalidated Merger Architecture

**Purpose of this document:** the original merger analysis (`CCTNS_V1_vs_V2_Column_Comparison_Report.pdf` and `MERGER_DOSSIER.md`, plus the earlier `schema.sql`/`DESIGN.md` draft they both build on) was produced across several passes, some before the V1/V2 ETL audits reached their final state. This document re-walks every major assumption against the **current** code and **current** live data and states, explicitly, what still holds, what changed, and what the final architecture should be. It does not silently correct the earlier documents — they remain on disk, unedited, as the historical baseline.

**Evidence tags:** `[CODE VERIFIED]` `[DATABASE VERIFIED]` `[LIVE API VERIFIED]` `[DOCUMENT VERIFIED]` `[INFERRED]` `[UNKNOWN]`.

**As-of:** 2026-10-01, ~13:03 UTC. V1 `cctns-v1` branch HEAD `179fb2a`, V2 `cctns-v2` branch HEAD `a05f6c5` — both already merged into `dopams-cctns` (commits `053d972`/`81be39c`/`f47d8e5`); no newer commits exist on either source branch as of this check `[CODE VERIFIED]`. All database numbers below were re-queried fresh for this document, not copied from `MERGER_DOSSIER.md`.

**Safety:** no production data, V1/V2 code, or schema was modified to produce this. Every query below is read-only (`q.py`/`batch.py`, enforced SELECT-only + `default_transaction_read_only=on`). No credentials appear below.

---

## 1. ORIGINAL MERGER PLAN — SUMMARY

Three documents make up the original baseline, in chronological order:

**a) `schema.sql` + `DESIGN.md`** `[DOCUMENT VERIFIED]` — the earliest artifact. Proposes a single normalized Postgres schema (`crimes`, `persons`, `identity_details`, `physical_features`, `physical_deformities`, `family_associates`, `accused`, `mo_seizures`, `chargesheets`), with:
- `VARCHAR(50)` IDs + `source_system CHECK IN ('V1','V2')` + `source_record_id`, `UNIQUE(source_system, source_record_id)` on `crimes` and `persons`.
- **`accused.person_id` declared `NOT NULL`**, `UNIQUE(crime_id, person_id)` — one current row per (crime, person).
- **No history table of any kind.** `accused`, `chargesheets`, etc. are pure current-state tables.
- **Automatic person deduplication at load time**: DESIGN.md §3.2 explicitly instructs the loader to "resolve to **one** `persons` row (match on name + DOB + father's name + mobile) ... before inserting."
- Explicitly scopes OUT: `hierarchy`, `file_media_bookkeeping`, `etl_bookkeeping`, `geo_reference`, `geo_countries`, `disposal`, `fsl_case_property`, most of `interrogation_reports`.

**b) `CCTNS_V1_vs_V2_Column_Comparison_Report.pdf`** `[DOCUMENT VERIFIED]` — pure field-level cross-reference: 37 direct 1:1 column mappings, 4 groups of V1→V2 "data exists but relocated" mappings (identity proofs → `IDENTITY_DETAILS`, 14 deformity fields → `PHYSICAL_FEATURES`, 60 `INT_*` relative fields → `FAMILY_HISTORY`/`ASSOCIATE_DETAILS`, 14 seizure/admin fields → `mo_seizures`/`case_property`), and a list of V2-exclusive modules (hierarchy, IO, GD entries, 41A/CCL/absconding flags, GPS, FSL/CPR, ICJS, telecom/financial intelligence, drug supply-chain graph). It makes **no claims about overlap, identity resolution, history, or synchronization** — it is a column dictionary, not an architecture.

**c) `MERGER_DOSSIER.md`** `[DOCUMENT VERIFIED]` — written after the V1/V2 ETL audits (same session, immediately prior to this document). Already a significant departure from (a): proposes plain-union core tables (no auto-merge), a separate `identity_links` table for human-reviewed candidate person matches, a central `change_log`, a `source_gap_ledger`, and recommends Option B (read-only consolidation service) over ETL-3/CDC/triggers.

Items 1–13 required by the task, extracted:

| # | Item | Original answer | Source |
|---|---|---|---|
| 1 | Entity mapping | crimes↔crimes, accused↔accused+persons (split), court↔chargesheets+charge_sheet_updates+disposal | PDF, dossier §8 |
| 2 | Column mapping | 37 direct + 4 relocated groups (97 V1 columns total accounted for) | PDF |
| 3 | Schema assumptions | Fully normalized, `VARCHAR(50)` PK, FK-enforced | schema.sql |
| 4 | Overlap assumptions | Zero case overlap; some person overlap | dossier §10 |
| 5 | Identity assumptions | schema.sql: auto-merge on name+DOB+father+mobile. dossier: never auto-merge, candidate-link only | **Direct conflict between (a) and (c) — see §4 below** |
| 6 | Precedence assumptions | None in schema.sql (silent, implicit "last write wins" via `ON CONFLICT DO UPDATE`). Dossier: field-level, mostly "no rule needed" since no overlap, "preserve both" where unresolved | dossier §15 |
| 7 | Unified schema proposal | schema.sql's 9-table normalized design | schema.sql |
| 8 | Consolidation architecture | Not addressed in (a)/(b). Dossier recommends Option B | dossier §20 |
| 9 | Incremental sync proposal | Not addressed in (a)/(b)/(c) in implementation detail — dossier §21 is conceptual, not a concrete mechanism | dossier §21 (needs deepening — this document does that in §15/§16) |
| 10 | History/change-log proposal | schema.sql: none. Dossier: `change_log` table, SCD2-style, conceptual only | dossier §16 |
| 11 | Migration proposal | schema.sql DESIGN.md §3.2: ETL from raw JSON files. Dossier §17: snapshot-based, ordered load | Both |
| 12 | Risk register | Dossier §26 only | dossier |
| 13 | Unresolved questions | Dossier §27 (confidence threshold, pre-2002/180-gap backfill, address re-enable, unified DB ownership, DOPAMS BE's actual DB target) | dossier |

---

## 2. CURRENT V1 VERIFIED STATE

Re-queried fresh this session, not copied forward:

| Metric | Value | Evidence |
|---|---|---|
| `cctns_fir` | 7,305 | `[DATABASE VERIFIED]` |
| `cctns_accused` (dossier) | 34,384 | `[DATABASE VERIFIED]` |
| `cctns_accused_details` | 20,198 | `[DATABASE VERIFIED]` |
| `cctns_court` | 7,531 | `[DATABASE VERIFIED]` |
| Run 54 | `extract_partial_failed`, 34,337 fetched, 0 ins/upd, 180 failed windows | `[DATABASE VERIFIED]` — pre-fix, before the known-gap classification |
| Run 55 | `extract_failed`, "abandoned: process lost or superseded (run lock acquired)" | `[DATABASE VERIFIED]` — deploy-induced abandon, not a concurrency bug (operator-confirmed, consistent with `db/run_lock.py` `[CODE VERIFIED]`) |
| Run 56 | **`loaded_with_known_gaps`**, 34,337 fetched, 0 ins/upd (expected — no new source changes in that window), 180 known gaps classified and excluded from blocking | `[DATABASE VERIFIED]` — first live proof the known-gap fix works end-to-end |
| Runs ≥57 | **none exist yet** | `[DATABASE VERIFIED]` — "future runs can continue outside the 180-day gaps" is `[INFERRED]` from code (`is_known_ora_gap`/`partition_failed_windows` `[CODE VERIFIED]`) + the run-56 proof, **not yet observed on a run carrying genuinely new data** |
| Stuck/running rows | 0 | `[DATABASE VERIFIED]` |
| Ledger (`cctns_v1_failed_fetch_window`) | 180 rows, **all status=OPEN**, 0 RESOLVED | `[DATABASE VERIFIED]` |
| DOB populated | 272 / 34,384 (0.79%) | `[DATABASE VERIFIED]` — confirms DOB is practically unusable as a match key |
| Mobile populated | 27,321 / 34,384 (79.4%) | `[DATABASE VERIFIED]` |
| Father/relative name populated | 31,621 / 34,384 (92.0%) | `[DATABASE VERIFIED]` |
| `person_code` identity churn (in `cctns_accused_details`, 20,198 rows) | 20,125 person_codes map to exactly 1 `accused_id` (stable); 2 map to 2; **1 maps to 69** | `[DATABASE VERIFIED]` — confirms the MD5-natural-key stale-duplicate pattern is real, bounded, and heavily concentrated in a single outlier, not widespread |

**Conclusion carried forward unchanged:** V1 is operationally healthy, the dossier freeze is resolved, and the 180-window gap is a permanent, bounded, source-side limitation now correctly represented as a durable ledger rather than a pipeline blocker. The one correction to the user's stated baseline: "future runs can continue processing outside the known 180-day gaps" is a reasonable inference from code and from run 56, but has not yet been observed in a run that actually ingested new, non-gap data — classify it `PARTIALLY VERIFIED`, not fully verified.

---

## 3. CURRENT V2 VERIFIED STATE

| Metric | Value | Evidence |
|---|---|---|
| `crimes` / `accused` / `persons` / `arrests` / `chargesheets` | 9,535 / 32,867 / 32,790 / 32,862 / 7,061 | `[DATABASE VERIFIED]` |
| Pure-CCTNS pipeline, configured order count | **exactly 22** (`[Order 1]` hierarchy … `[Order 22]` update_file_extentions) | `[CODE VERIFIED]` — `cctns-v2/etl_master/input.cctns-pure.txt`, directly confirms the operator's "22 orders" figure at the configuration level |
| "All 22 orders completed in the latest cycle" | Not independently confirmed by a single run-status table (V2 has no consolidated per-order success/failure log the way V1's `cctns_v1_etl_run_log` does) | `[INFERRED]` from data freshness below — operator-reported, cross-validated, not directly queried |
| `etl_bookkeeping` run_state watermarks | `accused`, `disposal`, `persons`, `properties` all updated **2026-10-01 ~12:13–12:14 UTC** (within the hour of this check); `master_etl_backfill_complete` present (2026-09-28) | `[DATABASE VERIFIED]` — confirms a very recent successful cycle touched these modules |
| Replay-floor (`*__replay_from`) rows | **0** — none found under `kind='run_state'` | `[DATABASE VERIFIED]` |
| Arrest duplicates `(crime_id, accused_seq_no)` | **0** | `[DATABASE VERIFIED]` — fix holding |
| `arrests_dup`-style `(crime_id, person_id)` on `accused` | **1 group** — see §9 for root cause (NULL `person_id` collision under `GROUP BY`, not a real business duplicate) | `[DATABASE VERIFIED]`, `[NEW FINDING]` |
| Latest `crimes.date_created` / `fir_date` | 2026-10-01 11:36:29 / 2026-10-01 09:45:36 | `[DATABASE VERIFIED]` — same-day data present, confirms half-open fix still working |
| DOB populated (`persons`) | 79 / 32,790 (0.24%) | `[DATABASE VERIFIED]` — even more null than V1 |
| Phone populated | 27,057 / 32,790 (82.5%) | `[DATABASE VERIFIED]` |
| Relative name populated | 25,769 / 32,790 (78.6%) | `[DATABASE VERIFIED]` |
| `accused.person_id` NULL | 78 / 32,867 (0.24%) | `[DATABASE VERIFIED]`, `[NEW FINDING]` |
| `accused` table provenance columns | `source_system` (constant `'CCTNS_V2'` for all 32,867 rows), `source_endpoint`, `fetched_at`, `etl_run_id` | `[DATABASE VERIFIED]`, `[NEW FINDING]` — V2 already stamps its own rows with run-level provenance; this is V2-internal bookkeeping (always `'CCTNS_V2'`), not a dual-source field, but it is directly reusable as the "source observation" provenance pattern in §13 |

**Known limitations carried forward and re-confirmed, not re-litigated:** `/accused` has no independent modification timestamp (accused/arrest timestamp coupling — 523/32,860 rows at risk, established earlier this session, not re-queried here since the mechanism is structural and unchanged); list APIs expose no completeness metadata; `/update-chargesheets` has no `dateModified`; some failures are log-based, not fully durable; address/domicile is disabled in prod (`checkpoint/etl-address` frozen at 2026-09-29, `[DATABASE VERIFIED]` this session); 2026-08-24 bulk event must not be read as organic history; FK-retry/address backlogs below.

| `etl_bookkeeping` row class | Count | Resolved |
|---|---|---|
| `fk_retry/arrests` | 72 | 72 (fully resolved) |
| `fk_retry/chargesheets` | 90 | 0 |
| `fk_retry/fsl_case_property` | 2,887 | 1,993 (894 unresolved) |
| `fk_retry/updated_chargesheet` | 168 | 0 |
| `failure/etl-address` | 1,627 | 0 (feature disabled, not shrinking) |

All `[DATABASE VERIFIED]`, unchanged in shape from the prior session's numbers, confirming stability rather than drift.

---

## 4. ORIGINAL PLAN vs CURRENT REALITY

| Original assumption | Source | Evidence from current code/DB | Status | Impact on merger |
|---|---|---|---|---|
| Zero case-level overlap between V1 and V2 | dossier §10 | Re-ran the exact full-set join this session: 7,305 V1 `fir_reg_num` vs 9,535 V2 `fir_reg_num`, **0 intersection** `[DATABASE VERIFIED]` | **VERIFIED** | Core merge-is-union thesis holds |
| `accused.person_id NOT NULL` is safe | schema.sql | V2 has 78 accused rows with NULL `person_id` `[DATABASE VERIFIED]` | **CONTRADICTED** | The original unified `accused` table as literally written would reject 78 live V2 rows on load. Must relax to nullable, or route unlinked accused rows to a staging/exception table |
| `UNIQUE(crime_id, person_id)` on unified `accused` | schema.sql | 1 live V2 group currently violates this under naive GROUP BY (3 rows, same crime, all NULL `person_id`, same microsecond `date_created`, all "Absconding") `[DATABASE VERIFIED]` — note Postgres UNIQUE treats distinct NULLs as non-colliding, so this specific case would not technically violate a real UNIQUE constraint, but it does show the NOT-NULL assumption above is what actually breaks | **PARTIALLY VERIFIED** (constraint survives on a technicality; the underlying NOT NULL assumption does not) | Need an explicit "unlinked accused" handling path, not a silent constraint pass |
| V1 `accused_id` is a stable per-(crime,person) natural key suitable as unified `accused_id`/`PRIMARY KEY` | schema.sql | V1's MD5-full-row natural key churns on any field edit; 3 of 20,128 `person_code`s in `cctns_accused_details` have >1 `accused_id` (one has 69) `[DATABASE VERIFIED]` | **CONTRADICTED** | Loading V1's raw `accused_id` as a global PK would either (a) create duplicate "current" rows for the same crime+person under `UNIQUE(crime_id,person_id)`, forcing silent overwrites, or (b) require pre-collapsing V1's stale-duplicate rows before load — schema.sql does neither |
| No history table is needed | schema.sql (no such table exists) | V1 produces de facto history via stale-duplicate rows (87 at last full count); V2 produces none at all — both audits this session established per-source history is partial-to-absent | **OUTDATED** | A `change_log`/history layer is now a confirmed requirement, not optional — dossier §16 already moved past schema.sql on this, this document keeps that correction |
| Auto-merge persons at load time on name+DOB+father+mobile | DESIGN.md §3.2 | DOB populated in 0.79% (V1) / 0.24% (V2) of rows `[DATABASE VERIFIED]` — the stated match key is missing in >99% of rows on both sides | **CONTRADICTED** | Auto-merge as literally specified in schema.sql's own design doc cannot work; the dossier's candidate-link, human-reviewed model (never auto-merge) is the only approach the data supports. This document keeps that correction |
| Source precedence needed globally ("V1 wins" / "V2 wins") | Not explicit in schema.sql (silent `ON CONFLICT DO UPDATE` = implicit "last write wins"); dossier explicitly rejects a global rule | No case-level overlap exists (confirmed above), so no global precedence question even arises for crime/accused/arrest/seizure/court. Only persons (via candidate links) have a live precedence question, and that's explicitly "preserve both" per dossier §15 | **VERIFIED** (dossier's position holds; schema.sql's silent "last write wins" was never actually exercised since nothing overlaps to conflict) | No change |
| ETL-3 as a third *extractor* needed | Not proposed in (a)/(b); raised as an open question in the task prompt itself | V1 and V2 both remain independently healthy, decoupled, and have shipped 6+ independent fixes with zero cross-impact this session `[DATABASE VERIFIED]`/`[CODE VERIFIED]` across both | **VERIFIED (as "not needed")** | Confirms dossier §19's Option B recommendation; see §19 below for the full re-evaluation |
| V2's `/accused` re-fetch is timestamp-complete | Not an original-plan assumption, but implicit in schema.sql treating `accused` as a reliable current-state source | Accused/arrest-timestamp coupling confirmed earlier this session (523/32,860 rows), structural, still present, mechanism unchanged | **CONTRADICTED** (new finding this session's earlier audit, reconfirmed structurally unchanged here) | Unified `accused_unified`/current-state table cannot be assumed authoritative on status without a parallel observation/history layer — §10 |
| Two-DB, no-overlap architecture means no conflict-resolution logic is needed anywhere | Implicit across dossier | Holds for every entity except persons (where it was never claimed) | **VERIFIED** | Simplifies §9/§18 significantly — conflict detection work is scoped to identity-linking only, not general field reconciliation |

---

## 5. CURRENT DATABASE ANALYSIS

Full catalogs were captured earlier this session and re-spot-checked here; not repeated table-by-table (see `MERGER_DOSSIER.md` §4/§5 for the full constraint/trigger/index catalogs, which were re-verified live and are unchanged in shape). Deltas found this pass:

- V1: no new tables, no constraint changes, counts static-to-growing (dossier count unchanged at 34,384 since run 56 produced 0 inserts — expected, since run 56 re-processed an already-current window).
- V2: `accused` table confirmed to carry `source_system`/`source_endpoint`/`fetched_at`/`etl_run_id` columns `[DATABASE VERIFIED]` — not previously inventoried in this detail; directly reusable for consolidation-layer provenance (§13/§16).
- V2 `accused` additionally has `type` and `accused_code`/`seq_num` columns (27 columns total) `[DATABASE VERIFIED]` — richer than `MERGER_DOSSIER.md`'s summary table implied; the full column list is now on record in this document's §3/§9 evidence.

---

## 6. V1/V2 ENTITY MAPPING

Unchanged from `MERGER_DOSSIER.md` §8 and the PDF's own structure — re-confirmed, not re-derived:

| V1 | V2 | Relationship |
|---|---|---|
| `cctns_fir` | `crimes` | Same concept, 0 row overlap |
| `cctns_accused` + `cctns_accused_details` | `accused` + `persons` | V1 packs person+accused+seizure into one row; V2 splits |
| (fields inside `cctns_accused_details`) | `arrests` | V2 has explicit 41A/apprehended/absconding/died flags V1 lacks |
| (drug fields inside dossier) | `mo_seizures`, `properties`, `fsl_case_property` | Different structure, same concept |
| `cctns_court` | `chargesheets` + `charge_sheet_updates` + `disposal` | V1 flat, V2 split by lifecycle stage |
| — | `hierarchy`, `interrogation_reports`, GPS/telecom/financial/supply-chain fields | V2-exclusive, per the PDF §3 |

No pair is a `DIRECT_EQUIVALENT` at the row level; the PDF's 37 direct *column* mappings are real and useful for field-level translation, but do not imply row-level equivalence — this distinction was implicit in the dossier and is made explicit here because schema.sql's single-current-row design conflated the two.

---

## 7. V1/V2 DATA OVERLAP

Recalculated fresh this session, against current data, not assumed from the original report:

**Crime/FIR:** 7,305 V1-only, 9,535 V2-only, **0 both** — exact full-set intersection, both directions `[DATABASE VERIFIED]`.

**Person (approximate, attribute-matched, not canonical ID) — re-run fresh in a follow-up pass**, two-tier methodology (Tier 1: phone match + ≥1 shared name token; Tier 2: name+father-name match, excluding Tier-1 pairs), at the **raw accused-row level** (34,384 V1 rows × 32,789 V2 rows with a resolved person):

| Tier | Candidate pairs | Distinct V1 rows | Distinct V2 rows |
|---|---|---|---|
| 1 (phone + name token) | 2,303 | 1,088 | 468 |
| 2 (name + father, Tier-1 excluded) | 653 | 524 | 155 |

`[DATABASE VERIFIED]`, this session. **This does not match the ~300–330 figure previously cited** (from `MERGER_DOSSIER.md` §10, a prior same-session pass whose exact script is not available to re-run byte-for-byte). The discrepancy is methodological, not a correction of a wrong prior number — and it is itself a useful finding:

- The prior figure's phone tier was described as "phone match AND at least one shared name token, 334 V1 rows" — directionally the same filter used here, but this re-run finds **1,088** V1 rows on that same filter, roughly 3× higher.
- The most likely cause: **raw row-level counting is inflated by V1's own known duplicate-row problem** (§2 above — natural-key churn means one real person can have multiple `cctns_accused` rows, all sharing the same phone number, each counted separately here). A phone number shared by, say, 5 stale snapshot rows of the same real V1 person against 2 V2 rows produces 10 raw candidate pairs for what is really at most 1–2 genuine cross-system identity questions.
- **This reinforces, with a concrete number, a design decision already made in `ETL3_MERGER_IMPLEMENTATION_PLAN.md`**: V1's stale-duplicate collapse (§10 of that document) must happen *before* identity-link candidate generation runs, not after — doing it after inherits this inflation directly into the review queue a human would have to work through.

**Action, not yet taken:** re-run this matching at the *person* level (post-duplicate-collapse `persons_unified`, per ETL-3 Phase 7 then Phase 10) rather than the raw accused-row level, before presenting any candidate-pair count as the real scope of the identity-linking review workload. The numbers above should not be used as a sizing estimate for that review queue as-is.

**Accused/arrest/seizure/court/property/chargesheet:** 0 overlap by construction (scoped to crime/FIR, which never overlaps) — **VERIFIED**, unchanged.

---

## 8. IDENTITY AND KEY STRATEGY

This is the section where the original plan's two source documents actively disagree with each other, and where this document picks a side with reasons.

**schema.sql/DESIGN.md position:** auto-resolve to one `persons` row per real person at load time, matched on name + DOB + father's name + mobile.

**MERGER_DOSSIER.md position:** never auto-merge; `persons_unified` as plain union + `identity_links` (candidate/confirmed/rejected) for human review only.

**Resolution, with fresh evidence:** DOB is populated in 0.79% of V1 accused rows and 0.24% of V2 persons rows `[DATABASE VERIFIED]`, both re-confirmed this session. A match key that requires a field present in roughly 1 in 125–400 rows cannot be the basis of an automatic merge for the other 99%+ of rows — schema.sql's own stated mechanism is not executable as written. **This document affirms the dossier's position and rejects schema.sql's auto-merge instruction as superseded by evidence**, not as a stylistic preference.

Confirmed usable signals, by population rate:
- Father/relative name: 92.0% (V1) / 78.6% (V2) populated
- Mobile/phone: 79.4% (V1) / 82.5% (V2) populated
- Name: effectively 100% on both sides (not separately re-queried this pass; established throughout prior audits)

**Recommended key model** (unchanged from dossier §16, reaffirmed here against the schema.sql alternative): `persons_unified` (plain union, `(source_system, source_record_id)` key, exactly matching the pattern V2's own `accused` table already uses internally — see §3/§5) + `identity_links(person_a, person_b, match_basis, confidence_score, status)`, status never auto-set to `confirmed`.

For `crime_id`/`accused_id`: schema.sql's approach (use the source ID directly as the unified PK) is **safe for `crimes`** (0 collision risk — V1 `fir_reg_num` format and V2 `crime_id`/ObjectId format are structurally distinct and the values never overlap, confirmed §7) but **unsafe for `accused`** per §4's finding — V1's `accused_id` is not stable across time for the same real (crime, person) pair. Recommended: unified `accused_unified` keyed on `(source_system, source_record_id)` as the dossier already proposed, with a separate, explicit **current-vs-historical flag** derived by the consolidation layer (latest `source_created_at`/`source_modified_at` per (crime, resolved-person) wins "current"; everything else becomes a `change_log` row) — this is what actually replaces schema.sql's naive `UNIQUE(crime_id, person_id)` + direct-accused_id-as-PK design.

---

## 9. CONFLICT ANALYSIS

Because case-level overlap is exactly zero (§7), there is **no field-level conflict to resolve** for crime/accused/arrest/seizure/court/property/chargesheet between V1 and V2 — this was already the dossier's finding and is re-confirmed, not revised.

**New finding this pass, scoped narrowly to V2 internal data quality, not a V1/V2 conflict:**

| Entity | V2 ID | Field | Finding |
|---|---|---|---|
| `accused` | crime_id=`66544fae884cf131822968f5`, 3 distinct `accused_id` (`66545908588cd61d3048d0e0`, `66545908588cd625e448d0df`, `66545908588cd6cc4448d0de`) | `person_id` | All 3 rows have NULL `person_id`, identical `date_created` to the microsecond, identical `accused_status='Absconding'` `[DATABASE VERIFIED]` |

This is not a V1/V2 conflict (it's entirely within V2) and not a business-data conflict (no two different values for the same fact) — it's a **structural gap**: three accused entries for one crime where the person link was never resolved. Classified as a known gap (§14 of `MERGER_DOSSIER.md`'s taxonomy extends to cover it), not fixed, not silently resolved, surfaced to `source_gap_ledger` in the target design (§13).

No conflict table entry has V1 ID + V2 ID populated for the same real-world fact anywhere in this dataset, because no such pair currently exists in the data — consistent with zero overlap.

---

## 10. ACCUSED/ARREST STATUS STRATEGY

Re-affirming `MERGER_DOSSIER.md` §12 with the newly-confirmed provenance columns factored in:

**V2:** `/accused` re-fetch is gated by arrest-row timestamps, not accused's own (`[CODE VERIFIED]`, established in the prior session's code read of the window-guard/checkpoint logic, unchanged this session). 523/32,860 rows previously identified as at-risk (not re-queried this pass — the mechanism is structural, the risk applies prospectively to all future rows, re-querying the exact count doesn't change the finding). The newly-confirmed `accused.etl_run_id`/`fetched_at` columns `[DATABASE VERIFIED]` mean every V2 accused row already carries the information needed to know *when* it was last actually re-observed, independent of whether its `date_modified` moved — this is a usable building block for the "source observation" layer below that wasn't factored into the original dossier's §12 writeup.

**V1:** status changes produce a brand-new dossier row under a new MD5 key; the old row is retained, unflagged (87 at last full count, confirmed still real-but-small via the churn distribution in §2: only 3 of 20,128 person_codes show any churn, one of them heavily).

**Recommended unified representation** (confirmed, not changed from dossier's direction, made concrete): three-layer model, not two —
1. **Source observation** — every row V1/V2 ever produced, keyed `(source_system, source_record_id, fetched_at or equivalent)`, append-only, never updated. This is what V2's `accused.etl_run_id`/`fetched_at` already is per-row; V1's stale-duplicate rows are the same concept achieved differently (new MD5 key instead of an explicit timestamp).
2. **Current canonical state** — one row per (crime, resolved-person), the "freshest-evidence-wins" row, explicitly computed, not naively upserted.
3. **Status change log** — derived by diffing consecutive source observations for the same (crime, resolved-person); this is where "did the status actually change, or did an unrelated timestamp move" gets resolved, and where V2's quiet-staleness risk becomes *visible* instead of silent (a resolved-person with no new source observation for N days despite its parent crime moving is a detectable, alertable condition once this layer exists — it isn't today, in either source).

---

## 11. HISTORY STRATEGY

What actually exists, re-assessed against fresh churn data:

- **V1:** partial, accidental history via retained stale-duplicate rows (bounded — only 3 of ~20,128 persons show churn, confirmed §2) plus a genuine field-level `audit_log` for non-key-field changes (336 rows, unchanged this session). Reconstructable: the *order* of versions (by `accused_id` insertion order / `created_at`), not a clean "this was the state as of exactly date X" without inference.
- **V2:** none. `date_modified` exists but, per §10, does not reliably mean "this fact changed."
- **What cannot be reconstructed, either source:** pre-change values for the 2026-08-24 bulk event (it overwrote `date_modified` on 86%+ of rows in one administrative action — the old per-row timestamps are gone, confirmed via the hour-concentration statistics established earlier this session and not contradicted by anything found this pass).
- **2026-08-24 handling, reaffirmed:** must be excluded from any `change_log` backfill baseline — treating it as ~36,000 genuine business-state transitions would be a fabricated history, not a reconstructed one. This is unchanged from the dossier and is the single most important guardrail in this section.

**Classification for the unified `change_log`:** current state (layer 2 above) is NOT history; a diff between two source observations IS a business-state transition only if it isn't attributable to the 2026-08-24 event or an equivalent future bulk operation (the design should keep an explicit, append-only list of "known bulk/administrative events to exclude from history inference," not just this one hardcoded date).

---

## 12. SOURCE PRECEDENCE STRATEGY

Reaffirmed from dossier §15, with the new NULL-`person_id` finding added as a concrete row:

| Entity | Field | Rule | Confidence | Reason |
|---|---|---|---|---|
| Crime/Accused/Arrest/Seizure/Court (all) | any | No rule needed | n/a | 0 overlap |
| Person, where `identity_links` flags a candidate match | any | Preserve both source observations; never pick a winner automatically | n/a | DOB unusable (§8), no reliable per-field V1 timestamp |
| V2 `accused` with NULL `person_id` | `person_id` | Route to an exception/unlinked queue, do not drop, do not synthesize a person | High (mechanical) | §9 — these are structurally incomplete source rows, not a precedence question at all |
| Case status vocabulary | `fir_status` vs `case_status` | Store both raw, reuse DOPAMS-BE's existing normalization mapping if one exists (not found in this repo — see §20) | n/a | Avoid inventing a second vocabulary mapping |

No global "V1 wins"/"V2 wins" rule — re-confirmed, no new evidence changes this.

---

## 13. UNIFIED DATABASE DESIGN

This supersedes schema.sql's table list for the reasons established in §4/§8, while keeping everything from schema.sql that evidence did **not** contradict (the column-level field mapping from the PDF, the `crimes`/`persons` `(source_system, source_record_id)` key pattern, FK-enforced referential integrity within a single source's rows).

**SOURCE DATA (append-only, one row per source observation):**
```
crimes_source, accused_source, arrests_source, seizures_source,
chargesheets_source, persons_source
  — (source_system, source_record_id, fetched_at, etl_run_id, payload)
  — V2 rows map near-directly (it already carries fetched_at/etl_run_id per row,
    confirmed §3/§5); V1 rows map from (accused_id, created_at) per natural-key
    version
```

**CURRENT CANONICAL DATA (one row per unified entity, computed not upserted-blind):**
```
crimes_unified, accused_unified, arrests_unified, seizures_unified,
chargesheets_unified, persons_unified
  — same field set as schema.sql's tables, MINUS the NOT NULL on person_id
    (§4 — must be nullable, with unlinked rows flagged, not rejected)
  — PRIMARY KEY (source_system, source_record_id) for crime/accused/arrest/etc,
    NOT raw source ID reused directly for accused (§8 — V1 accused_id is not
    stable)
```

**HISTORY:**
```
change_log  — (entity, unified_id, field, old_value, new_value, observed_at,
                source_run_id, excluded_bulk_event_id NULLABLE)
bulk_event_exclusions  — (event_id, date_range, description) seeded with
                          2026-08-24; append new ones as discovered
```

**IDENTITY:**
```
identity_links  — (person_a, person_b, match_basis, confidence_score, status)
                   status NEVER auto-set to confirmed
```

**SYNCHRONIZATION CONTROL:**
```
consolidation_cursor  — (source_system, source_table, last_consolidated_source_run_id
                          or last_consolidated_fetched_at) — see §15 for why this is
                          NOT simply "max(fetched_at) in the unified DB"
```

**RECONCILIATION:**
```
source_gap_ledger  — seeded from cctns_v1_failed_fetch_window (180 rows) and the
                      relevant etl_bookkeeping rows (fk_retry: 90+168+2,887;
                      failure: 1,627) — copied, not re-derived
reconciliation_run_log  — (run_id, source_system, table, source_count,
                            unified_count, delta, status)
```

This is a direct extension of `MERGER_DOSSIER.md` §16, not a replacement — the source/current/history/identity/sync/reconciliation split is unchanged; what's new here is (a) the explicit source-observation layer, driven by V2's already-existing `fetched_at`/`etl_run_id` columns found this session, and (b) the nullable-`person_id` correction to the current-state table, driven by the NULL-collision finding in §9.

---

## 14. INITIAL MIGRATION DESIGN

Dependency order, derived from the actual FK structure (V1: enforced FKs `cctns_fir → {accused, accused_details, court}`; V2: no FKs, enforced only by application convention `crime_id`/`person_id` references):

1. Schema creation (empty tables, all constraints as in §13)
2. `hierarchy` (V2 reference data — no V1 equivalent, load as-is)
3. `crimes_source` then `crimes_unified` — both sources, plain union, dependency root for everything else
4. `persons_source` then `persons_unified` — plain union, **no linking yet** (§8 — linking is a separate, later, human-reviewed phase)
5. `accused_source`/`accused_unified`, `arrests_source`/`arrests_unified` — depends on 3+4; **V1's stale-duplicate rows must be collapsed here**, not left for a later phase — group by the pre-MD5-fix logical key (fir_reg_num + name + father + mobile-or-DOB), order by `created_at`, latest → `*_unified`, rest → `change_log` entries, excluding 2026-08-24-dated rows from being misread as real transitions
6. `seizures_unified`, `chargesheets_unified` — depends on 3
7. `identity_links` candidate generation — **separate, independently schedulable, run after 4**, never blocks 5/6
8. `source_gap_ledger` seeding — can run anytime after schema creation, has no FK dependency on the above
9. Validation: row counts per unified table == source counts minus the 87 (or however many at migration time) V1 rows correctly collapsed into `change_log`; spot-check the specific collapsed group (e.g., re-identify the 69-row `person_code` outlier found in §2 and confirm it collapses to exactly 1 current row + 68 `change_log` entries)
10. Cutover (§22)

Everything above is read-only against V1/V2; nothing here writes to `cctns_v1` or `cctns-v2`.

---

## 15. INCREMENTAL SYNCHRONIZATION DESIGN

This is the section the task explicitly says not to hand-wave, and where the original documents (both schema.sql and the dossier) stopped short of a concrete mechanism. Answering the task's explicit question: **do not use unified `fetched_at` as the sole consolidation cursor.**

**Why `fetched_at` alone is insufficient**, with evidence: V2's own `accused.fetched_at` is per-row, but §10 already established that a status change doesn't reliably move the row that the consolidation layer would be watching (the accused/arrest timestamp coupling). If the consolidation cursor is just "give me everything with `fetched_at` > last checkpoint," it inherits V2's own quiet-staleness risk one layer up, silently.

**Correct control key**, built from what's actually available per source:

| Element | V1 source | V2 source |
|---|---|---|
| `source_system` | `'V1'` constant | `accused.source_system` = `'CCTNS_V2'` `[DATABASE VERIFIED]`, or constant `'V2'` for tables without the column |
| `source_module` | entity (`fir`/`accused`/`accused_details`/`court`) | `module_name` as used in V2's own `etl_bookkeeping` `[CODE VERIFIED]` |
| `source_run_id` | `cctns_v1_etl_run_log.id` | `accused.etl_run_id` (confirmed present, UUID) `[DATABASE VERIFIED]`; for tables without it, the containing `master_etl.py` cycle's identifier (not independently confirmed to exist per-row on every table — `[UNKNOWN]`, worth confirming before implementation on tables other than `accused`) |
| `source_record_id` | V1 natural ID (`fir_reg_num`/`accused_id`) | source ID (`crime_id`/`accused_id`/etc.) |
| `source_checkpoint` | V1's own `cctns_v1_etl_run_log` high-water mark | V2's own `etl_bookkeeping(kind='run_state')` watermark per module |
| `consolidation_checkpoint` | **separate**, owned by the consolidation service, = the highest `source_run_id` (not `fetched_at`) it has fully processed, per `(source_system, source_module)` | same |

**Mechanism:**
1. Consolidation service reads each source's own bookkeeping (`cctns_v1_etl_run_log` / `etl_bookkeeping`) **read-only**, purely to discover "is there a run newer than what I last fully processed" — it never writes there.
2. For a newly-discovered source run, it reads all rows touched by that run (V1: via `cctns_v1_etl_row_action`, which already logs per-row insert/update per run, 69,539 rows confirmed earlier this session `[DATABASE VERIFIED, prior pass]`; V2: via `etl_run_id` on the touched tables, confirmed present on `accused`).
3. Upserts into `*_source` (append, keyed by `(source_system, source_record_id, source_run_id)` — never overwritten, so replay is free).
4. Recomputes `*_unified` current-state row for each touched `(crime, resolved-person)` by comparing the new source observation's `source_modified_at`/`created_at` against whatever the current-state row already reflects — **stale-write prevention**: if the incoming row is older than what's already canonical (scenario: "a source sends an older record after a newer one," task §12 item 7), it is recorded in `*_source` and in `change_log` (it's still a real fact) but does **not** overwrite the current-state row.
5. Writes `change_log` entries for any field that actually differs between the new observation and the prior canonical value, excluding anything inside a `bulk_event_exclusions` window.
6. Only after all of 3–5 commit for the whole source run does the consolidation service advance its own `consolidation_cursor` for that `(source_system, source_module)` to that `source_run_id` — the same floor-before-commit pattern V2's own `etl_window_guard.py` already uses successfully `[CODE VERIFIED]`.

**Idempotency:** replaying the same source run twice re-upserts the same `*_source` rows (no-op on an already-identical row) and produces no new `change_log` entries (diff against current canonical finds nothing changed) — safe by construction, same as task §12 item 5.

**Deduplication:** happens at step 4 (resolved-person identity) and at V1's stale-duplicate collapse (§14 step 5), not as a separate pass.

---

## 16. CONSOLIDATION CONTROL PLANE

The `consolidation_cursor` table from §13, one row per `(source_system, source_module)`:

```
consolidation_cursor(
  source_system, source_module,
  last_processed_source_run_id,
  last_processed_at,              -- when the consolidation service itself ran, for staleness alerting
  status                           -- idle | running | failed
)
```

This is deliberately **not** a single global cursor — V1 and V2 advance independently and at different cadences (V1: ~daily via Airflow; V2: four times daily via cron, confirmed `[CODE VERIFIED]` earlier this session), and a module-level failure in one (e.g., V2's `fsl_case_property` FK-retry backlog) must not stall consolidation of unrelated modules (e.g., `crimes`).

---

## 17. FAILURE / REPLAY DESIGN

Working through the task's ten explicit scenarios against the §15 mechanism:

| # | Scenario | What happens |
|---|---|---|
| 1 | V1 ETL succeeds, consolidation fails | V1's own `cctns_v1_etl_run_log` row is unaffected (consolidation never writes there). `consolidation_cursor` for V1 modules does not advance. Next consolidation attempt re-reads the same V1 run from scratch — safe (idempotent, §15) |
| 2 | V2 ETL succeeds, consolidation fails | Same as #1, mirrored for V2's `etl_bookkeeping` |
| 3 | Consolidation partially commits | Must not happen by design — step 6 of §15 only advances the cursor after all of steps 3–5 commit as one transaction per source run; a partial commit without a cursor advance is equivalent to #1/#2 on next attempt |
| 4 | Consolidation crashes after 50% of records | Same as #3 — uncommitted transaction rolls back entirely (standard Postgres transaction semantics), cursor unchanged, full re-process on restart |
| 5 | Same source window replayed | No-op on `*_source` (already-seen `source_run_id`+`source_record_id` combinations), no new `change_log` entries — safe (§15 idempotency) |
| 6 | V1 and V2 both update the same canonical entity | Cannot occur for crime/accused/arrest/seizure/court (0 overlap, §7). For a `identity_links`-confirmed person, both sources' observations land in `persons_source` independently; `persons_unified` has no single "current" row to fight over because persons are never merged into one row (§8) — each source's person row stays canonically its own |
| 7 | Source sends an older record after a newer one | Stale-write prevention (§15 step 4) — recorded as a source observation and in `change_log`, current-state row not overwritten |
| 8 | A source record disappears | Neither V1 nor V2 signals deletes (confirmed: V1 has `ON DELETE CASCADE` FKs but nothing in the ETL issues deletes against source tables based on upstream disappearance `[CODE VERIFIED, prior session's read of the sync scripts]`; V2 has no deletes in its upsert-only model). Unified row is flagged `unified_only`/stale in reconciliation (§18), never auto-deleted — R6 in dossier's risk register, reaffirmed |
| 9 | A known source gap later becomes available | Operator/future ETL run resolves the `OPEN` ledger row to `RESOLVED` at the source (V1's own mechanism, `resolve_windows_not_failing` `[CODE VERIFIED, prior session]`); consolidation picks it up as a normal new source run on next pass — no special handling needed beyond the normal mechanism |
| 10 | A duplicate source record is encountered | V1: caught by the natural-key UNIQUE constraint at the source already (0 duplicate `natural_key`s confirmed, prior session and unchanged); V2: caught by the new arrests unique index at the source (0 duplicates, re-confirmed this session). Consolidation never sees true source-level duplicates for these; for the one NULL-`person_id` grouping artifact (§9), it is not a duplicate at all once `person_id` nullability is handled correctly (§13) |

---

## 18. RECONCILIATION DESIGN

Per source, per table, on a schedule (unchanged in structure from dossier §22, reaffirmed):

```
source_count (from source's own bookkeeping-reported count, read-only)
  vs unified_source_count (rows in *_source for that source_system/table)
  vs unified_current_count (rows in *_unified)
source_gap_ledger open count vs fixed baseline (180 V1; ~1,150 V2 fk_retry; 1,627 V2 address)
  — alert only on growth beyond baseline, not on the baseline itself
identity_links candidate / confirmed / rejected counts
```

New in this pass: the reconciliation job should also assert `consolidation_cursor.last_processed_source_run_id` for each `(source_system, source_module)` is monotonically non-decreasing and matches the highest `source_run_id` actually present in `*_source` — a cheap, direct check that the control-plane mechanism in §16 is behaving as designed, not just that row counts look right.

---

## 19. ETL-3 vs CONSOLIDATION vs CDC vs TRIGGERS

Re-run against the current, re-verified evidence (not just the dossier's framing):

**Is a third ETL required?** No, in the sense of a third *extractor* hitting the CCTNS APIs — V1's API is already the fragile point in this whole system (it's the source of the permanent 180-window ORA-06502 gap, confirmed this session); adding a third consumer of it for no functional reason is a cost with no offsetting benefit.

**What is required is a read-only consolidation service** (Option B), re-confirmed, with the control plane detailed in §15/§16:
- **Reads:** V1's `cctns_v1_etl_run_log`/`cctns_v1_etl_row_action` and V2's `etl_bookkeeping` (read-only, to discover new source runs), plus the actual business tables themselves.
- **Writes:** only `dopams_unified` (`*_source`, `*_unified`, `change_log`, `identity_links`, `consolidation_cursor`, `source_gap_ledger`). Never writes to `cctns_v1` or `cctns-v2`.
- **Cursor:** `consolidation_cursor(source_system, source_module) → last_processed_source_run_id` — not `fetched_at`, per §15's explicit reasoning.
- **Change detection:** new `source_run_id` values appearing in the source's own run-log/bookkeeping tables since the last cursor value.
- **Recovery:** §17, all ten scenarios — transactional, cursor-gated, idempotent by construction.
- **Avoids missing changes:** by reading from each source's own authoritative run/row log rather than re-deriving "what changed" from timestamps the way a naive `fetched_at > X` poll would (the exact failure mode `[CONTRADICTED]` in §4/§15 that would inherit V2's quiet-staleness risk).
- **Avoids duplicate processing:** `(source_system, source_record_id, source_run_id)` keys on `*_source` make re-ingesting an already-seen run a no-op.

**Why not CDC (Option C):** no logical replication/WAL-mining setup is evidenced on either source Postgres instance `[UNKNOWN — not configured, not observed]`; V1's full-row-MD5-on-any-change pattern would make CDC report a storm of "new rows" for what are really just re-observations of the same logical entity, which is a worse signal than what the consolidation service's own diffing (§15 step 5) already produces deliberately.

**Why not triggers (Option D):** V2 has no FK integrity today (§3, unchanged) and V1's natural key changes on every edit — triggers firing on every such "change" would be high-frequency and fragile, and would couple write-path latency on two independently-operated production databases to a third system's availability, which both source teams have spent this session proving they don't want (every fix shipped independently, with zero coordination overhead, specifically because the systems are decoupled).

**Why not direct writes (Option A):** would require modifying both ETLs' logic to understand unified-DB semantics (resolved-person identity, `change_log`, stale-write prevention) — recoupling two codebases that this session's entire fix history (6+ independent, zero-cross-impact deployments) demonstrates are healthier apart.

**Verdict, reaffirmed: Option B**, now specified concretely enough to build (§15/§16), which is the gap the original dossier left open.

---

## 20. DOPAMS APPLICATION COMPATIBILITY

`[UNKNOWN]` — **no DOPAMS backend application exists in this repository.** The repository contains exactly three top-level directories: `cctns-v1` (the V1 ETL), `cctns-v2` (the V2 ETL), and `dopams_cctns` (schema/design documents only — no application code) `[CODE VERIFIED]`, confirmed by direct directory listing this session. There is no code here that reads from or writes to either CCTNS database on behalf of an end-user-facing DOPAMS application, and no materialized views, API layer, or ORM models for one were found.

This means:
- **READ COMPATIBILITY:** cannot be assessed — unknown what DOPAMS BE reads today.
- **WRITE COMPATIBILITY:** cannot be assessed — unknown whether DOPAMS BE writes to CCTNS databases at all (plausible it's read-only, but unconfirmed).
- **MIGRATION COMPATIBILITY:** cannot be assessed.

This is unchanged from `MERGER_DOSSIER.md`'s R5 risk entry and §24 step 3's explicit caveat — re-confirmed, not resolved, by this pass. **This is the single largest open gap blocking a real cutover plan** (§22 below is therefore necessarily conditional). Recommend treating "locate and inspect the actual DOPAMS BE repository" as a precondition for implementation, not an implementation-time detail.

---

## 21. KNOWN GAPS AND LIMITATIONS

Carried forward from `MERGER_DOSSIER.md` §14, re-confirmed where re-checked this pass, with one addition:

- V1: 180 single-day ORA-06502 gaps, permanent, now ledgered, all still `OPEN` `[DATABASE VERIFIED]`, 0 `RESOLVED`.
- V1: 87-ish stale-duplicate rows, confirmed via a different method this pass (person_code churn: 3 of 20,128 affected, one at 69 rows) — same underlying phenomenon, consistent order of magnitude, not identical count (different detection method, different table scope).
- V2: ~1,150 permanently-capped FK-retry rows (90 chargesheets + 168 updated_chargesheet + 894 unresolved fsl_case_property), re-confirmed this pass, unchanged in shape.
- V2: 1,627 unresolved address rows, feature disabled in prod, re-confirmed frozen (`checkpoint/etl-address` last touched 2026-09-29).
- V2: accused/arrest timestamp coupling, structural, 523 rows previously identified, mechanism re-confirmed unchanged.
- **New this pass:** V2 `accused.person_id` NULL on 78 rows (0.24%), 3 of them colliding on `(crime_id, NULL)` under naive grouping — a genuinely new, small, bounded gap that the original schema.sql's `NOT NULL` constraint did not anticipate.
- 2026-08-24 bulk event — exclude from history backfill, re-affirmed, no new evidence changes this.

---

## 22. PRODUCTION CUTOVER PLAN

Conditional on resolving §20 (DOPAMS BE's actual database target), which is outside this repository's visibility:

1. Stand up `dopams_unified` per §13, run consolidation continuously against both sources for a validation window, comparing via §18's reconciliation job.
2. Add DOPAMS BE read access for **net-new** surfaces only first (cross-case person search spanning V1+V2 via confirmed `identity_links`) — lowest risk, nothing existing to regress.
3. Only after that's stable, and only once §20 is resolved, migrate existing BE queries module-by-module.
4. V1 and V2's own databases and ETLs remain untouched throughout — this is unchanged from the dossier and is a hard constraint, not a preference.

---

## 23. ROLLBACK PLAN

Unchanged from dossier §25: because the consolidation service never writes to V1/V2, rollback at any stage is "point BE reads back at whatever it read before." `dopams_unified` can be dropped and fully rebuilt from the two sources at any time (every row in it is a read-only derivative, `*_source` being append-only and fully reconstructible from each source's own run/row logs). No new rollback risk was discovered this pass.

---

## 24. MONITORING AND ALERTING

Extends dossier §23 with the control-plane specifics from §16:

- `source_gap_ledger` open count exceeding its fixed baseline (180 V1 / ~1,150+1,627 V2) — new gaps, not the known ones.
- `consolidation_cursor.last_processed_at` staleness per `(source_system, source_module)`, thresholds matched to each source's own cadence (V1 ~24h via Airflow; V2 ~6h via the confirmed four-times-daily cron, `[CODE VERIFIED]`).
- `consolidation_cursor` regression check (§18) — cursor must never decrease.
- A per-module count of V2 `accused` rows with NULL `person_id` arriving per consolidation run — new, specific to the §9/§21 finding, cheap, and gives early warning if the rate changes materially from the current 0.24% baseline.

---

## 25. RISKS

All of dossier §26's risks (R1–R7) are reaffirmed, unchanged in substance. Additions from this pass:

| # | Risk | Evidence | Severity |
|---|---|---|---|
| R8 | DOPAMS BE application is entirely outside this repository's visibility | §20, directory listing this session | **Blocks §22 cutover planning entirely** until resolved — elevate above R5's original framing, since this pass confirms it's not just "unconfirmed," it's "not present in any form here" |
| R9 | schema.sql's auto-merge-persons design, if anyone builds from that document without reading this revalidation, would silently merge the wrong people | §4, §8 — DOB-based match key is populated on <1% of rows, so any implementation naively following schema.sql's literal instruction would effectively be matching on name+mobile+father-name alone, with no DOB cross-check in practice | High — this is a "stale design doc read by someone new" risk, not a code risk; mitigate by this document existing and being discoverable next to schema.sql |
| R10 | V2's per-row `etl_run_id`/`fetched_at` are confirmed present on `accused` but not independently confirmed on every other V2 table | §15 — `[UNKNOWN]` for tables other than `accused` | Medium — affects how complete the source-observation layer (§13) can be on day one; needs a quick column check across all 14 V2 business tables before implementation |

---

## 26. OPEN DECISIONS

Carried forward from dossier §27, plus:

- Re-run the ~300–330 candidate person-match batch against current row counts before identity-linking implementation starts (§7 — the figure is carried forward, not re-derived, this pass).
- Confirm which V2 tables beyond `accused` carry `etl_run_id`/`fetched_at`/`source_system` (R10) before finalizing the source-observation schema.
- Locate the actual DOPAMS BE repository/deployment (R8/§20) — this blocks real cutover sequencing, not just nice-to-have context.
- Confidence threshold for surfacing an `identity_links` candidate (unchanged open question).
- Whether V1's 27 pre-2002 FIRs and 180 gapped windows get any out-of-band backfill effort, or are permanently accepted (unchanged).
- Who owns `dopams_unified` operationally (unchanged).

---

## 27. IMPLEMENTATION PHASES

| Phase | Scope | Status |
|---|---|---|
| 0 | V1 run-lock, known-gap classification; V2 checkpoint/half-open/arrests fixes | **Done, re-verified live this session** |
| 0.5 (new) | Resolve R8/R10: locate DOPAMS BE, confirm `etl_run_id` coverage across V2 tables | Not started — recommended before Phase 1 |
| 1 | `crimes_source`/`crimes_unified` | Not started |
| 2 | `accused`/`arrests`/seizure/court family, including V1 stale-duplicate collapse | Not started |
| 3 | `persons_source`/`persons_unified` (no linking) | Not started |
| 4 | `identity_links` candidate generation (re-run matching batch first) | Not started |
| 5 | `change_log` + `bulk_event_exclusions` | Not started |
| 6 | `source_gap_ledger` + `consolidation_cursor` + reconciliation job | Not started |
| 7 | BE cutover | Not started, blocked on Phase 0.5 |

---

## 28. FINAL RECOMMENDATION

**Is the original merger plan still valid?** Partially. The PDF's field-level column mapping is fully valid and unaffected by anything found during the ETL audits — it's a dictionary, not an architecture, and dictionaries don't go stale from operational fixes. `MERGER_DOSSIER.md`'s union-not-dedup thesis, zero-overlap finding, and Option B recommendation are **re-confirmed** by fresh data this session, not just carried forward on faith. schema.sql/DESIGN.md's auto-merge-persons instruction and NOT-NULL/UNIQUE accused design are **superseded** — they predate evidence (DOB nullness, V1 key churn, V2 NULL person_id) that makes them unworkable as literally written.

**What changed:** nothing about the V1/V2 code or data changed between the dossier and this document (both branches are at the same commits checked at the start of this pass) — what changed is the *depth* of verification: §15's concrete cursor mechanism, §9's NULL-person_id finding, §8's quantified DOB-nullness evidence, and §20's hard confirmation that no DOPAMS BE code exists in this repository are all new since the dossier, not reversals of it.

**Final architecture:** Option B, a read-only consolidation service, with the source/current/history/identity/sync/reconciliation six-table-family split from §13, the `(source_system, source_module) → last_processed_source_run_id` cursor from §15 (explicitly not `fetched_at`), and the ten-scenario failure handling in §17. No ETL-3 extractor, no CDC, no triggers, no changes to V1 or V2.

**What should happen before any implementation starts:** Phase 0.5 (§27) — find DOPAMS BE, confirm V2's per-table provenance column coverage. Both are cheap, both materially change how much of §13 can be built with full confidence on day one, and neither requires touching production data.
