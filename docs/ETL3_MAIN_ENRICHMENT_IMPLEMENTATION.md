# ETL-3 enrichment from the `main` reference

ETL-3 still consolidates CCTNS V1 and V2 into `dopams_cctns`. It now also builds the derived fields that the `main` branch generated, without writing to either source database and without changing either source ETL.

Canonical `*_unified` columns are left as the source stored them. Derived values go in separate enrichment tables. Raw `*_source` payloads are not rewritten.

## 1. What `main` actually generated

`origin/main` and `cctns-v2/` are the same pipeline. The live database `cctns-v2` is the pure CCTNS store: it does **not** contain `brief_facts_ai`, `drug_categories`, or `drug_ignore_list`. `geo_reference` and `geo_countries` exist and have zero rows.

Fields the old jobs wrote, and where they are available to ETL-3:

| Data element | `main` processing | Present in live V2? | ETL-3 action |
|---|---|---|---|
| `crimes.class_classification` | Deterministic NDPS section rules in `section-wise-case-clarification/process_sections.py` | Yes, 9,613 / 9,613 crimes | Copy the stored value. For V1, run the same rules on `section_of_law` |
| `crimes.case_status` dictionary | `etl_case_status/case-status.sql` overwrites the raw status | The raw value is what V2 currently holds when a later crime reload undoes the map | Store the mapped label beside the raw status. Do not overwrite `crimes_unified.case_status` |
| `persons.domicile_classification` | Deterministic country/state rules in `domicile_classifier.py` | Yes, 29,747 persons | Copy the stored value. Recompute only when the column is empty and structured geo fields exist |
| `persons.surname`, `relation_type`, `gender_source` | API passthrough, plus offline `@` cleanup scripts | Yes | Copy, then apply the four `fix_fullname` rules on V1 and V2. Cleaned name, alias, and relative are stored beside the source name |
| Arrest `is_41a_crpc`, `is_41a_explain_submitted`, `date_of_issue_41a`, `accused_type` | API passthrough, plus `parse_accused_status` when status text contains `41a` and `issued` | Yes, 8,954 arrests with `is_41a_crpc` true | Copy. Fill a false/null 41A flag from accused status text only when the source flag is not already true |
| `is_41a_pending` | Parsed from status text and then discarded | Not stored | Not stored |
| Hierarchy `sub_zone_*`, `adg_*` | Column rename from the hierarchy API | Yes | Copy onto `hierarchy_enrichment` |
| Chargesheet NBW and taken-on-file | Flattened API fields | Yes | Copy onto `chargesheet_enrichment` |
| Property status, nature, place, category, values | API coercion | Yes | Copy. `additional_details.WEIGHT` / `WEIGHT_IN` become normalized drug measurements |
| FSL status, opinion, disposal fields, `mo_id` | Mostly API copy. V2 remaps `mo_id` before insert | Yes, already remapped in the source row | Copy the stored row. FSL stays out of `fsl_unified` |
| Disposal type / date | API copy | Yes | Copy |
| Drug name, form, category, grams/kg/ml/litres/count, worth, commercial flag | `brief_facts_ai` LLM plus KB plus `standardize_units` | The AI table is absent. V1 dossier weights and V2 property weights are present | Deterministic unit conversion and category map. AI client exists for brief-facts text and is off unless enabled |
| Accused drug role (`peddler`, `consumer`, …) | Keyword classifier on LLM role text in `brief_facts_ai` | Role text is not in this V2 database | Enrichment only for existing CCTNS `accused_id`. FIR may fill missing role/type and person fields. Narrative-only names are never created as accused |
| Address ward/state fill from `geo_reference` | KB lookup, then Ollama only for what the KB missed | Reference tables are empty in live V2 | Exact and trigram KB lookup runs. Confirmed fields go on `person_enrichment.address_resolution`. The address model is not called. An empty KB confirms nothing |

## 2. AI extraction

Implemented in `etl3/enrichment/ai.py`.

