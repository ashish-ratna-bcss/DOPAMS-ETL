# ETL-3 Source Compatibility Matrix

Table-by-table incremental-read analysis for both source databases, queried fresh this session against `information_schema` and the live data. Companion to `ETL3_MERGER_IMPLEMENTATION_PLAN.md`. Evidence tags: `[CODE VERIFIED]` `[DATABASE VERIFIED]` `[INFERRED]` `[UNKNOWN]`.

---

## V2 (`cctns-v2` database, schema `public`)

Every one of the 14 live business tables was checked directly against `information_schema.columns` this session — not assumed from the `accused` table alone, which is what the prior revalidation document had left as `[UNKNOWN]` (its R10).

| V2 table | Rows | PK | `etl_run_id` | `fetched_at` | `source_system`/`source_endpoint` | `date_created`/`date_modified` | Usable incremental signal | Limitations |
|---|---|---|---|---|---|---|---|---|
| `crimes` | 9,535 | `crime_id` | ✓ | ✓ | ✓ | ✓ | `etl_run_id` (preferred) or `fetched_at` | None found |
| `accused` | 32,867 | `accused_id` | ✓ | ✓ | ✓ | ✓ | `etl_run_id` (preferred) | `date_modified` does **not** reliably reflect a real status change — re-fetch is gated by the sibling `arrests` row's timestamp, not this row's own (`[CODE VERIFIED]`, prior session's read of the window-guard/checkpoint logic). Use `etl_run_id`/`fetched_at`, never `date_modified` alone, as the signal that this row was re-observed |
| `persons` | 32,790 | `person_id` | ✓ | ✓ | ✓ | ✓ | `etl_run_id` | DOB populated on 0.24% of rows — unusable as an identity signal (not an incremental-read limitation, but blocks a different ETL-3 responsibility — identity resolution) |
| `arrests` | 32,862 | `id` | ✓ | ✓ | ✓ | ✓ | `etl_run_id` | `(crime_id, accused_seq_no)` unique index now enforced at source — 0 duplicates confirmed live this session |
| `chargesheets` | 7,061 | `id` | ✓ | ✓ | ✓ | ✓ | `etl_run_id` | None found |
| `charge_sheet_updates` | 6,163 | `id` | ✓ | ✓ | ✓ | ✓ | `etl_run_id` | Source API (`/update-chargesheets`) exposes `dateCreated` but not `dateModified` (`[CODE VERIFIED]`, prior session) — V2's own `date_modified` column on this table is therefore ETL-assigned, not source-assigned; treat as lower-trust than `crimes`/`accused`'s `date_modified` |
| `disposal` | 470 | `id` | ✓ | ✓ | ✓ | ✓ | `etl_run_id` | None found |
| `mo_seizures` | 3,534 | `mo_seizure_id` | ✓ | ✓ | ✓ | ✓ | `etl_run_id` | None found |
| `properties` | 7,646 | `property_id` | ✓ | ✓ | ✓ | ✓ | `etl_run_id` | None found |
| `fsl_case_property` | 2,003 (etl_bookkeeping shows 2,887 fk_retry attempts against it, 894 still unresolved) | `case_property_id` | ✓ | ✓ | ✓ | ✓ | `etl_run_id` | Largest FK-retry backlog of any V2 table — rows referencing a `crime_id`/`accused_id` not yet present get retried up to 5 times then permanently capped, not deleted |
| `interrogation_reports` | 19,497 | `interrogation_report_id` | ✓ | ✓ | ✓ | ✓ | `etl_run_id` | 11 rows reference a `person_id` not present in `persons` (unenforced FK) |
| `hierarchy` | 816 | `ps_code` | ✓ | ✓ | ✓ | ✓ | `etl_run_id` | Reference/master data, not transactional — low change rate expected |
| `file_media_bookkeeping` | 165,864 | `id` | ✓ | ✓ | ✓ | **no** `date_created`/`date_modified` | `etl_run_id`/`fetched_at` only | The one table without business timestamps at all — must rely purely on `etl_run_id` |
| `etl_bookkeeping` | 4,850 | `id` | — | — | — | — | n/a — this IS the control table, not a business table | ETL-3 reads this read-only; never writes to it |
| `etl_run_state` | (small) | `module_name` | — | — | — | — | n/a — control table | Same |
| `geo_reference`, `geo_countries` | — | `id` | — | — | — | — | n/a | V2-internal enrichment reference data, out of scope per the original schema design (unchanged) |

**Conclusion for V2:** every business table carries `etl_run_id` + `fetched_at` + `source_system` + `source_endpoint` already `[DATABASE VERIFIED]`. This fully resolves the prior document's open question (R10) — ETL-3's source-observation layer for V2 can be built uniformly across all 14 tables using `etl_run_id` as the primary incremental signal, with no per-table exceptions needed except treating `file_media_bookkeeping` and `charge_sheet_updates`'s `date_modified` as lower-trust (per-row notes above).

---

## V1 (`cctns_v1` database, schema `cctns`)

| V1 table | Rows | PK | Per-row `created_at`/`updated_at` | `natural_key` | Per-row run linkage | Usable incremental signal | Limitations |
|---|---|---|---|---|---|---|---|
| `cctns_fir` | 7,305 | `fir_reg_num` | ✓ | — (FIRs aren't natural-key versioned) | via `cctns_v1_etl_row_action` join on `record_key` | `cctns_v1_etl_row_action` filtered to `table_name='cctns_fir'` since last processed `run_id` | No natural_key column, so no churn risk for this table specifically |
| `cctns_accused` (dossier) | 34,384 | `accused_id` | ✓ | ✓ | via `cctns_v1_etl_row_action` | Same mechanism | `accused_id` itself is **not stable** across time for the same real (crime, person) — confirmed this session: 3 of 20,128 `person_code`s in the sibling `accused_details` table have >1 distinct `accused_id`, one has 69. A new MD5 `natural_key` on any field edit produces a brand-new row+ID rather than updating in place |
| `cctns_accused_details` | 20,198 | `accused_id` | ✓ | ✓ | via `cctns_v1_etl_row_action` | Same mechanism | Same churn pattern as above, independently confirmed on this table (the 69-row outlier was found here) |
| `cctns_court` | 7,531 | `court_id` | ✓ | ✓ | via `cctns_v1_etl_row_action` | Same mechanism | Same natural-key-churn pattern, not separately quantified this session but structurally identical |
| `cctns_v1_etl_row_action` | 69,539 | `id` | `action_at` | — | **is** the run-linkage table | `(run_id, table_name, record_key, action, action_at)` — this is the mechanism | Columns confirmed this session: `id, run_id (uuid), entity, table_name, record_key, action, action_at` `[DATABASE VERIFIED]` |
| `cctns_v1_etl_run_log` | 56 | `id` | — | — | is the run table | `id` (monotonic), `status`, `started_at`/`finished_at` | ETL-3's V1 cursor should be the highest `id` whose `status` is a success state (`loaded`/`loaded_with_known_gaps`), not `extract_failed`/`extract_partial_failed` |
| `cctns_v1_failed_fetch_window` | 180, all `OPEN` | `id` | `first_seen_at`/`last_seen_at` | — | `run_id` column present | n/a — this is the gap ledger, not a change feed | ETL-3 reads this to populate `source_gap_ledger`, never to drive the main incremental cursor |
| `cctns_v1_audit_log` | 336 | `id` | — (has its own `changed_at`-style columns, not separately re-verified this pass) | — | n/a | Supplementary — field-level diffs for non-key-field changes | Not a primary incremental signal; useful for history reconstruction only |

**Conclusion for V1:** there is no per-row `run_id` column on the business tables themselves — the only reliable way to determine "what changed since my last processed run" is the join `cctns_v1_etl_row_action.record_key → <business table PK>`, filtered by `run_id > last_processed_run_id` (via `cctns_v1_etl_run_log.id`, restricted to successful-status runs). This is a **two-table join cursor**, not a single-column watermark — materially different from V2's per-row `etl_run_id`, and must be designed into ETL-3 explicitly rather than assumed symmetric with V2.

---

## Cross-cutting notes

- Both databases live on the same Postgres cluster (identical `pg_roles` listing returned from both connections this session, host `192.168.103.106`) `[DATABASE VERIFIED]` — different `dbname`s, same server. This matters for §13 (security boundary) in the implementation plan: role separation must be enforced with per-database `GRANT`, not assumed from network segregation.
- An existing, non-trivial set of application-facing roles already exists on this cluster: `ndps_admin`/`ndps_analyst`/`ndps_officer`/`ndps_readonly`, `dopamas_admin`/`dopamas_chat_ur`/`dopamasprd_ur`/`dopamasreadonly`/`dopamasdev_ur`/etc., `readonly_user`/`readonly_userdev` `[DATABASE VERIFIED]`. None of these were created or modified this session. Their existence is strong evidence a DOPAMS/NDPS application ecosystem operates against this cluster today, even though no application code is present in this repository (`MERGER_REVALIDATION.md` §20) — this is a refinement of that finding, not a reversal: the app is real, just not in this codebase.
