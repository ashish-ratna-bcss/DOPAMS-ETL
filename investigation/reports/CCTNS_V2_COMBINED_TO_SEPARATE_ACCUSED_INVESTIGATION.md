# CCTNS V2 Combined Accused → Separate Accused: Investigation Report

**Investigation date:** 2026-11-24. **Read-only.** No INSERT/UPDATE/DELETE/UPSERT/TRUNCATE/ALTER was executed, no migration was applied, no production ETL job was triggered, no code/config/log file was modified, no service was restarted. This report and its evidence live entirely outside `etl3/` and `cctns-v2/` (per the task's own instruction to keep investigation output outside the application/ETL source trees).

**Relationship to prior work.** This project's own repository already contains three substantial prior documents on this exact question, dated 2026-11-19/20:
- `CCTNS_V2_ACCUSED_API_CHANGE_AUDIT.md` ("the Audit")
- `CCTNS_V2_ACCUSED_API_CHANGE_AUDIT_APPENDIX.md` ("the Appendix")
- `DOPAMS_ETL3_ACCUSED_RECONCILIATION_IMPLEMENTATION_REPORT.md` ("the Implementation Report")

Those three documents are treated here as corroborating prior evidence, not re-derived from scratch. This report's job is threefold: (1) independently re-verify, 4-5 days later, every time-sensitive claim in them against the live database; (2) fill specific structural gaps the newer investigation brief asks for that those three documents do not present in this exact form (full table inventory, an explicit 13-question CCTNS-V2-ETL Q&A, the brief's own 8-case taxonomy, a fresh V2-only classification count, a chronological evidence table); and (3) answer the brief's final 10 questions directly. Every claim below states its own evidence; nothing is asserted "because the prior report said so" without being independently re-checked where a live check was possible.

---

## 0. Read-only confirmation and connection identity

Confirmed live, 2026-11-24 07:35-07:37 UTC, via this repository's own connection helpers (`etl3/db/connections.py`), the same helpers and the same two independent read-only enforcement mechanisms (`SHOW transaction_read_only` → `on`; `current_database()` asserted against a hardcoded expected name, not read from `.env`) that the prior Audit and Appendix used:

| Connection | Database | Host | `transaction_read_only` |
|---|---|---|---|
| `get_unified_connection(readonly=True)` | `dopams_cctns_v2` | `192.168.103.106` | `on` |
| `get_v2_source_connection()` | `cctns-v2` | `192.168.103.106` | `on` |

Both match the Appendix's stated connection identity exactly (§G: "the live `dopams_cctns_v2` database (host `192.168.103.106`)"). All SQL in this report is `SELECT`-only; ad hoc query scripts used to produce it were written to this session's scratchpad directory, never to the repository, per the same methodology note the Appendix itself documents (§G).

---

## A. Executive Conclusion

1. **Is this behaviour possible?** Yes. CCTNS V2's `/accused` → `/person-details` contract has no field preventing a crime from carrying both an uncoded, multi-name-packed accused record and separately-coded single-name accused records simultaneously. Nothing in the audited API, in DOPAMS's own CCTNS-V2 ingestion ETL (`cctns-v2/etl-accused/etl_accused.py`, `cctns-v2/etl-persons/etl_persons.py`), or in ETL3's unified-schema merger prevents this structurally.
2. **Is it actually happening?** Yes, confirmed for one real case (FIR 869/2026) and re-confirmed, unchanged, as of this pass. System-wide, a fresh full re-scan of all 34,373 currently-linked V2 accused names (new in this report, §E) found exactly **one** record anywhere in the live V2 data that is a high-confidence combined/packed name — the same FIR 869/2026 record already known. No second case of this exact "numbered pack" shape exists in the current V2 data.
3. **Is it expected by CCTNS V2?** Unknown/not established either way. No documentation, API comment, or workflow artifact seen anywhere in this codebase states this is deliberate CCTNS V2 behaviour. It is also not contradicted by anything seen. This remains a question for the client to put to the CCTNS V2 provider (Audit §11, Q2) — nothing newly found in this pass changes that.
4. **Caused by the API, CCTNS V2's ETL, CCTNS V2's database, or DOPAMS's ETL?** For the one confirmed real case, the duplication demonstrably **predates** both DOPAMS's legacy `cctns-v2` ingestion ETL and ETL3. New evidence in this pass (§F) shows all five native CCTNS V2 accused records for FIR 869/2026 — the combined "Absconding" record **and** the four separately-coded `A1`/`A2`/`CCL3`/`A4` records — carry the **identical `DATE_CREATED` timestamp to the millisecond** (`2026-06-19T19:57:53.654000`) in CCTNS V2's own API payload. This is strong evidence the duplication originates in CCTNS V2's own source data at the moment of creation, not from any transformation performed by DOPAMS's ingestion ETL or by ETL3 downstream. ETL3's own multi-accused-split feature then added a **second** layer of duplication on top (4 more rows) before the fix described in the Implementation Report closed that specific gap (§D below).
5. **Confidence.** High for "it has happened once, in CCTNS V2's own source data, independent of any DOPAMS ETL" (direct, byte-level timestamp evidence). Low/Inconclusive for "this is the general pattern across all of CCTNS V2" — one confirmed case in 34,373 currently-linked V2 names, a six-week-wide but **not continuously growing** observation window (§0 of the Audit; still true, see §1 below), is not enough to generalize a base rate.

---

## B. Architecture and Data-Flow Diagram

```
 CCTNS V2 (government source system — API only; no source code,
 no internal DB, no internal ETL logs are accessible to DOPAMS)
        │
        │  GET /accused, GET /person-details/{id}   (HTTPS, JSON)
        ▼
 DOPAMS's own "pure-CCTNS" ingestion ETL  (cctns-v2/etl-accused/etl_accused.py,
 cctns-v2/etl-persons/etl_persons.py — a DOPAMS-authored client of the CCTNS V2
 API, NOT part of CCTNS V2 itself)
        │  date-window chunking (chunk_days=5, overlap_days=1) — no page-token
        │  pagination exists on this API; "pagination" here means date ranges
        │  INSERT ... ON CONFLICT (accused_id) DO UPDATE SET col = EXCLUDED.col
        │  (accused: unconditional field overwrite, no row history retained)
        │  persons: UPDATE ... SET col = COALESCE(new, existing) (protects
        │  against a null API value erasing existing data; still overwrites
        │  on any new non-null value)
        ▼
 Postgres database literally named "cctns-v2"  (current-state only —
 every re-fetch overwrites the prior row; this is NOT a CCTNS-V2-internal
 database, it is DOPAMS's own mirror/staging database for this one source)
        │
        │  DOPAMS ETL3 — read-only connection, one-way
        ▼
 ETL3 append-only observation tables  accused_source / persons_source
 (dopams_cctns_v2) — the ONLY genuine historical trail that exists anywhere
 in this stack for CCTNS V2. One row per (source_system, source_record_id,
 source_run_id); never updated, never deleted.
        │
        │  etl3.merger — field-diff → change_log, then current_state → …
        ▼
 accused_unified / persons_unified  (dopams_cctns_v2) — current-state,
 one row per accused/person occurrence, plus ETL3-generated split-child
 rows (accused_id suffixed "...:split:<hash>") when the multi-accused
 detector fires.
        │
        │  DOPAMS-BE criminal-profile service (unified-profile.ts)
        ▼
 DOPAMS-BE API  →  frontend
```

