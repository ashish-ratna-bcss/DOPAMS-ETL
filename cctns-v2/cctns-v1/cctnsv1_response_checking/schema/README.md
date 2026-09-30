# CCTNS V1 — Live ETL Schema (ground truth)

`cctnsv1_live_schema.sql` is a **structure-only** dump (`pg_dump --schema-only`)
of the actual `cctns_v1` PostgreSQL database on `dopams-new`
(192.168.103.106), cleaned of pg_dump boilerplate the same way as the V2
schema in `../../cctnsv2/schema/`.

## Tables (4) — row counts as of 2026-09-28

| Table | Rows | Purpose |
|---|---|---|
| `cctns_fir` | 7,305 | FIR master. Primary key is `fir_reg_num` — everything else hangs off this. |
| `cctns_accused` | 34,377 | Wide flat "dossier" table: FIR summary + accused personal/physical details + all 60 `INT_*` interrogation-relative columns (father/mother/wife/son/daughter/brother/sister/FIL/MIL/uncle/aunt/friend × 5 fields each), unnormalized. |
| `cctns_accused_details` | 20,197 | A second, slimmer accused table (subset of the same fields as `cctns_accused` plus `person_code`) — not clearly a superset or subset of `cctns_accused`; looks like two separate ingestion passes/sources rather than one normalized accused table. |
| `cctns_court` | 7,660 | Chargesheet/court disposal info per FIR. |

## Design notes

- **No normalization at all.** Unlike the V2 schema (which consolidated
  arrays/child-records into structured array/JSONB columns), V1 keeps every
  field flat on the row — including the 60 interrogation-relative columns
  (`int_father_name`, `int_mother_name`, ... one full set of 5 columns per
  relation type) exactly as the original CCTNS V1 system stored them. This
  matches what `CCTNS_V1_vs_V2_Column_Comparison_Report.pdf` described.
- **IDs are `bigint`** (`accused_id`, `court_id`) or plain `varchar`
  (`fir_reg_num`) — ordinary auto-increment/sequence style, unlike V2's
  Mongo ObjectId strings. See prior discussion on why V1 and V2 IDs can
  never be joined directly.
- **Everything foreign-keys to `cctns_fir.fir_reg_num`** with `ON DELETE
  CASCADE` — `cctns_fir` is the only real "parent" table; `cctns_accused`,
  `cctns_accused_details`, and `cctns_court` are independent children of it,
  not of each other.
- `cctns_accused` and `cctns_accused_details` are **not linked to each
  other** — both only reference `cctns_fir`. If the same accused person
  appears in both tables, there's no FK connecting those two rows; matching
  them (if needed) would have to go through `fir_reg_num` + name/mobile,
  same caveat as cross-referencing V1 to V2.

## Refreshing this dump

```bash
ssh dopams-new "PGPASSWORD='<password>' pg_dump -U dopams_bcss -h localhost -d 'cctns_v1' --schema-only --no-owner --no-privileges" > cctnsv1_live_schema.sql
```
