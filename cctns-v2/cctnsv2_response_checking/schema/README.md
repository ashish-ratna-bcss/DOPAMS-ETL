# CCTNS V2 — Live ETL Schema (ground truth)

`cctnsv2_live_schema.sql` is a **structure-only** dump (`pg_dump --schema-only`)
of the actual `cctns-v2` PostgreSQL database on `dopams-new`
(192.168.103.106) — the database an ETL already populated from the DOPAMS
V2 API. No data, just table/column/constraint/index definitions.

This is the authoritative schema for CCTNS V2 — it reflects what the
existing ETL actually built, not a proposed design. (The earlier
hand-designed merge schema lives separately at
`../../cctns/schema/schema.sql` and is a different artifact: a proposed
*unified V1+V2* model, not this one.)

## Tables (17)

| Table | Purpose |
|---|---|
| `crimes` | FIR/crime master |
| `persons` | Person 360 (name, address, contact, physical) |
| `accused` | Crime↔person junction, accused-specific fields |
| `arrests` | Arrest status, 41A CrPC compliance |
| `mo_seizures` | Material-object seizures (drugs, GPS, media) |
| `chargesheets` | Chargesheet + accused particulars + acts/sections |
| `charge_sheet_updates` | Court "taken on file" trial updates |
| `disposal` | Case disposal records |
| `properties` | Seized property details |
| `fsl_case_property` | FSL/CPR case property (schema present, **currently empty** — see validation note below) |
| `interrogation_reports` | Full interrogation record, 124 columns (nested arrays flattened) |
| `hierarchy` | Police station organisational hierarchy |
| `file_media_bookkeeping` | Media/file attachment references |
| `etl_bookkeeping` | Consolidated ETL state: checkpoints, run watermarks, FK-retry queue, and per-record failure log (`kind` column distinguishes the four) — replaces four legacy tables (`etl_checkpoint`, `etl_run_state`, `etl_fk_retry_queue`, `etl_address_failures`) |
| `etl_run_state` | Legacy/superseded standalone table, **intentionally empty** — its role was absorbed into `etl_bookkeeping.kind='run_state'` |
| `geo_reference` / `geo_countries` | Static India geo reference data used by the address/domicile resolver. **Not sourced from CCTNS** — loaded/maintained separately, so empty ≠ a DOPAMS ingestion gap |

Two other consolidations worth knowing about (from the table comments in the
dump): `interrogation_reports` replaces 23 former `ir_*` child tables, and
`file_media_bookkeeping` replaces 7 former per-entity media tables (`files`,
`property_media`, `mo_seizure_media`, `chargesheet_files`, `chargesheet_media`,
`fsl_case_property_media`, `ir_media`). This is a genuinely well-thought-out
schema, not a naive first pass.

## Known gaps (validated 2026-09-28)

- `fsl_case_property` has the correct schema but **0 rows** — Case Property/FSL
  data from `/api/DOPAMS/case-property` was never loaded despite its crimes
  being present in `crimes`. This is the one confirmed real data-loss bug
  (everything else above is either by design or out of current scope).
- Three DOPAMS "Reports" endpoints (`missing-udb-persons`, `arrest-particulars`,
  citizen-portal `arrest-particulars`) have **no destination table** in this
  schema at all.
- 1,112 unresolved address-enrichment failures sit in `etl_bookkeeping`
  (`kind='failure'`, `reason = kb_and_llm_rejected`).

## Refreshing this dump

```bash
ssh dopams-new "PGPASSWORD='<password>' pg_dump -U dopams_bcss -h localhost -d 'cctns-v2' --schema-only --no-owner --no-privileges" > cctnsv2_live_schema.sql
```
