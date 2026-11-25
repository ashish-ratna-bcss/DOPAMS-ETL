# Appendix: Supporting Evidence — CCTNS V2 Accused API Change Audit

Companion to `CCTNS_V2_ACCUSED_API_CHANGE_AUDIT.md`. All data below was pulled via read-only SQL against the live `dopams_cctns_v2` database (host `192.168.103.106`), through `etl3.db.connections.get_unified_connection(readonly=True)`, on 2026-11-19. Names are masked (first letter of each word kept, remainder replaced with `*`); every example carries its real, unmasked system identifiers so the client can retrieve the real text from their own systems.

---

## A. FIR 869/2026 — full evidence trail

**Identifiers:** `fir_reg_num = 2022011260869`, `fir_num = 869/2026`, `crime_id = 6a359f414d1e611f530f007e`, `fir_date = 2026-06-19`, source_system = V2.

### A.1 Raw `accused_source` observation (the combined-name parent)

Single observation, `source_run_id = aebf1b62-cc68-44f9-98fe-081ad9b3ddef`, `ingested_at = 2026-10-08T15:29:28Z` (no second observation exists for this record in the available window):

```json
{
  "ear": "", "eyes": "", "face": "", "hair": "", "mole": "", "nose": "",
  "type": "Known", "beard": "", "build": "", "color": "", "teeth": "",
  "height": "", "is_ccl": false,
  "seq_num": "202201126086967001",
  "crime_id": "6a359f414d1e611f530f007e",
  "mustache": "",
  "person_id": "6a359fd85a42d644ff8bfb8b",
  "accused_id": "6a35a0541103855a721a342c",
  "etl_run_id": "aebf1b62-cc68-44f9-98fe-081ad9b3ddef",
  "fetched_at": "2026-09-25T03:38:38.770277+00:00",
  "leucoderma": "", "accused_code": "",
  "date_created": "2026-06-19T19:57:53.654000",
  "date_modified": "2026-08-24T09:59:00.164000",
  "source_system": "CCTNS_V2",
  "accused_status": "Absconding",
  "source_endpoint": "/accused"
}
```

Note: `date_modified = 2026-08-24T09:59:00` falls inside the documented 2026-08-24 bulk-administrative-timestamp window (see main report §6) — this record's `DATE_MODIFIED` touch that day cannot be assumed to represent a real content change on its own; it is used here only as a timestamp, not as proof of a human edit on that date.

The linked person's `FULL_NAME` (from `persons_source`, same `source_run_id`): `"1). D**** S******, 2). S*** A***** A**, 3). B**** k****** N***, 4). K******** M******"` — mapping: `D****`=4-letter first name, `S******`=7-letter surname (segment 1); `S***`=4-letter, `A*****`=6-letter, `A**`=3-letter (segment 2); `B****`=5-letter, `k******`=7-letter (lowercase-leading, as in source), `N***`=4-letter (segment 3); `K********`=9-letter, `M******`=7-letter (segment 4).

### A.2 The 4 pre-existing, separately-coded accused on the same `crime_id`

All 4 share `current_as_of = 2026-08-24T09:59:00.164Z` (same bulk-touch timestamp as A.1) and `current_source_run_id = aebf1b62-cc68-44f9-98fe-081ad9b3ddef` (same observation batch):

| `accused_id` | `accused_code` | `accused_status` | `person_id` | `full_name` (masked) |
|---|---|---|---|---|
| `6a363156a31682272e6e4fad` | `A1` | `41A Cr.P.C issued on 20/06/2026` | `6a3631...` (own) | `S****** G*** d****` |
| `6a3628db469624eb39d009b1` | `A2` | `41A Cr.P.C issued on 20/06/2026` | `6a3628...` (own) | `A*** A** S***` |
| `6a3615b8872ad512c2ecc4e0` | `CCL3` | `41A Cr.P.C issued on 20/06/2026` | `6a3615...` (own) | `k****** H****` |
| `6a3636b86e9012f576065bc6` | `A4` | `41A Cr.P.C issued on 20/06/2026` | `6a3636...` (own) | `M****** K*********` |