| Item | Value |
|---|---|
| Model | `LLM_MODEL_EXTRACTION` (no default; the old extraction client also had no default) |
| Host | `OLLAMA_HOST`, default `http://localhost:11434` |
| Temperature | 0 |
| Prompt | Production prompt from `brief_facts_ai/extractor_drugs.py`, stored at `etl3/enrichment/drug_extraction_prompt.txt` |
| Input | Crime `brief_facts` (V2) or `fir_contents` (V1) |
| Output | Drug pass: JSON `{"drugs":[...]}`. Accused pass (separate prompt): JSON `{"accused":[...]}` only for the CCTNS roster |
| Validation | Object with a `drugs` list. Each row needs a name, a non-negative quantity and worth, `drug_form` in `solid\|liquid\|count`, `worth_scope` in `individual\|drug_total\|overall_total`. Markdown fences are stripped |
| Empty output | `{"drugs":[]}` is valid and stores no drug row |
| Invalid / malformed | Rejected. No drug row is written and existing rows are kept |
| Retry | One retry for timeout, invalid JSON, and transport errors |
| Timeout | `LLM_TIMEOUT`, default 300 seconds |
| Idempotency | An attempt is keyed by `(crime_id, sha256(brief_facts), status)`. Success, empty, and invalid are not repeated. Timeout/error may be tried until three attempts |
| Enable | `ETL3_AI_ENABLED=1`. Optional `ETL3_AI_LIMIT` caps how many crimes are sent |

The historical backfill did not call a model. `LLM_MODEL_EXTRACTION` is not set in the V2 environment, and `brief_facts_ai` is not in the live source. Turning the flag on uses the same pass as the backfill, after observations exist, and a failure cannot update `crimes_unified`.

## 3. Knowledge base

`drug_categories` is not in live `cctns-v2`. ETL-3 does not read `dev-2`.

The alias file `cctns-v2/drug_standardization/drug_mappings.json` is copied to `etl3/enrichment/drug_mappings.json` and loaded in file order.

Lookup, first hit wins:

1. Exact match on the lowercased raw name, or on a compacted alphanumeric form.
2. A KB key contained in the raw name.
3. The raw name contained in a KB key, only when the raw name is at least 4 characters.

No match leaves `primary_drug_name` equal to the raw name. It does not invent a label. There is no pg_trgm tier, because that queried a table this database does not have.

`drug_category` is the fixed map in `brief_facts_ai/db.py` `_resolve_drug_category` (`Cannabis`, `Opioid`, `Stimulant`, `Sedative/Benzodiazepine`, `Hallucinogen`, otherwise `Other`). A null name stays null, not `Other`.

The drug row hash covers quantity, unit, raw name, and measurements. It does not cover the KB label. Editing the alias file does not rewrite rows whose source measurement is unchanged.

## 4. Deterministic transformations

From `etl3/enrichment/rules.py`, matching the old code:

- Section classification priority: Cultivation > Commercial > Intermediate > Small. Digits-only, `27*`, and exact `8c` are Small. `20a` is Cultivation. A trailing `a`/`b`/`c` is Small/Intermediate/Commercial.
- Case-status map: `PT Cases` and `Pending Trial` → `PT`; `UI Cases`, `New`, `Under Investigation`, `Under Trial` → `UI`; `Chargesheet Created` → `Chargesheeted`; `compounded` → `Compounded`. Any other value is kept.
- Domicile: permanent country, else present country, else nationality. Anything other than `india` is `international` (so `Indian` is international, which is the old rule). India + `telangana` is `native state`. India + another listed state/UT is `inter state`. Missing state with country `india` is null. `default` is treated as missing.
- Units: g→grams and kg (`/1000`), kg→grams (`*1000`), mg→grams (`/1000`), litre↔ml (`*1000` / `/1000`). A `wg` / `w/g` sentence with worth/quantity above 1000 is treated as kilograms. Unknown unit with a solid/liquid/count form uses that form. A missing quantity stays missing. Zero grams stays zero.
- Commercial quantity uses the old NDPS thresholds (ganja 20 kg, heroin 0.250 kg, and the rest of the dicts in `extractor_drugs.py`). `mdm` is compared as `mdma`. An existing true flag propagates to the crime+drug group.
- Brief-facts drug extraction keeps drug name, quantity, unit, worth, and the seizure-only filter.
- Brief-facts accused enrichment keeps only existing CCTNS `accused_id` values. It fills missing role, type, age, alias, gender, occupation, phone, address, status, and CCL. It never invents a synthetic accused_id, never creates a narrative-only accused, and never overwrites a populated CCTNS/person field. CCL uses that accused's age or an explicit CCL statement about them; another accused's age does not transfer. Person identity for a null `person_id` is stored on `accused_enrichment` only; `persons_unified` is not invented from FIR text.
- V1 has no separate persons table: identity is read from the accused dossier (`accused_name`, `alias_name`, `accused_occupation`, `mobile_1`, addresses) and from `persons_unified` when linked. V1 `accused_code` prefers a CCTNS value, else an A-code explicitly tied to that name in the FIR. V1 category is `CCL` when that accused's age is under 18 and `Accused` when 18+. Minors are renumbered `CCL 1`, `CCL 2`, … even if the FIR called them A1/A2. Missing age or code stays unresolved.

