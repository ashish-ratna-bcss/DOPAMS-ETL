# CCTNS Unified Schema (V1 + V2 merge) — v2

`schema.sql` is a PostgreSQL DDL file that merges the real, live CCTNS V1 and
V2 databases into one model. This is **v2 of this file** — rewritten after
pulling the actual live schemas (`../../cctnsv1/schema/cctnsv1_live_schema.sql`
and `../../cctnsv2/schema/cctnsv2_live_schema.sql`) instead of relying on the
V1↔V2 comparison PDF alone.

## What changed from the first version

The first version of this file guessed at how to normalize V2's data
(separate child tables for chargesheet acts, seizure items, etc.). Pulling
the live V2 database showed a different, already-proven design: **repeating
structured data is stored as parallel PostgreSQL arrays on the parent row**,
not child tables (e.g. `chargesheets.acts_sections text[]`,
`interrogation_reports.family_history_relations text[]`). This version
follows that same pattern, so V1's flat, fixed-count data (12 relation
groups × 5 fields, 9 identity proofs) is folded into arrays too, instead of
being split into separate normalized tables.

## Key decisions

- **Traceability**: `crimes`, `persons`, `accused` carry `source_system`
  (`V1`/`V2`) and `source_record_id` (the original `fir_reg_num` / `crime_id`
  / `accused_id` / `person_id`), so every merged row traces back to its
  source database and row.
- **IDs are `VARCHAR(50)`** — V1 uses `bigint`/plain `varchar` sequence IDs,
  V2 uses 24-char Mongo ObjectId strings. Confirmed by both live schemas;
  see the earlier discussion on why the two ID schemes can never be joined
  directly (different DB technology, no shared mapping — matching a person
  across systems has to go through name/DOB/mobile, not ID).
- **Arrays over child tables** for repeating data, matching V2's own proven
  design: `family_associates` (12 V1 relation-groups + V2's family/associate
  arrays), `chargesheets.acts_*[]`/`accused_*[]`.
- **Fixed-shape data stays as named columns**, also matching V2: physical
  features/deformities have a known, closed set of types in both V1 and V2,
  so they're columns (`physical_features`) or a small enum-checked child
  table (`physical_deformities`), not arrays.
- **V1's single flat "dossier" row is split** across `crimes`, `persons`,
  `accused`, `mo_seizures`, `chargesheets` — V1's `cctns_accused` table packs
  crime + person + accused + drug-seizure fields into one wide row per
  accused; this schema separates those concerns the way V2 already does.

## Table groups

| Group | Tables |
|---|---|
| Crime/FIR | `crimes` |
| Person 360 | `persons`, `identity_details`, `physical_features`, `physical_deformities`, `family_associates` |
| Accused/arrest | `accused` |
| Seizures | `mo_seizures` |
| Court | `chargesheets` |

## Explicitly out of scope (stay V2-only, not duplicated here)

`hierarchy`, `file_media_bookkeeping`, `etl_bookkeeping`, `etl_run_state`,
`geo_reference`, `geo_countries`, `disposal`, `fsl_case_property`, and most
of `interrogation_reports` (financial/telecom/consumer/drug-supply-chain
arrays). These are either V2 ETL operational infrastructure with no V1
counterpart at all, or V2-exclusive intelligence modules V1 never captured
anything equivalent to. Duplicating them into a "unified" schema would just
mean V1 rows are permanently `NULL` there — better to keep them as V2-only
tables (already live in `cctns-v2`) and have this unified schema hold only
what both systems genuinely have overlapping data for. Extend this file if
a real V1-side need for one of them shows up later.

## Loading data

Apply the schema:

```bash
psql -d cctns -f schema.sql
```

When migrating raw V1/V2 data into these tables, always set `source_system`
and `source_record_id` first. For V1, one `cctns_accused` row must be split
into a `crimes` row (dedup on `fir_reg_num`), a `persons` row, an `accused`
row, and (if drug fields are populated) an `mo_seizures` row — plus resolving
whether that same real person already has a row in `persons` from another
V1 FIR or from V2 (match on name + DOB + father's name + mobile, per
`CCTNS_V1_vs_V2_Column_Comparison_Report.pdf`'s "Direct Column Mapping").