Token-overlap mapping to the combined record's 4 segments (by shared first letters and length, since full text is masked in this document but was compared unmasked during the investigation):
- `S****** G*** d****` (A1) ↔ segment 1 `D**** S******` — shares the 7-letter "S******" token; order and casing differ, and an extra 4-letter "G***" token appears in A1's version.
- `A*** A** S***` (A2) ↔ segment 2 `S*** A***** A**` — shares all 3 tokens, reordered, with "A*****"/"A**" lengths swapped (6↔3).
- `k****** H****` (CCL3) ↔ segment 3 `B**** k****** N***` — shares the 7-letter "k******" token; "H****" (5 letters) vs. "B****"/"N***" in the combined version does not exactly match either trailing token, the weakest link in this mapping.
- `M****** K*********` (A4) ↔ segment 4 `K******** M******` — same 2 tokens, exactly reordered.

**This mapping is evidence, not certainty** — it is why §11, question 2 of the main report asks the client to confirm identity directly rather than this audit asserting it.

### A.3 What ETL3's split pass produced (2026-11-18T14:57:10.485Z, `enrichment_run_id = 3df9c7c7-2d51-4f0c-9bcb-730817c7f698`)

4 new rows in `accused_split_audit` (and matching `accused_unified`/`persons_unified` rows), `parse_method = numbered_pack_v1`:

| `created_accused_id` (suffix shown) | `segment_index` | `raw_segment_text` (masked) |
|---|---|---|
| `...:split:f33ce37de0dae84e7f8e53f9` | 1 | `D**** S******` |
| `...:split:2c0cf62a16781f77a8fd76b4` | 2 | `S*** A***** A**` |
| `...:split:3433fc63bf877891b7e235c4` | 3 | `B**** k****** N***` |
| `...:split:1cd2de4fbc8e9e3a12e25fe3` | 4 | `K******** M******` |

The parent row `6a35a0541103855a721a342c` had `split_superseded_at` set to the same timestamp (retired from future AI-roster consideration; row still exists, not deleted). None of A.2's 4 rows were touched, referenced, or compared against by this process.

**Net result, today:** 9 live `accused_unified` rows for `crime_id 6a359f414d1e611f530f007e` (4 from A.2, 1 superseded combined parent, 4 new split children), for what the evidence above suggests are 4 real people.

---

## B. All 13 real split-parent events (system-wide), with V1/V2 attribution

Source: `accused_split_audit`, grouped by `parent_accused_id`; 32 total rows. Masked names shown where the real text is a person's name.

| Parent `accused_id` | Source | Crime (masked FIR where known) | Children (masked names) | Run batch |
|---|---|---|---|---|
| `6a35a0541103855a721a342c` | **V2** | FIR 869/2026 (§A) | D**** S******; S*** A***** A**; B**** k****** N***; K******** M****** | 2026-11-18 |
| `V1:09031b25290f14650aad93b03d30491f` | V1 | `203203612010434004` | R****** B******; R****** P******** | 2026-10-24 |
| `V1:10a7a071d7ee724d0779193ea78b310e` | V1 | `202201114026734003` | M*********** E***** R**; J********* K****** R** | 2026-11-18 |
| `V1:1386e3a27feb24c0aee6c2a4d7d94a04` | V1 | `203200414006034001` | J***** P***** s/o N******, 20 yrs, ST; J***** V******* d/o R***, 27 yrs | 2026-11-18 |
| `V1:2c0e9a175e6b15da15daa31f407d1127` | V1 | `202202414073334001` | G***** J**; R****** | 2026-10-24 |
| `V1:49c745a65ce25cbd8489992e3ac9d65b` | V1 | `202306712092434001` | A******** K****; G**** S*********** | 2026-10-24 |
| `V1:5707b813b1318dbbf5419c662d08f520` | V1 | `202202213081134003` | M******** L****; M******** J***** | 2026-11-18 |
| `V1:63f05b586c548ec6af3353d6695ffcb8` | V1 | `202306713000534001` | N.N****; L******** T****** R** | 2026-10-24 |
| `V1:6aca34e6e316abdc89ad243f34ad1ab5` | V1 | `203007513032334001` | K******* C******; B******* P*****; M****** V***** S*** S*****; M****** S*** s** | 2026-10-24 |
| `V1:875cb52044db81ffc90b26c147b56d32` | V1 | `202302514027934001` | A. P**** R****; K. V**** K******* | 2026-10-24 |
| `V1:a4c2aa446a2cf5155e4156e6c58cfb9e` | V1 | `203201811013367001` | B****** M****** S/o. P*********; S****** B****** M****** W/o. B****** M******; D***** S******* | 2026-10-24 |
| `V1:d65a32c0389610d5b6f1a810f2dc5b78` | V1 | `202305613022834001` | D****** A*****; N******** D******* | 2026-10-24 |
| `V1:ed28b7db2bd3989cd1d563adfe3040f3` | V1 | `202202214109234004` | B***** S****; K****** S****** K****; B***** S** R**** | 2026-11-18 |