## 5. Source mappings

All of these are read from `*_source.payload` in `dopams_cctns`, which Phase 3 already captured with `SELECT *`. Enrichment does not open V1 or V2.

| Target | Observation | Payload fields |
|---|---|---|
| Crime class / status | `crimes_source` | V2 `class_classification`, `acts_sections`, `case_status`. V1 `section_of_law`, `fir_status` |
| Person | `persons_source` (V2 only) | `domicile_classification`, `surname`, `relation_type`, `gender_source`, and the geo columns when domicile is null |
| Arrest 41A | `arrests_source` plus V2 `accused_source.accused_status` | `is_41a_crpc`, `is_41a_explain_submitted`, `date_of_issue_41a`, `accused_type`, `accused_seq_no` |
| Accused category | `accused_source` | `type` |
| Chargesheet | `chargesheets_source` | `taken_on_file_*`, `accused_requested_for_nbw` |
| Hierarchy | `hierarchy_source` | `sub_zone_code`, `sub_zone_name`, `adg_code`, `adg_name` |
| Property | `properties_source` | `property_status`, `nature`, `place_of_recovery`, `category`, `estimate_value`, `recovered_value`, `additional_details.WEIGHT`, `WEIGHT_IN`, `SPECIFICATION_OF_DRUG` |
| FSL | `fsl_source` | `status`, `mo_id`, `fsl_no`, `opinion`, `report_received`, disposal fields |
| Disposal | `disposal_source` | `disposal_type`, `disposal`, `case_status`, `disposed_at` |
| V1 drug weight | `accused_source` where `source_system='V1'` | `drug_type`, `weight_gm`, `weight_kg`, `fir_reg_num` |

## 6. Target mappings

| Table | Key | Provenance |
|---|---|---|
| `crime_enrichment` | `crime_id` | `source_column`, `deterministic_sections`, `dictionary`, `source_unchanged`, `non_recoverable` |
| `person_enrichment` | `person_id` | `source_column` or `deterministic` |
| `arrest_enrichment` | `arrest_id` | `source_column` or `status_text` |
| `accused_enrichment` | `accused_id` | CCTNS category copied; FIR fills missing role/type/person fields for that `accused_id` only; `field_sources` provenance |
| `chargesheet_enrichment` | `charge_sheet_id` | copied lifecycle fields |
| `hierarchy_enrichment` | `ps_code` | copied sub-zone and ADG |
| `property_enrichment` | `property_id` | copied property fields |
| `fsl_enrichment` | `case_property_id` | copied FSL fields, no FK to `fsl_unified` |
| `disposal_enrichment` | `disposal_id` | copied disposal fields |
| `drug_extractions` | `extraction_id` | `v1_dossier`, `v2_property`, or `etl3_ai` |
| `ai_extraction_attempts` | `(crime_id, input_hash, status)` | one outcome per text hash |
| `enrichment_run_log` | `run_id` | stats for that pass |

`extraction_id` is `V1:dossier:{accused source id}` or `V2:property:{property_id}` or `{source}:ai:{crime_id}:{ordinal}`.

