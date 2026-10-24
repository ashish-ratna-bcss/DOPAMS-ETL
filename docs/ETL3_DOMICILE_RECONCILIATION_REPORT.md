# Domicile & Nationality Reconciliation Report

## PRODUCTION BACKFILL APPLIED (2026-10-24)

**Explicitly authorized and executed.** All 27 nationality-safe rows
identified below have been written to production `person_enrichment` via
`etl3/diagnostics/_reprocess_nationality_fix_verified.py --commit` — the
same verify-before-commit script, run with its write path enabled instead
of its default dry-run path. Result: `upsert stats: {'inserted': 0,
'updated': 27, 'unchanged': 0}`; the script's own post-write
verification (re-reading from the same, still-open transaction) confirmed
all other 53,155 rows remained byte-identical before committing.
Independently re-verified afterward from a separate connection: row count
unchanged (53182, no inserts — confirming these were corrections, not new
rows), exactly 27 matching `change_log` entries for this run's
`run_id`, and spot-checked individual rows (e.g. the `Muscat` row now
reads `nationality='Oman'`, `domicile_classification='international'`;
an `Africa`-nationality row now reads `nationality='India'`,
`domicile_classification='inter state'`, `domicile_method='deterministic'`)
match the manifest below exactly. The 6 "other" rows (address-resolution
differences) were, as designed, **not** touched by this write.

**Pre-execution safeguard:** a full `pg_dump` backup of `dopams_cctns_v2`
was taken and verified restorable before this write (and before the
subsequent migrations), at
`/home/eagle/backups/dopams_cctns_v2_pre_migration018_020_2026-10-24.sql.gz`
on `dopams-etl`.

Everything below was the pre-execution analysis; it is preserved as-is
and remains the accurate description of what the backfill changed.

---

Covers the domicile precedence rewrite and the Muscat/Oman nationality
mapping (commit `1ef60f8`), verified against the actual current code and
the live `dopams_cctns_v2` database. All identifiers below are real
`person_id` values from `persons_unified`; they are **redacted** in this
report per the personal-identifier policy and retained only in the
protected server-side diagnostic log
(`/tmp/domicile_full_dryrun.log` on `dopams-etl`, produced by
`etl3/diagnostics/_reprocess_nationality_fix_verified.py`), not
reproduced here.

**No production rows were written during this phase.** Every number below
comes from a dry run (`etl3/diagnostics/_reprocess_nationality_fix_verified.py`,
run without `--commit`): it opens a write-capable connection only because
`person_row()` needs one for its KB lookups, computes every candidate row,
compares against a BEFORE snapshot taken on a separate read-only
connection, and rolls back unconditionally before exiting.

**Re-run independently a second time (2026-10-24)**, rather than assuming
the earlier count still holds: identical result in every respect — same
27 nationality-safe rows, same 6 "other" rows, same exact before/after
values for every one of them. Row counts unchanged (53,182
`person_enrichment` / 53,197 `persons_unified`). This confirms the
candidate set is fully stable and reproducible, not stale — no new
source observation has touched any of these specific rows since they
were first flagged (consistent with `change_log` showing each as a
single, never-revised `initial_observation`).

## Current table state

```
person_enrichment rows (BEFORE snapshot): 53182
persons_unified rows (independently confirmed):  53197
```

Matches the figures already established in
`ETL3_COMPLETE_EDGE_CASE_RECONCILIATION_REPORT.md`. The table has not
grown or shrunk since the address/name/nationality fixes were verified
earlier in this project.

## Result summary

```
candidate rows computed:                 53182
nationality-safe changes (see below):        27
other changes (NOT applied, see below):       6
unchanged (not listed by the script):     53149
```

"Nationality-safe" means **only** `nationality`, `domicile_classification`,
and `domicile_method` differ from what is currently stored — every other
tracked column (`surname`, `relation_type`, `gender_source`,
`address_resolution`, all four name fields) is byte-identical. This is
the exact same verify-before-commit classification the script already
used for the original nationality false-positive fix (commit `c4516a0`);
nothing new was invented for this phase.

### Of the 27 nationality-safe rows: what actually changes