**12 of 13 parents are CCTNS V1; 1 of 13 is CCTNS V2.** All children are `status='active'` as of this audit; none have yet been superseded by a later correction (no record has been corrected post-split in the available window).

---

## C. Sample of genuine V2 `person.full_name` corrections (masked)

From `change_log` where `entity='person'`, `source_system='V2'`, `field='full_name'`, `old_value IS NOT NULL`, `old_value <> new_value` — 378 total, all dated 2026-11-05 (one observed batch). `unified_id` is the real `person_id`.

| `person_id` | Old (masked) | New (masked) | `observed_at` (UTC) |
|---|---|---|---|
| `6adada71e3755a5107f31eb0` | K**** B** | K**** B** S**** | 2026-11-05 13:40:27 |
| `6aea2ef2fd5f30200992e8c0` | K**** v***** | V**** K**** | 2026-11-05 13:39:10 |
| `6a3a73d74b917e22c2d9d3a7` | T********* C**** S***** | C**** S***** | 2026-11-05 13:38:04 |
| `6adad9e9395c096f125ecf05` | H****** B** | A**** B** S***** | 2026-11-05 12:39:39 |
| `6aa377e1283ab683b4a091f1` | A****** M**** B********* | M**** B********* A****** | 2026-11-05 12:27:46 |
| `6a9a3a10fcfaf34123c9d02f` | D******* G***** K**** | G***** k*** | 2026-11-05 12:15:21 |
| `6aeb09499e485dc496999e98` | J****** M******* | J****** | 2026-11-05 11:23:11 |
| `6aeb50a21411ee7bc2a2e51a` | J***** R****** | R****** J***** | 2026-11-05 11:09:19 |
| `6aeb26505716eac95a7764ad` | P****** A***** | A**** J****** K*** M******** | 2026-11-05 10:57:35 |
| `66b59c089d3d5899b3ea30b6` | W**** | K**** W********* | 2026-11-05 07:43:53 |

Pattern observed across the full 378: the dominant shape is **word reordering** (surname/given-name order swapped) and **truncation/expansion** (a shorter name corrected to a fuller one, or vice versa), not simple spelling-typo fixes. A few examples (e.g. `6aeb26505716eac95a7764ad`, `6aeb25f0...`, `6aeb2598...` — three adjacent-timestamp rows at 10:51–10:57 on 2026-11-05) show what looks like a name-to-name reassignment rather than a correction of the same person's own name — this pattern is flagged for the client's attention but this audit cannot determine from text alone whether it is a genuine correction or a data-entry mix-up between two different real accused.

---

## D. Real `accused_status` lifecycle transitions (sample)

From `change_log`, `entity='accused'`, `source_system='V2'`, `field='accused_status'`, genuine (non-null-to-different-value) transitions — 1,197 total.

| `accused_id` | Old | New | `observed_at` (UTC) |
|---|---|---|---|
| `6adceac5df1623090a623a9b` | `Absconding` | `41A Cr.P.C issued on 04/11/2026` | 2026-11-05 18:42:55 |
| `6aeb39410b4a117989dbaaae` | `Absconding` | `Arrest By Police` | 2026-11-05 17:28:35 |
| `6aecb1047f928e9a008ef3e7` | *(blank)* | `Absconding` | 2026-11-05 16:48:58 |

---

## E. Quantitative rollup