## 7. Schema

- `etl3/migrations/007_enrichment.sql` creates the tables above.
- `etl3/migrations/008_enrichment_cascade.sql` sets `ON DELETE CASCADE` from each derived row to its unified parent, so a unified rebuild is not blocked. The next enrichment pass recreates the derived row from observations.

Both are applied to `dopams_cctns` only.

## 8. Historical backfill

`python etl3/run_enrichment.py` was run against the already consolidated database.

First pass inserted: 16,918 crimes, 32,973 persons, 33,055 arrests, 33,060 accused, 12,760 chargesheets, 817 hierarchy rows, 7,700 properties, 2,008 FSL rows, 487 disposals, 12,240 V1 drug rows, 5,285 V2 property drug rows.

V1 section classification: 7,304 classified, 1 non-recoverable. V2 class labels were copied (0 mismatches against the latest crime payload).

## 9. Incremental processing

`etl3/run_phase4_consolidation.py` calls `run_enrichment` after hierarchy consolidation and before the cursor advance. Phase 5 incremental calls that same consolidation function, so a later source observation is enriched on the next ETL-3 cycle.

Each family commits on its own. A crash keeps finished families. The next pass compares `input_hash` and does not insert another `change_log` row when the hash matches.

A second pass after the backfill reported `unchanged` for every family and `deleted: 0` for drugs.

## 10. Provenance

Every enrichment row has `input_hash` and `enrichment_run_id`. Crime, person, arrest, and accused rows also store a method column (`source_column`, `deterministic_sections`, `status_text`, `non_recoverable`, and so on). Drug rows store `provenance` and `kb_match_tier`.

`change_log` gets one row per enrichment entity when the hash changes (`initial_observation` or `business_change`, field `input_hash`). A replay writes none.

## 11. Failure handling

- Invalid AI output is recorded in `ai_extraction_attempts` and does not delete existing `etl3_ai` rows.
- Timeout retries once inside the call, then records `timeout`. Further runs may retry until three attempts.
- A database error marks `enrichment_run_log.status = failed` and re-raises. Completed families stay committed and are safe to rerun.
- Rows whose crime, person, arrest, or property is not in the unified table are skipped. No parent is invented.
- Zero weights do not create a drug row.

## 12. AI dependencies

Ollama at `OLLAMA_HOST`, model `LLM_MODEL_EXTRACTION`, flag `ETL3_AI_ENABLED=1`. Without the flag the pass returns `{"status": "disabled"}` and changes nothing. The historical load used that disabled path.

## 13. KB dependencies

The checked-in alias file only. No runtime read of `drug_categories`, `dev-2`, or the empty `geo_*` tables.

## 14. Test results

`python etl3/tests/test_enrichment.py` covers sections, case status, domicile, units (including null, zero, and invalid quantity), commercial thresholds, KB exact/substring/miss, accused keywords, 41A text, projection that refuses zero weights and unknown crimes, stable hashes, AI valid/empty/malformed output, and one retry.

The existing ETL-3 suite was run after the backfill:

- Passed: adapter safety, connections, daily source gate, enrichment, phase 3, phase 4, phase 5 acceptance, phase 5 incremental, phase 6, phase 7, V1 adapter, V2 adapter.
- Phase 3 no longer pins hierarchy at 816 rows. The live hierarchy table is 817. The test still requires the second capture to insert nothing.
- Phase 7 chargesheet pins were updated to the current consolidated counts (V1 court 7,535, V2 chargesheets 7,093, V2 updates 6,217). Distinct `charge_sheet_id` still equals the row count, and every update raw id still also appears on a court row.
- The hierarchy crash test deletes `hierarchy_unified`. Cascade removed `hierarchy_enrichment` during that test. A following enrichment pass reinserted all 817 hierarchy rows and left every other family unchanged.

## 15. Live validation