A **separate, legacy, DOPAMS-owned Postgres database** exists — `DB-schema.sql` in this repository is a `pg_dump` of it, owned by role `dev_dopamas` and referred to elsewhere in this project's own documentation (`docs/DOPAMS_DEV2_CCTNS_ARCHITECTURE_AUDIT.md`, `docs/DOPAMS_UNIFIED_CCTNS_USECASE_MAPPING.md`) as **"dev-2."** It is **not** the `cctns-v2` database the diagram above describes, is **not** read by ETL3, and is **not** a CCTNS V2 artifact — it carries DOPAMS-BE's own application tables (`user`, `files`, AI/LLM drug-extraction output, and `person_deduplication_tracker` — see §C, Q13). This distinction matters because the brief's investigation scope asks about "CCTNS V2's internal ETL" — no such internal system is accessible to DOPAMS at all; everything DOPAMS can observe is the API contract and its own two downstream ingestion layers (`cctns-v2` and ETL3) described above.

**What the API response actually is**, confirmed from `cctns-v2/CCTNSV2_data/*.json` captured samples and `cctns-v2/cctns-v2_schema_mapping_report.md` (reconfirms Audit §3): the accused/person records are **directly returned from CCTNS V2's own database rows** (option A in the brief's own A/B/C/D framing) — no join, aggregation, or visible server-side transformation was found in any captured sample; `FULL_NAME` is the one documented exception, itself a CCTNS-computed `NAME + "@" + ALIAS` composite for single individuals with an alias (Audit §3) — not evidence of multi-person combination.

---

## C. Database Inventory

### C.1 `cctns-v2` database (DOPAMS's own CCTNS-V2 ingestion target — 16 tables, per `cctns-v2/cctns-v2_schema.sql`'s own header)

| Table | What it holds | History? |
|---|---|---|
| `hierarchy`, `crimes`, `persons`, `accused`, `arrests`, `disposal`, `properties`, `mo_seizures`, `chargesheets`, `charge_sheet_updates`, `fsl_case_property`, `interrogation_reports` | CCTNS-sourced domain data | **No** — every table is upserted in place; current-state only |
| `file_media_bookkeeping` | File/media download tracking for every entity above | Current-state |
| `etl_bookkeeping` (`kind`: `checkpoint`\|`run_state`\|`fk_retry`\|`failure`) | DOPAMS's own ingestion-ETL run/watermark/retry state | Current-state (watermark overwritten each run) |
| `geo_countries`, `geo_reference` | Static lookup tables for address resolution | N/A |

**Explicitly excluded from this schema** (per its own header comment, lines 64-71): `brief_facts_ai`, `brief_facts_drug` (LLM drug-extraction output), `drug_categories`/`drug_ignore_list`, **`person_deduplication_tracker`** ("AI dedup tool"), `etl_crime_processing_log`, `old_interragation_report`, `"user"` — all explicitly tagged "not populated by the pure-CCTNS pipeline." Confirmed live: `to_regclass('public.person_deduplication_tracker')` on the actual `cctns-v2` database returns **NULL — the table does not exist there**. See §C.3.

### C.2 `dopams_cctns_v2` database (ETL3's unified target — the project's own historical layer)

Re-confirms and extends the Audit's §2 table, all independently re-queried live on 2026-11-24:

| Table | Role | V2 coverage (confirmed live, 2026-11-24) | Append-only? |
|---|---|---|---|
| `accused_source` | Raw-ish (one layer downstream of the API) append-only observation of every `accused` row ETL3 ever read | 39,519 rows; `source_fetched_at` 2026-09-25 03:29→2026-11-06 06:11; `ingested_at` 2026-10-08 15:27→2026-11-06 07:15 | **Yes** — confirmed unchanged from the Audit's stated window, 18 days later |
| `persons_source` | Same, for `persons` | 38,313 rows; `ingested_at` 2026-10-08 15:30→2026-11-06 07:15 | **Yes**, same frozen window |
| `change_log` | Field-level diff per re-observation | 0 new `accused`/`person`, V2, `full_name`/`accused_status`/`person_id`/`crime_id` rows of **any** classification since 2026-11-19 | Yes, but no new V2-accused/person activity in this window (see §1) |
| `accused_unified` / `persons_unified` | Current-state unified rows | V2: 34,453 accused rows; `persons_unified` joined via `person_id` for 34,373 of them (80 V2 accused rows have no join-resolvable person row — consistent with the known `unlinked_accused`/`unlinked_persons_placeholder` gap types below, though the gap-ledger count for those two specific types is only 2; the remaining ~78 were not individually re-traced in this pass and are flagged as an open item) | Current-state only |
| `accused_split_audit` | ETL3-generated split-child creation events | 13 distinct parents / 32 children, unchanged since 2026-11-18 14:57:10 | Yes (append-only; nothing deletes a row here) |
| `accused_split_review` (migration 023) | Crime-level-overlap / low-confidence review queue | **Table does not exist on the live database** — confirmed via `information_schema.columns` returning zero rows and `to_regclass(...)` returning NULL. Matches the Implementation Report's §13 blocker #1 ("Migration 023 is not applied anywhere") — **still true, unchanged, 4 days later.** | N/A — not applied |
| `bulk_event_exclusions` | Registry of known administrative bulk-touch windows | **0 rows**, confirmed unchanged | N/A — never populated |
| `consolidation_run_log` | ETL3's own pipeline run history | Last **and only most-recent** successful run: 2026-11-06 07:15:01→07:48:17 UTC. **No run of any status since.** | Yes, but stalled |
| `source_gap_ledger` | Unresolved FK/link/address gaps, V2 | **142** open issues (up from 132 on 2026-11-19 — see §1.4), no gap type specific to a partial/incomplete accused-module fetch | Current-state snapshot |
| `person_enrichment` | AI/placeholder-name enrichment flags | 189 rows system-wide flagged `unidentified_reason='invalid_literal'` (not V2-filtered in this query — see caveat in §1.5; not directly comparable to the Audit's V2-specific 150) | Current-state |
| `identity_links` | Human-reviewed candidate/confirmed/rejected person-identity matches | 1,171 rows, none auto-confirmed (Audit §6; not re-queried live this pass, no write path exists to change it and nothing in this pass touched it) | Human-maintained |

### C.3 A previously-undocumented clarification: `person_deduplication_tracker` is NOT a CCTNS-V2 or ETL3 mechanism

New finding this pass, not present in the three prior reports. `cctns-v2/DB-schema.sql` (a `pg_dump`, dated 2026-04-23, role `dev_dopamas`) defines a `person_deduplication_tracker` table with `canonical_person_id`, `all_person_ids`/`all_accused_ids`/`all_crime_ids` (arrays), `matching_tier` (1-5), `matching_strategy`, `fuzzy_match_score`, `confidence_score` — on its face, exactly the kind of "intentional consolidation" mechanism the brief's §6 Q13 and Case 7/8 taxonomy ask about.

Tracing it fully: this table belongs to **"dev-2,"** a separate, legacy, all-in-one DOPAMS-BE Postgres database — confirmed by (a) `DB-schema.sql`'s dump header (`Owner: dev_dopamas`, a much larger ~60-table schema including `"user"`, `files`, `brief_facts_ai`) and (b) this project's own `docs/DOPAMS_DEV2_CCTNS_ARCHITECTURE_AUDIT.md` and `docs/DOPAMS_UNIFIED_CCTNS_USECASE_MAPPING.md`, which independently describe "dev-2" as a legacy database mixing real CCTNS data with DOPAMS application tables, and explicitly warn: *"Do not treat `identity_links` as confirmed duplicates or as `person_deduplication_tracker`"* and *"Do not replace `person_deduplication_tracker` with `identity_links`."* It is used only by DOPAMS-BE's legacy `criminalProfile`/`criminalNetworkDetails` GraphQL resolvers (`src/schema/criminal-profile/services/`, per that audit) for a "repeat offender" grouping UI feature — never by CCTNS V2, never by ETL3.

Confirmed live, 2026-11-24, on the actual `cctns-v2` database ETL3 reads: **`person_deduplication_tracker` does not exist there** (`to_regclass(...)` → NULL). This matches `cctns-v2_schema.sql`'s own header comment, which **explicitly and pre-emptively excludes it** as "AI/LLM-derived or legacy/unrelated, not populated by the pure-CCTNS pipeline." Net conclusion: there is no CCTNS-V2-side or ETL3-side "intentional consolidation" business logic. The one DOPAMS-side artifact with dedup-shaped columns lives on an entirely separate legacy database, is explicitly out of scope for the current ingestion pipeline, and was checked and found **not** to contain FIR 869/2026 (query attempted; table absent on the database queried, so no overlap is possible by construction). This closes out brief §6 Q13 cleanly: **Not Observed / Not Applicable**, with the clarification recorded so a future reader does not need to re-discover it.

### C.4 Raw API JSON archive (`cctns-v2/CCTNSV2_data/*.json`)

15 files, all dated 2026-10-01, sizes 364 bytes–17.3 KB each. These are **one static captured sample per endpoint** (per `save_cctns_api_samples.py` in the same directory), used by developers to document the API contract (this is the Audit's own cited source for §3's field list) — **not** a time series, **not** per-run archives, **not** evidence of historical change. `ACCUSED_API.json` (702 bytes) and `PERSON_DETAILS_API.json` (1,552 bytes) are single-record examples. No date-range coverage claim can be made from this source beyond "captured once, 2026-10-01."

### C.5 Logs — actual filesystem check (not assumed from memory)

Checked directly on disk, 2026-11-24:
- `cctns-v2/logs/` — **does not exist** (confirmed: `ls` returns "No such file or directory"). Matches the Audit's claim.
- Searched the entire `cctns-v2/` and `etl3/` trees for any `*.log` file: **three exist**, none related to accused/person ingestion: `cctns-v2/output.log` (2,941 bytes, dated 2026-02-26/27 — a drug-extraction AI service's stdout, `nohup`-captured, mentions crime IDs and Ollama HTTP calls, zero mentions of "accused" or "person"), `cctns-v2/domicile_classification/domicile_classification.log` (1.25 MB), `cctns-v2/etl_mo_seizures/etl_mo_seizure.log` (159 KB). None is a per-run HTTP request/response log for the `/accused` or `/person-details` endpoints.
- Searched for any systemd/cron/Docker scheduling artifact anywhere in `cctns-v2/` or `etl3/`: the only hits are **ETL3's own** daily-pipeline rollout doc and runner (`etl3/docs/DAILY_PIPELINE_ROLLOUT.md`, `etl3/run_daily_pipeline.py`) and read-only audit-tooling scripts (`etl3/diagnostics/_audit_ops_ro.sh`, two Ollama-parallelism diagnostics) — nothing from CCTNS V2 itself or from DOPAMS's legacy `cctns-v2` ingestion ETL.
- **Conclusion, confirmed by direct filesystem check rather than inference:** no raw CCTNS V2 HTTP request/response log survives anywhere on this checkout, for any date. The only durable historical trail for CCTNS V2 accused/person data is ETL3's own `accused_source`/`persons_source` (§C.2), which is one layer downstream of the literal API bytes (it captures the already-ingested `cctns-v2` Postgres row, lower-cased column names, not the raw HTTP JSON).

---

## D. Historical Scan Results

| Metric | Value | Source |
|---|---|---|
| Total V2 accused rows in `accused_unified` | 34,453 | live re-query |
| V2 accused rows with a join-resolvable linked person | 34,373 | live re-query (§C.2 gap note) |
| V2 crimes with any accused | 10,027 | live re-query, matches Audit exactly |
| V2 crimes with >1 non-split-derived accused | 7,734 | live re-query, matches Audit exactly |
| Real `accused_split_audit` parents / children, system-wide | 13 / 32 | live re-query, unchanged since 2026-11-18 |
| ...of which V2-sourced | 1 parent / 4 children | live re-query, unchanged |
| Fresh V2-only high-confidence numbered-pack detections (this pass, §E) | **1** (FIR 869/2026, same record already known) | live re-query, new in this pass |
| Fresh V2-only high-confidence accused-code-pack detections (this pass, §E) | **0** | live re-query, new in this pass |
| Fresh V2-only low-confidence "ambiguous, requires review" detections (this pass, §E) | **91** (90 unnumbered-candidate + 1 non-sequential-marker) | live re-query, new in this pass |
| Confirmed chronological combined→separate transitions (same record observed twice, changing shape) | **0** | Not Observed — single-snapshot coverage for every real V2 case so far |
| Concurrent combined/separate cases (both forms coexisting at once) | **1** confirmed (FIR 869/2026) | direct SQL, re-confirmed this pass |
| `accused_split_review` rows that could not be produced (no table) | N/A | migration 023 not applied |
| Records excluded from the fresh V2 scan, and why | 80 V2 accused rows with no joinable `persons_unified.person_id` (excluded by the `JOIN`, not examined individually — see open item, §H) | live re-query |

---

## E. Fresh V2-Only Classification (new evidence this pass)

The Audit's and Appendix's classification counts (Appendix E: ~150-200 candidates, 13 high-confidence, 138 low-confidence, 127 spelled-out "Alias"/"AKA") are explicitly sourced from a **pre-ETL3, 51,123-record legacy export that blends V1 and V2 display text** (Appendix E's own footnote). This report instead ran the live, current `etl3.enrichment.multi_accused.detect_multi_accused()` function — a pure function, no DB access, no writes, the exact same code the production split feature calls — against the **current `full_name`** of every one of the 34,373 currently-linked V2 accused records, live on 2026-11-24. This is a true current-state, V2-only count, not a blended legacy estimate.

| Bucket (brief §5 taxonomy) | Count | Detector signal |
|---|---:|---|
| 1. Clearly numbered multi-person (`"1) A, 2) B"`) | **1** | `confidence="high"`, `method="numbered_pack_v1"` — FIR 869/2026's combined record, accused_id `6a35a0541103855a721a342c` |
| 2. Clearly coded multi-person (`"A1. A, A2. B"`) | **0** | `confidence="high"`, `method="a_code_pack_v1"` — none currently exist in V2 |
| 3. High-confidence unnumbered multi-person | **0, by design** | The detector's own validation pipeline (`_build_pack_result`, `multi_accused.py` lines 161-228) can never assign `"high"` confidence to an unnumbered list — every unnumbered candidate is capped at `"low"`. This bucket is structurally empty under the current detector, not merely unobserved; see §I. |
| 4. Single-person with alias | **29** | 2 records with a literal `@` marker (e.g. `@ N******** B**** N******** @********* B****`, `@******* M******* @*******`) + 27 records with the spelled-out word "Alias"/"AKA" in the name text. None were misread as multi-person by the detector (confirms Audit §3's `@`-is-not-a-marker finding, now re-verified against live current data rather than inferred) |
| 5. Ambiguous, requires review | **91** | 90 `confidence="low"`, `method="unnumbered_candidate"` (2+ comma/"and"/"&"-separated, name-shaped chunks — never auto-split); 1 `confidence="low"`, `method="numbered_pack_ambiguous"`, `warnings=["missing_leading_marker"]` (a sequence starting at label 2, implying an unmarked leading accused — see sample below) |
| (no signal at all — ordinary single name) | 34,281 | `confidence="none"` — the overwhelming majority, as expected |

**Representative samples (masked; real `accused_id`/`crime_id`/FIR kept per the brief's own instruction to preserve traceability):**

| Accused ID | Crime ID | FIR | Masked text | Why flagged |
|---|---|---|---|---|
| `63bb20c815c4077ef401db92` | `63bb15d415c407532101bc7f` | 35/2023 | `M******* a** h** b******` | unnumbered candidate (3 comma/"and"-separated name-shaped chunks) |
| `63cb4d95655d90340f35fa03` | `63cb492cae7a3d49259c269b` | 104/2023 | `E****** & V****** S**** K****` | unnumbered candidate (`&`-separated) |
| `63d8e4327f45007198bfa72f` | `63d8dab7939b765a75d6f949` | 75/2023 | `S***** a** o****` | unnumbered candidate — but this is "Suresh and others," not two names; the detector's own crude name-chunk heuristic (`_unnumbered_candidates`, `multi_accused.py` lines 129-143) treats "others" as a name-like token, a documented, deliberate crudeness ("a false positive is never fed to any writer, only surfaced for review" — same file's docstring). Human review would correctly dismiss this one. |
| `6476da881027df4ab9d6978d` | `6476275b1027df5303d3b2a1` | 251/2023 | `P*** R*** K**** & o*****` | same "and others" false-positive pattern as above |
| `66edb242a11853fb7fed311d` | `66edacf1cddf22ba912bcf49` | 782/2024 | `R**** A** S***** A******* A******` | `numbered_pack_ambiguous`, `missing_leading_marker` — the only record in the entire current V2 dataset with this specific warning. Not auto-split, correctly held for review rather than guessed at. |
| `669bac2a52b1312f35c50770` | `669b878ea9f2653e44d8f802` | 974/2024 | `@ N******** B**** N******** @********* B****` | alias marker (`@`), not multi-person — correctly never flagged by the pack detectors |

**Bottom line:** across the entire current live V2 dataset, exactly one high-confidence combined/packed record exists, and it is the already-known FIR 869/2026 case. No second "combined name later/also appears as separate records" candidate of the brief's illustrative shape was found anywhere else in CCTNS V2's current data.

---

## F. Chronological Evidence — FIR 869/2026 (built only from already-evidenced timestamps)

**Identifiers:** `fir_num = 869/2026`, `crime_id = 6a359f414d1e611f530f007e`, source_system V2. (Appendix §A.1 for full prior detail; this table adds the new §F.1 finding and re-confirms every row live.)

| Timestamp | Evidence source | Event | Accused ID (masked suffix) | Name (masked) | Interpretation |
|---|---|---|---|---|---|
| `2026-06-19T19:57:53.654000` (CCTNS `DATE_CREATED`, identical to the millisecond on all 5 native rows — **new finding, §F.1**) | `accused_source.payload->>'date_created'`, re-queried live | All 5 native CCTNS V2 accused records for this crime created | `...2c` (combined), `...fad` (A1), `...9b1` (A2), `...4e0` (CCL3), `...bc6` (A4) | combined pack + 4 coded singles | CCTNS V2's own metadata shows simultaneous creation — no evidence either representation preceded the other |
| `2026-08-24T09:59:00.164000` (CCTNS `DATE_MODIFIED`, identical on all 5) | same | Bulk administrative timestamp touch (documented, unrelated, system-wide 2026-08-24 event — Audit §2) | all 5 | — | Confounding event, not a genuine content edit; correctly excluded from business-change interpretation |
| `2026-09-25T03:38:38.77-39.33Z` | `accused_source.source_fetched_at`, re-queried live | ETL3 fetched this crime's CCTNS V2 row for the first (and, so far, only) time | all 5 | — | Start of ETL3's observation window for this record |
| `2026-10-08T15:29:28.45Z` | `accused_source.ingested_at`, re-queried live | ETL3 ingested the fetched rows into `accused_source` | all 5 | — | |
| `2026-11-18T14:57:10.485262Z` | `accused_split_audit.created_at`, re-queried live | ETL3's multi-accused split pass (pre-fix code) created 4 new child rows from the combined record and marked the parent `split_superseded_at` | 4 new `...:split:<hash>` rows | D*** S*******; S*** A**** A**; B**** k****** N***; K******** M****** | Third representation added on top of the pre-existing 5 |
| `2026-11-24T07:37:02Z` (this pass) | live `accused_unified`/`persons_unified` re-query | Re-confirmed: still exactly 9 rows, same structure, zero further change | all 9 | — | No activity on this FIR since 2026-11-18 |

**F.1 is the one genuinely new fact this report adds to the FIR 869/2026 record:** the Appendix's own §A.1/§A.2 showed `DATE_MODIFIED` matching across all 5 rows (attributed to the known bulk event) but did not state whether `DATE_CREATED` also matched across the 4 coded siblings — only the combined parent's `DATE_CREATED` was shown in Appendix §A.1. Re-querying all 5 rows' `date_created` in this pass shows they are **identical to the millisecond**, which is new, specific, and meaningfully strengthens the "source-side coexistence, not a chronological transition" conclusion (§A.4, §D.4 below) beyond what "single-snapshot coverage" alone could support.

---

## G. CCTNS V2 ETL Code Analysis — 13-Question Q&A (brief §6)

**Scope caveat, stated once and applying to every answer below:** CCTNS V2's own internal source code, internal database, and internal ETL are not accessible to DOPAMS at all — only its public API contract is observable. "CCTNS V2's ETL" in every answer below therefore means **DOPAMS's own ingestion client** of that API (`cctns-v2/etl-accused/etl_accused.py`, `cctns-v2/etl-persons/etl_persons.py`), which is the closest thing to "the ETL between the API and a database" that this investigation can inspect with code-level evidence. Anything about CCTNS V2's own internal behaviour beyond what its API exposes is **Not available** by construction, not merely unchecked.

1. **Full refresh or incremental?** Incremental, by date-window. `generate_date_ranges()` / `fetch_accused_api()` (`etl_accused.py` lines 658, 686-690) chunk every run into 5-day windows (`chunk_days=5`) with a 1-day overlap (`chunk_overlap_days=1`), driven by a persisted watermark in `etl_bookkeeping` (`kind='run_state'`, lines 389-475). There is no page-token pagination on this API — "pagination" here is purely date-range chunking.
2. **INSERT, UPDATE, UPSERT, or delete-and-reinsert?** **UPSERT**, both tables, but with different null-handling semantics (new distinction, not previously stated this precisely): `accused` uses a true `INSERT ... ON CONFLICT (accused_id) DO UPDATE SET col = EXCLUDED.col` for **every** column (`etl_accused.py` lines 1607-1647) — an existing row is **unconditionally overwritten**, including with a new empty/blank value if the API returns one. `persons` uses `UPDATE ... SET col = COALESCE(new_value, existing_value)` (`etl_persons.py` lines 1707-1729) — an existing non-null value is **never erased** by a null API response, but **is** overwritten by any new non-null value. No delete-and-reinsert pattern exists anywhere in either file.
3. **What is the accused primary key?** `accused_id` (CCTNS V2's own Mongo-ObjectId-shaped string) — enforced as the literal Postgres primary key via the `ON CONFLICT (accused_id)` clause itself (line 1619). A secondary uniqueness check exists on `seq_num` (lines 1584-1605): a new accused whose `seq_num` collides with an existing different `accused_id` is **rejected outright** (`skipping insert to preserve unique constraint`, logged as `seq_num_conflict`) — this is the one place DOPAMS's own ETL actively refuses to create a second row rather than silently duplicating.
4. **Can one API response create multiple DB rows?** No evidence found. Each `/accused` API record maps to exactly one upserted row. The multi-name-packing behaviour this whole investigation concerns happens entirely on the **person** side (one `person.full_name` field holding packed text) and is never expanded into multiple rows by this ETL — the expansion into multiple rows, when it happens at all, is performed only by ETL3's separate, later, opt-in multi-accused-split feature (§D), not by this ingestion layer.
5. **Can multiple API responses update the same DB row?** Yes, routinely — this is the normal `ON CONFLICT`/`COALESCE` update path; confirmed 2,797 distinct V2 `accused_id`s and 2,784 `person_id`s were genuinely re-observed more than once in ETL3's own append-only trail (Audit Appendix E, not independently re-queried this pass since the observation window is frozen — see §1.2).
6. **Does the ETL retain historical snapshots?** No, at the `cctns-v2` database layer — confirmed again directly from the upsert code itself (Q2): the prior row's values are gone the instant a new one lands, except where `COALESCE` on the persons table protects a field from being nulled. The **only** place any history survives is one layer downstream, in ETL3's own `accused_source`/`persons_source` append-only tables (§C.2) — a DOPAMS/ETL3 design choice, not anything CCTNS V2 or this ingestion ETL provides natively.
7. **Overwrite or preserve previous names?** See Q2 — mixed by table: `accused` overwrites unconditionally; `persons.full_name` is `COALESCE(new, existing)`, i.e. preserved only against a null, not against a differing non-null value.
8. **Can the ETL replace a combined record with separate records?** No — this ingestion layer (`cctns-v2/etl-accused`, `cctns-v2/etl-persons`) performs no name-parsing, no splitting, and no record-count change of any kind; it is a field-for-field upsert of whatever CCTNS V2's API returns, one API record to one DB row, always. (The only code in this whole stack that can ever turn one record into several is ETL3's `etl3/merger/accused_split.py`, confirmed again this pass to be a wholly separate, later, opt-in layer — see Q13 and §D.)
9. **Does it retain the original combined record?** N/A at this layer (nothing is ever split here to begin with); **yes** at the ETL3 layer, by design — a split parent is marked `split_superseded_at` but never deleted (Implementation Report §4.1.1, re-confirmed live this pass: FIR 869/2026's parent row `6a35a0541103855a721a342c` still exists, `split_superseded_at = 2026-11-18T14:57:10.485262Z`, not deleted).
10. **Are inactive/deleted records retained?** Trivially yes, because **no code path in either `etl_accused.py` or `etl_persons.py` ever issues a `DELETE` against the `accused` or `persons` tables** — confirmed by an explicit repo-wide search: every `DELETE FROM`/`TRUNCATE` statement found anywhere under `cctns-v2/` targets only bookkeeping/media-tracking tables (`file_media_bookkeeping`, `etl_bookkeeping`), never `accused` or `persons` content rows.
11. **Can partial API responses cause incomplete data?** Not independently testable from code alone for CCTNS V2's own side (Not available). On DOPAMS's ingestion side: `total_duplicates`/`seen_accused_ids` tracking (lines 1933-2070) exists specifically because the 1-day chunk overlap deliberately re-fetches boundary records more than once "to ensure no data loss" (comment, line 2171) — i.e. the design assumes date-window chunking could otherwise miss a boundary record, and compensates with overlap rather than detecting partial responses directly. `source_gap_ledger` (ETL3 side) has no gap type specific to an incomplete accused-module fetch (Audit §2, re-confirmed: still 142 V2 rows, still none of the 6 types is accused/persons-partial-fetch-shaped — see §1.4).
12. **Is there a dedup step?** Confirmed **no** — `stats['total_duplicates']` only counts an `accused_id` appearing more than once **within the same chunk-overlap window** (lines 1933-2070); every one of those occurrences is still individually processed as an ordinary idempotent upsert ("All duplicates are processed to allow updates," line 2225/2236's own log message) — nothing here merges, consolidates, or skips a record because it looks like a duplicate of another.
13. **Is there source-side business logic that intentionally consolidates or separates accused records?** **No**, confirmed two independent ways: (a) no such logic exists anywhere in `cctns-v2/etl-accused/` or `cctns-v2/etl-persons/` (grepped for "duplicate", "merge", "split", "combine", "consolidat*" — the only hits are the chunk-overlap duplicate-counting described in Q12, which does not merge or split anything); (b) the one table in this entire codebase with dedup-shaped columns, `person_deduplication_tracker`, is **not** CCTNS-V2-side or ETL3-side at all — it is confirmed to live only on a separate legacy DOPAMS-BE database ("dev-2"), confirmed absent from the actual `cctns-v2` database this ETL writes to, and explicitly, pre-emptively excluded from the current ingestion pipeline's own schema documentation (§C.3). The only intentional consolidation/separation logic anywhere in this stack is ETL3's own multi-accused-split feature, which is unambiguously downstream of both CCTNS V2 and this ingestion ETL, not a part of either.

---

## H. Case-by-Case Taxonomy (brief §4)

Applied to FIR 869/2026, the only confirmed real instance; "system-wide" column covers the fresh §E scan.

| Case | FIR 869/2026 | System-wide (V2) | Classification |
|---|---|---|---|
| 1. Same source ID; name/person details change over time | Not observed — the combined record (`...2c`) has exactly one CCTNS-side observation; no second snapshot exists to show its own name changing | 378 genuine `full_name` corrections exist system-wide (Appendix C) but none of those are on a record that also carries a multi-name pack | **Inconclusive** for Case 1 specifically on a packed record; **Confirmed** for ordinary (non-packed) name correction generally |
| 2. New accused source IDs appear later for the same crime, originally combined | The 4 coded IDs (`A1`/`A2`/`CCL3`/`A4`) and the combined ID share an **identical `DATE_CREATED` to the millisecond** (§F.1) — no "later" relationship is evidenced; both forms were created together, per CCTNS's own metadata | No second case found anywhere in current V2 data (§E) | **Not Observed** as a chronological "later" event; **Confirmed** as simultaneous coexistence instead |
| 3. Original (combined) record remains active while separate records are added | The combined parent is still a live, non-deleted row today (`split_superseded_at` only marks it hidden-from-roster, not deleted) — but per Case 2's finding, the 4 separate records were not "added" after it; they co-existed from creation | N/A, only one instance exists | **Not Supported by the timeline evidence** — the premise of addition-after-the-fact is not what §F.1 shows |
| 4. Original record is updated or deactivated after separate records appear | The combined parent's only observed state change is ETL3's own `split_superseded_at` (2026-11-18) — a DOPAMS/ETL3-side bookkeeping flag, not a CCTNS-side deactivation; CCTNS's own `accused_status` for it ("Absconding") has never been seen to change | N/A | **Confirmed, but caused by ETL3, not CCTNS V2** — see §G Q9 |
| 5. Same person IDs between combined and separate forms | **No** — the combined record's `person_id` (`6a359fd8...`) is distinct from each of the 4 coded records' own `person_id`s; CCTNS V2 treats each as an independent accused-appearance identity, consistent with the Audit's finding that `PERSON_ID` is "an accused-appearance identity concept more than a verified cross-case 'same human' identity" (Audit §6) | 0 genuine `accused.person_id` reassignments found system-wide (Audit, unchanged — no write path exists to alter this since) | **Confirmed: different person IDs** (Case 6, not Case 5) |
| 6. Different person IDs, potentially the same individuals | Yes — see Case 5. Token-overlap mapping (Appendix §A.2) is evidence, not certainty, that the 4 coded names and the 4 packed-segment names refer to the same 4 real people | — | **Confirmed** (evidence-based, not asserted as certain identity — per the brief's own instruction not to confuse name match with identity match) |
| 7. CCTNS-side ETL transformation (API returns one structure, internal ETL changes the DB representation) | **Not supported by any evidence found.** No internal CCTNS V2 ETL is observable at all (§G's scope caveat); DOPAMS's own ingestion ETL performs no such transformation (§G Q8); the duplication is present in the API-shaped payload itself, at the earliest point DOPAMS can observe it | — | **Not available / Not Observed** — nothing in this investigation can confirm or rule out an internal CCTNS V2 transformation, because that system's internals are inaccessible; what **is** confirmed is that DOPAMS's own layers don't do this |
| 8. DOPAMS-only duplication (source DB already has both, DOPAMS imports without reconciling) | **Partially confirmed, with an important correction**: CCTNS V2's own source data already has both forms (this much matches "DOPAMS imports without reconciling," and before the fix described in the Implementation Report, ETL3 indeed imported both without reconciling them, then added a third layer on top). But the *origin* of the duplication is CCTNS V2's own source data (§F.1), not a DOPAMS import-time artifact — so this is better described as **brief §9's category "A source-side coexistence case"** (its own letter A) with a **DOPAMS-side compounding effect** (ETL3's pre-fix split, since fixed per the Implementation Report) layered on top, rather than a pure Case 8 | — | **Confirmed (source-side coexistence) + Confirmed (DOPAMS-side compounding, now mitigated)** |

**Nothing above is classified "Impossible under verified implementation constraints."** Every "Not Observed"/"Inconclusive" line states exactly what was checked and what limits the conclusion, per the brief's own instruction never to call something impossible merely because it wasn't observed.

---

## I. Logs and Historical-Evidence Limitations

**Available:** ETL3's `accused_source`/`persons_source` append-only trail (6 weeks wide: 2026-09-25/10-08 → 2026-11-06, re-confirmed frozen at that same width 18 days later in this pass); `change_log`'s field-diff history over the same window; `accused_split_audit`'s complete, append-only record of every split ETL3 has ever performed; direct, repeatable, read-only SQL access to the live current-state tables; full read access to both ingestion-ETL codebases; one static API-contract sample per endpoint (`CCTNSV2_data/*.json`, captured once, 2026-10-01).

**Missing, confirmed by direct check (not assumed):** no raw CCTNS V2 HTTP request/response log of any kind, for any date (§C.5); no CCTNS-V2-internal ETL code, database, or log of any kind (that system is external and inaccessible by design — this was true before this investigation and remains true after it); no record of CCTNS V2's own internal `DATE_CREATED`/`DATE_MODIFIED` semantics beyond what the field names themselves suggest (no provider documentation was found stating, for example, whether `DATE_CREATED` reflects first case-entry or first record-of-this-specific-row creation — this matters for how strongly §F.1's "identical to the millisecond" finding can be read, and is flagged as a genuine, unresolved interpretive gap, not glossed over).

**Cannot be proven, even with everything above:** whether CCTNS V2's own internal system ever performs an in-place combined→separate transition on a single record (this would require either CCTNS-V2-internal logs or two independent CCTNS-side observations of the same still-combined record, neither of which exists); whether the FIR 869/2026 pattern is deliberate CCTNS V2 workflow or a data-entry artifact (requires asking the CCTNS V2 provider directly — Audit §11 Q2, still open); anything about CCTNS V2 behaviour before 2026-09-25 (no trail of any kind reaches further back, on either side of this investigation).

---

## J. Implications for DOPAMS ETL3 (no changes implemented here)

1. **The one real case found is already mitigated, not newly discovered.** The Implementation Report's crime-level-overlap check (`etl3/merger/accused_overlap.py`, `build_split_rows()`'s `existing_crime_accused` parameter) specifically targets FIR 869/2026's pattern — withhold a segment from auto-splitting when it already overlaps an existing accused on the same crime — but **that fix is not yet live anywhere**, because migration 023 (`accused_split_review`) is still not applied to the real database (§C.2, re-confirmed unchanged). Until it is applied and deployed to whichever host actually runs the scheduled pipeline (itself an open, unresolved question per the Implementation Report §13 blocker #2), a **second** real case like FIR 869/2026, if one newly arose in CCTNS V2's live data, would still be triple-represented by the current (pre-fix) production logic, not safely withheld for review.
2. **The observation window is stale and getting staler.** `consolidation_run_log`'s last successful run was 2026-11-06; as of this pass it is 18 days stale (up from 13 at the time of the Audit). No new V2 accused/person observations have landed since. Any future claim about "what CCTNS V2 has done since 2026-11-06" is unverifiable until this pipeline resumes — this is an operational gap, not a data-integrity one, but it directly limits how much more this kind of investigation can learn going forward.
3. **The fresh V2-only scan (§E) is good news, read correctly.** Only one high-confidence combined record exists in all of current V2 data. This is reassuring about *frequency*, not about *risk per occurrence* — the one case that does exist showed a duplication-of-duplication effect (9 rows for ~4 people) specifically because no cross-check against the crime's existing roster existed at the time. If a second case appears after the stale window resumes, the same category of risk (tripling, not merely doubling, a source-side duplication) applies until migration 023 is live.
4. **Two separate, non-reconciled "same person?" mechanisms already coexist in the broader DOPAMS stack** (`identity_links` in ETL3, `person_deduplication_tracker` on the legacy "dev-2" database) **plus a third, newer one** (`accused_split_review`, not yet live). None of the three is a superset of another, and this project's own architecture docs already warn against conflating any two of them (§C.3). This is not a new risk this report created, but it is a relevant piece of context for anyone reasoning about "could DOPAMS end up with duplicate accused identities" generally, beyond the specific CCTNS-V2-packed-name mechanism this investigation was scoped to.

---

## K. Final Recommendations (evidence-supported only; no fix implemented)

1. Apply migration 023 (`accused_split_review`) and resolve the unconfirmed deployed-commit/flag-state question on the actual pipeline host — both already identified as blockers in the Implementation Report (§13, items 1 and 2) and **re-confirmed still open, unchanged, in this pass.**
2. Restart or otherwise investigate why `consolidation_run_log` has not produced a successful run since 2026-11-06 — now an 18-day gap, growing — before relying on any further CCTNS-V2 historical-trend analysis.
3. Put the two still-open client-confirmation questions from the Audit (§11, Q2 and Q4) to the CCTNS V2 provider directly: is the FIR-869-shaped coexistence a known data-entry pattern, and should ETL3's split feature remain enabled for V2 given a 1-in-34,373 current hit rate against a demonstrated triple-duplication risk when it does fire.
4. Treat `person_deduplication_tracker` (§C.3) as out of scope for any CCTNS-V2 accused-identity reconciliation work — it is a different database, a different owner, and already explicitly excluded from the current ingestion pipeline's own schema contract; do not let it be rediscovered as a "mystery dedup table" in a future investigation.
5. The 80 V2 `accused_unified` rows with no join-resolvable `persons_unified` row (§C.2, §D) were not individually traced in this pass — a short, purely read-only follow-up query against `source_gap_ledger`'s `unlinked_accused`/`unlinked_persons_placeholder` rows (currently 1 each) versus this 80-row figure would close a real, small gap this report leaves open.

---

## Final Response to the Brief's §12 Questions

1. **Can CCTNS V2 return combined accused names and later return the same people as separate accused records?** Possible, yes. "Later" specifically (a true in-place or sequential transition) is **not evidenced** — the one real case shows **simultaneous** creation of both forms (§F.1), not a later appearance of the separate ones.
2. **Does its API implementation allow this?** Yes — nothing in the audited `/accused`/`/person-details` contract prevents a crime from carrying both a packed-name record and separately-coded records at once; the packing lives entirely in the person's free-text name field.
3. **Does CCTNS V2's own ETL create or contribute to this behaviour?** Cannot be determined — CCTNS V2's internal ETL is not accessible to this investigation at all. What **is** confirmed is that **DOPAMS's own ingestion ETL does not create it** (§G, every question) — the duplication is present in the data at the earliest point DOPAMS can observe it.
4. **Do historical API responses prove that it has happened?** No raw historical API responses survive anywhere on this system (§C.5, confirmed by direct filesystem check); the closest available evidence is ETL3's one-layer-downstream `accused_source` observation, which does prove the combined and separate forms were both present in the ingested data as of the earliest observation (2026-09-25).
5. **Does the CCTNS V2 database prove that it has happened?** DOPAMS has no access to a "CCTNS V2 database" in the government-system sense — only to DOPAMS's own `cctns-v2` mirror database and ETL3's `dopams_cctns_v2`. Within those, yes: the live, current-state data (re-confirmed 2026-11-24) proves the coexistence for FIR 869/2026, unchanged since first observed.
6. **Is the behaviour intentional, documented, or an anomaly?** Not established as intentional or documented by anything found in this codebase; not contradicted either. Genuinely **Unknown** per the brief's own classification scheme — a question for the CCTNS V2 provider, not resolvable from the client side alone.
7. **How many confirmed cases exist across the scanned historical data?** **One** confirmed real case of the brief's exact "combined-and-separate" shape (FIR 869/2026), re-confirmed unchanged across two independent passes 5 days apart. A fresh, full current-state rescan of every V2 accused name (§E, new in this report) found no second instance of the same high-confidence shape anywhere in the live data.
8. **What evidence proves each case?** FIR 869/2026: direct SQL against live `accused_unified`/`persons_unified`/`accused_source`/`accused_split_audit`, cited with exact table/column/timestamp evidence throughout §F and the Appendix; the system-wide "no second case" claim: a full, fresh, code-level re-run of the live multi-accused detector against all 34,373 currently-linked V2 names (§E), not an estimate.
9. **What can we definitively conclude about FIR 869/2026?** It is a **source-side coexistence case** (brief §8 letter A): CCTNS V2's own data already carried 4 independently-coded accused and 1 combined-pack accused for (almost certainly) the same 4 people, created at the identical millisecond per CCTNS's own metadata — not a chronological transition, and not introduced by either DOPAMS ingestion ETL. ETL3's own split feature then added 4 more rows on top before the fix described in the Implementation Report; that fix is written, tested, and not yet deployed to the live database (migration 023 still unapplied, re-confirmed in this pass).
10. **What remains impossible to prove because of missing historical logs?** Whether CCTNS V2's own internal system ever transitions a single record in place from combined to separate (no raw API log and no second CCTNS-side observation of this record exists, or could exist retroactively); anything about CCTNS V2 behaviour before 2026-09-25; whether the FIR 869/2026 pattern is deliberate CCTNS V2 workflow versus a data-entry artifact (requires provider-side confirmation this investigation cannot obtain).

---

## Appendix: Evidence Reference Index (this pass)

**Live read-only SQL run against `dopams_cctns_v2`** (via `get_unified_connection(readonly=True)`) and **`cctns-v2`** (via `get_v2_source_connection()`), both confirmed `transaction_read_only=on`, 2026-11-24 07:35-07:40 UTC: `consolidation_run_log`, `accused_split_audit`, `accused_split_review` (confirmed absent), `bulk_event_exclusions`, `accused_unified`/`persons_unified` (FIR 869/2026 roster + system-wide counts), `change_log`, `accused_source`/`persons_source` (schema + window), `source_gap_ledger`, `person_enrichment`, `information_schema.columns`; `person_deduplication_tracker` queried against `cctns-v2` and confirmed absent.

**Code read this pass (new, beyond the three prior reports' own index):** `cctns-v2/etl-accused/etl_accused.py` (lines 96-99, 1126, 1301, 1580-1714, 1933-2070, 2117-2171, 2224-2236), `cctns-v2/etl-persons/etl_persons.py` (lines 55-56, 1641-1730, 2064), `etl3/db/connections.py` (full file), `etl3/config/settings.py` (full file), `etl3/enrichment/multi_accused.py` (full file, executed live against current data), `cctns-v2/cctns-v2_schema.sql` (header comment, lines 1-75), `cctns-v2/DB-schema.sql` (header/dump metadata, `person_deduplication_tracker` definition at lines 4586-4620), `docs/DOPAMS_DEV2_CCTNS_ARCHITECTURE_AUDIT.md`, `docs/DOPAMS_UNIFIED_CCTNS_USECASE_MAPPING.md`, `etl3/docs/PHASE_7_DOPAMS_CUTOVER_READINESS.md`, `etl3/docs/UNIFIED_DB_DEEP_VALIDATION_REPORT.md` (all four grepped/read for `person_deduplication_tracker` cross-references).

**Prior project reports relied upon as corroborating evidence, re-verified wherever a live re-query was possible:** `CCTNS_V2_ACCUSED_API_CHANGE_AUDIT.md`, `CCTNS_V2_ACCUSED_API_CHANGE_AUDIT_APPENDIX.md`, `DOPAMS_ETL3_ACCUSED_RECONCILIATION_IMPLEMENTATION_REPORT.md` — every quantitative claim re-derived in this pass (consolidation-run gap, split-audit counts, FIR 869 roster, source/change_log windows, source_gap_ledger total) matched or extended (never contradicted) the prior reports' figures; the only numeric drift found was `source_gap_ledger`'s V2 total (132 → 142) and `person_enrichment.invalid_literal` (150, V2-specific → 189, not re-filtered to V2 in this pass — flagged as a caveat, not a contradiction, in §C.2).

**Key real identifiers (unmasked, structural only):** FIR `869/2026` / crime `6a359f414d1e611f530f007e` / combined accused `6a35a0541103855a721a342c` / coded accused `6a363156a31682272e6e4fad` (A1), `6a3628db469624eb39d009b1` (A2), `6a3615b8872ad512c2ecc4e0` (CCL3), `6a3636b86e9012f576065bc6` (A4) / split children `6a35a0541103855a721a342c:split:f33ce37de0dae84e7f8e53f9`, `...2c0cf62a16781f77a8fd76b4`, `...3433fc63bf877891b7e235c4`, `...1cd2de4fbc8e9e3a12e25fe3`.