| Category | Rows | Domicile label change? |
|---|---|---|
| `Yeman` → `Yemen` (spelling alias) | 7 | No — stays `international`/`nationality` |
| `Yemeni` → `Yemen` (demonym canonicalized) | 5 | No — stays `international`/`nationality` |
| Stored nationality was already `India`, but the OLD address-classification result was `international` (present-slot foreign evidence was incorrectly allowed to override a usable permanent slot) | 2 | **Yes** — both become `inter state`, from the permanent-address-first precedence rule now correctly applied |
| `West Indies` → `India` (region no longer treated as a foreign nationality; the real Indian address underneath now decides domicile) | 3 | **Yes** — 2 become `inter state`, 1 becomes `native state` |
| `Africa` → `India` or → `None` (continent no longer treated as a foreign nationality) | 2 | **Yes** — 1 becomes `inter state`, 1 becomes `null`/`non_recoverable` (no usable address underneath) |
| `Russian Federation` → `None` (exact phrase not in the alias map — only `russian`/`russia` are — falls through to the kept stored V2 label, which happens to also be `international`) | 2 | No — stays `international`, method changes from `nationality` to `source_column` |
| `Tanzania, United Republic Of` → `Tanzania` (leading-clause canonicalization) | 1 | No — stays `international`/`nationality` |
| `Federation of Nigeria` → `Nigeria` (alias canonicalization) | 1 | No — stays `international`/`nationality` |
| `Muscat` → `Oman` (this phase's new mapping) | 1 | No — stays `international`/`nationality` |
| Stored nationality was `India`, but domicile is correctly `international` from address evidence alone; the nationality field no longer echoes `India` in this precedence branch | 3 | No — domicile stays `international`/`deterministic` |

**Totals: 27 rows, of which 7 have an actual domicile *label* change**
(5 → `inter state`, 1 → `native state`, 1 → `null`), and **20 are pure
nationality-text canonicalization** with the domicile label unchanged.

This is lower than the "7/2/0/1 = 10" figure in the task's historical
background — that figure is explicitly described as historical evidence,
not a guaranteed current count, and this rerun used the actual, fully
deployed current code against the actual current data, not a
reconstruction of an earlier intermediate state. The categories match
(`West Indies`, `Africa`, `Muscat`, `India`, blank/placeholder values) —
this run additionally surfaces the `Yeman`/`Yemeni`/`Tanzania`/`Federation
of Nigeria`/`Russian Federation` canonicalization cases, which do not
change the domicile label and so would not have been counted in a
label-only tally.

### Muscat → Oman: the one row affected

Exactly 1 live row currently has `nationality = 'Muscat'`. Before this
fix it was already stored as `domicile_classification = 'international'`,
`domicile_method = 'nationality'` — unaffected in classification, since
`Muscat` already produced *some* foreign result upstream of this change.
What the fix corrects is the **stored nationality value itself**: it
becomes the canonical country name `'Oman'` instead of the city name
`'Muscat'`, consistent with every other row's nationality column holding
a country, not a sub-national place name. The domicile label and method
are unchanged for this row.

### Africa / West Indies: confirmed not invented into a country

2 rows had `nationality = 'Africa'` and 3 had `nationality = 'West
Indies'`. None of the 5 resulting rows gets a fabricated country —
`resolve_nationality_label('Africa')` and `resolve_nationality_label('West
Indies')` both still return `None` under the current code (confirmed
directly, and re-confirmed against the live 223-row `kb.geo_countries`
table — neither is an exact match and the nationality-field resolver never
fuzzy-matches). The `nationality` column for these rows becomes `'India'`
(when a recognized Indian state is present elsewhere in the record) or
`None` (when it is not) — never a continent or region name standing in
for a country, and never an invented country.

## Rows NOT touched by this phase (6) — address resolution also differs

The script's own safety design excludes any row where `address_resolution`
also differs from what is stored, since that is outside this fix's scope
and needs independent justification. 6 such rows exist. Confirmed via
`git diff e3c9ff0 HEAD -- etl3/enrichment/address.py`: none of
`resolve_address_text`, `_candidate`, `resolve_candidate`,
`_merge_address_hits`, or `resolve_person_address` changed in this
phase's commit — so this phase's code is not the cause.

- **5 of the 6** go from an unresolved slot (`None`) to a resolved one
  (e.g. permanent address text now confirms `Andaman and Nicobar Islands`,
  or `Odisha`, where nothing resolved before). This is consistent with
  filling in previously-missing data, not a regression — but it is still
  left untouched here, exactly as the script's conservative design
  requires, since it is not what this phase set out to fix.
- **1 of the 6** replaces one resolved value with a *different* resolved
  value for both address slots (previously both slots resolved to
  `Maharashtra`; now both resolve to `Jammu and Kashmir`/`Srinagar`). This
  is the one case in this set that is not simply "filling in a blank" and
  would need its own independent investigation (most likely a new source
  observation superseding an older one, since the resolution code itself
  is unchanged) before any correction is considered. **Not applied, not
  further investigated in this phase — flagged for separate review.**

None of these 6 rows were written to by this dry run; all remain exactly
as currently stored.

## Verification performed

- Full-table (53,182 rows), not sampled.
- Read-only BEFORE snapshot on a separate connection from the write
  connection used for recomputation (same pattern as the original
  nationality fix script).
- Every one of the 27 nationality-safe rows was independently confirmed
  to have **only** `nationality`/`domicile_classification`/
  `domicile_method` differing — every other column checked
  byte-for-byte identical.
- `test_enrichment.py` (34 assertions covering domicile precedence,
  Muscat/Oman, Africa/West Indies, state aliases, V2 fallback/replace
  behavior) passes in full on the server's deployed code; only the
  pre-existing, unrelated AI-retry test fails, as documented in the edge-
  case report.
- `test_geography_and_domicile` (the one domicile assertion in
  `test_kb_local.py`) passes against the live knowledge base.
  `test_pg_trgm_and_lookups` in that same file was deliberately **not**
  run, since it writes and deletes a row in `kb.drug_categories` on
  `dopams_cctns_v2` — exactly the kind of write this phase was told to
  avoid running against production.

## Production backfill gate (not yet approved)

Before any production backfill of the 27 nationality-safe rows:

- **Exact count of rows that would change:** 27 (7 with an actual
  domicile-label change, 20 nationality-text-only).
- **Before/after labels and methods:** tabulated above by category: exact
  per-row before/after values are in the protected diagnostic log on the
  server (real `person_id`s, redacted here).
- **Source-value categories affected:** `Yeman`, `Yemeni`, `Tanzania,
  United Republic Of`, `Federation of Nigeria`, `Muscat`, `West Indies`,
  `Africa`, `Russian Federation`, and three rows where a stored `India`
  nationality no longer needs to be echoed because address evidence alone
  already determines the domicile.
- **Evidence for Muscat → Oman:** confirmed against the live 223-row
  `kb.geo_countries` table that `Oman` is a real, present country name and
  that `Muscat` does not fuzzy-match it (best score 0.125 vs. the 0.70
  floor), so the explicit alias is necessary and correctly scoped.
- **Confirmation Africa/West Indies are not assigned invented countries:**
  confirmed above — both still resolve to `None` under the current code;
  the resulting `nationality` value is `India` or `None`, never a region
  name or a fabricated country.
- **Expected consequences for existing V2 labels:** 20 of the 27 rows keep
  their current domicile label and only change text, so there is no
  behavioral change for consumers that read `domicile_classification`.
  7 rows have a corrected label (`international` → `inter state`/`native
  state`/`null`); each is a genuine correction of a previously-wrong
  classification per the precedence rules in
  `docs/ETL3_MAIN_ENRICHMENT_IMPLEMENTATION.md`, not a loss of previously
  accurate information.
- **Rollback and verification plan:** the backfill, if approved, would
  reuse the existing `upsert_rows()` path (writes `change_log` with the
  old and new value for every changed field, classified
  `business_change`), the same audit trail every other enrichment write
  already relies on. Rollback is: read `change_log` for the affected
  `person_id`s and `UPDATE ... SET domicile_classification = old_value,
  domicile_method = old_value, nationality = old_value` from the logged
  `old_value`s — no different from reverting any other enrichment
  correction already made in this project. Re-verification after a
  backfill would be: re-run this same dry-run script immediately after;
  it should report 0 nationality-safe rows remaining (every one of the 27
  now matches what it just wrote).

**Status update (2026-10-24): applied.** This section originally stopped
short of applying the backfill pending approval. Explicit approval was
subsequently given; the backfill was executed and independently
re-verified — see "PRODUCTION BACKFILL APPLIED" at the top of this
report for the full evidence.