| Check | Result |
|---|---|
| `current_database()` on the writer | `dopams_cctns` |
| Source sessions used for snapshots | `transaction_read_only = on` for `cctns_v1` and `cctns-v2` |
| Enrichment writer | `get_unified_connection` only. The enrichment package does not import the V1 or V2 connection helpers |
| V2 `tup_inserted`, `tup_updated`, `tup_deleted` across the backfill window | Unchanged (`1026091`, `1376859`, `26963`) |
| V1 `tup_inserted` and `tup_deleted` | Unchanged (`208332`, `9323`) |
| V1 `tup_updated` | Moved by 152 during the window. The enrichment process did not open `cctns_v1`. The delta is other sessions on that database. Adapter tests still show a write on the ETL-3 read-only session is rejected |
| Class label vs latest V2 payload | 0 mismatches |
| Domicile vs latest V2 payload where method is `source_column` | 0 mismatches |
| `crimes_unified.case_status` vs dictionary label | 3,241 rows differ, and in all of them the unified column still holds the raw status |
| Drug primary-key duplicates | 0 |
| Drug rows with a null crime | 0 |
| 41A true | 8,954, same count as live V2 `arrests.is_41a_crpc` |
| Hierarchy ADG / sub-zone names | 817 / 263 |
| NBW payload present | 7,093 |
| Taken-on-file date present | 5,637 |
| FSL status present | 2,008 |

## 16. Old versus new

| Input | Old result | ETL-3 result | Match |
|---|---|---|---|
| V2 `class_classification = Small` (and the other stored labels) | Stored column | `crime_enrichment.class_classification`, method `source_column` | Yes, 0 mismatches |
| V1 `section_of_law` with no V2 class column | Rules in `process_sections.py` | Same rules, method `deterministic_sections` | Same function. 7,304 labeled, 1 with no section token |
| `PT Cases`, `Under Investigation`, `compounded` | SQL map to `PT`, `UI`, `Compounded` | `case_status_normalized` | Yes. Raw status remains on `crimes_unified` |
| V2 `domicile_classification` | Stored column | Copied, method `source_column` | Yes, 0 mismatches |
| `Indian` nationality with no country | Old function returns `international` | Same function, only when geo or nationality is present and the stored domicile is null | Same rule |
| Arrest `is_41a_crpc` | Stored boolean | Copied | 8,954 true on both sides |
| Status text `41A issued on 02/03/2024` and flag not already true | `is_41a_crpc` true, date `2024-03-02` | `arrest_flag_method = status_text` | Yes, unit-tested |
| V1 `Ganja`, `weight_gm = 1000` | `standardize_units`: 1000 g, 1 kg, category Cannabis | `drug_extractions` provenance `v1_dossier` | Yes, sampled live rows |
| Ganja group `weight_kg >= 20` | `is_commercial` true | 260 such rows flagged | Same threshold |
| Brief-facts narrative with no structured weight | `brief_facts_ai.drugs` via Ollama | Not generated in this backfill | Source table is absent. The client is the same prompt and is gated. See section 17 |
| Accused role from brief facts | roles + gap-fill names into `brief_facts_ai` | Existing CCTNS `accused_id` only; fill missing fields; no synthetic id; no narrative-only accused | Yes for roster members. Gap-fill discovery removed |

## 17. Non-recoverable from the current sources

- Narrative drug rows and seizure worth inside brief facts. Those can be produced later with `ETL3_AI_ENABLED=1` and a configured extraction model. They are not guessed.
- Accused enrichment from brief facts also needs `ETL3_AI_ENABLED=1`. Only existing CCTNS `accused_id` rows are enriched. Names that appear only in the FIR are not stored as accused.
- `drug_categories` fuzzy matches (`pg_trgm`, threshold 0.35). The table is not in the source database.
- Address normalization against `geo_reference` / `geo_countries`. Both tables are empty, so there is nothing to look up.
- V1 domicile. V1 persons are not in `persons_source`, and the V1 arrest payload has address text and nationality but not `permanent_state_ut` / `permanent_country`.
- Surname split out of `full_name`. The old persons ETL did not do that.
- `is_41a_pending`. The old parser set it and did not persist it.
- Interrogation `drug_quantities` is almost entirely null in the captured payloads, so it is not a second quantity source.