| Metric | Count |
|---|---|
| `accused_unified` rows, V2 | 34,453 |
| `accused_unified` rows, V1 | 17,384 |
| `persons_unified` rows (all) | 54,529 |
| `accused_source` observation rows, V2 (append-only) | 75,656 |
| Distinct V2 `accused_id`s observed more than once (genuine re-observation) | 2,797 |
| Distinct V2 `person_id`s observed more than once | 2,784 |
| V2 `person.full_name` genuine corrections (`change_log`) | 378 |
| V2 `accused.person_id` genuine reassignments | 0 |
| V2 `accused.crime_id` genuine reassignments | 0 |
| V2 `accused.accused_status` genuine transitions | 1,197 |
| V2 `person.full_name` → blank/NULL/"Unknown" genuine transitions | 0 |
| V2 persons currently flagged `name_identified=false` (standing state, not a transition) | 191 |
| Real `accused_split_audit` rows (system-wide) | 32 (13 parents) |
| ...of which V2-sourced | 4 children / 1 parent |
| ...of which V1-sourced | 28 children / 12 parents |
| PK collisions, `accused_unified` | 0 |
| PK collisions, `persons_unified` | 0 |
| Crimes (V2) with >1 non-split-derived accused | 7,734 of 10,027 total V2 crimes with any accused |
| V2 `source_gap_ledger` open issues (all types) | 132 (114 `unresolved_arrest_accused_link`, 12 `unresolved_interrogation_person_link`, 3 `fk_retry_capped`, 1 each `address_unresolved`/`unlinked_accused`/`unlinked_persons_placeholder`) |
| V2 `accused_source`/`persons_source` observation window | 2026-09-25 → 2026-11-06 (fetched); 2026-10-08 → 2026-11-06 (ingested) |
| Last successful ETL3 consolidation run (any source) | 2026-11-06 07:15–07:48 UTC — none since, as of 2026-11-19 |

**From the legacy (pre-ETL3, `public.persons`/`criminal_profiles_mv`) 51,123-record export** (per `DOPAMS_ETL3_NAME_CLEANING_EDGE_CASE_AUDIT.md` — this blends V1 and V2 display text as shown to the current frontend, not a V2-only figure, and is cited here as context on source-text patterns generally, not as additional live-V2 history):

| Metric | Count | % |
|---|---:|---:|
| Records with some multi-person text signal (numbered/A-code/`&`/bare `and`/comma) | ~150–200 | ~0.3–0.4% |
| ...of which high-confidence (auto-split-eligible) | 13 | 0.02% |
| ...of which low-confidence (flagged, never auto-split) | 138 | 0.27% |
| Records with the spelled-out word `"Alias"`/`"AKA"` | 127 | 0.25% |
| Records with a placeholder/invalid literal as the whole name (`"Unknown"`, `"N/A"`, etc.) | 173 | 0.34% |

---

## F. Internal document references used as cross-checked prior evidence

All six read in full or in the sections cited; treated as the project's own verified prior work, cross-checked against this audit's independent live-DB queries wherever a number could be re-derived (all re-derived numbers matched):

- `DOPAMS_ETL3_NAME_CLEANING_EDGE_CASE_AUDIT.md` — §0 (provenance: legacy export, not live V2), §2 (dataset stats), §3 (edge-case catalogue, incl. the task's own example as real record S.No. 1), §4 (multi-person detection matrix), §11 (final assessment / dataset-wide counts used in Appendix E).
- `DOPAMS_ETL3_NAME_CLEANING_VALIDATION_REPORT.md` — §3 (full 51,123-record validation run results), §5.1 (confirmation that migration 018 was already live before this engagement, with 19 children/8 parents as of 2026-10-24).
- `DOPAMS_ETL3_ETL_INTEGRATION_REPORT.md` — confirms the regular daily pipeline and the standalone backfill call the identical, single implementation of name-cleaning/split logic (no second/parallel implementation exists).
- `DOPAMS_ETL3_NAME_ENRICHMENT_BACKFILL_REPORT.md` — §4.2, the 2026-11-18 dry-run that identified the 5 genuinely-new split candidates (including FIR 869/2026) before they were executed.
- `DOPAMS_ETL3_FINAL_DEPLOYMENT_REPORT.md` — §1 (production server on an older, pre-this-feature commit; confirms the live DB, not a copy, was used), §5 (13 parents/32 children post-execution, matching this audit's independent count).
- `DOPAMS_ETL3_FINAL_VALIDATION_REPORT.md` — §4–5 (idempotency and integrity re-checks, PK-collision counts matching this audit's independent re-query).

---

## G. Methodology note

All counts in this appendix were produced by direct, read-only SQL (`SELECT` only) against `dopams_cctns_v2`, via this repository's own `etl3.db.connections.get_unified_connection(readonly=True)` — the same connection helper, with the same Postgres-level read-only session enforcement and database-identity assertion, that this codebase's own internal audits and diagnostics already use. No query wrote, altered, or deleted any row. No migration was run. No ETL job was triggered. Ad hoc query scripts used for this investigation are scratch files outside this repository and are not part of the deliverable.
