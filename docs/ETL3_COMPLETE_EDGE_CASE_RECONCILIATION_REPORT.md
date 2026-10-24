# ETL3 Edge-Case Fix Reconciliation Report

**2026-10-24 update:** the domicile/nationality precedence work and
Muscat/Oman mapping that followed this report (fix D below is unrelated
to it) have since been backfilled to production (27 rows) and the
multi-accused split has been activated and executed — see
`ETL3_DOMICILE_RECONCILIATION_REPORT.md` and
`ETL3_MULTI_ACCUSED_SPLIT_READINESS_REPORT.md` for that evidence. Nothing
in the scope of *this* report (fixes A/B/C below) changed in that
session; all three remain fully reconciled at 0 discrepancies as
described here.

Covers the three historical fixes that are not multi-accused splitting:
(A) deterministic fuzzy address matching, (B) name cleanup/deduplication,
(C) nationality/domicile false-positive correction. Every number below was
recomputed in this session by re-running the real production functions
against the live `dopams_cctns_v2` database, not assumed from prior
session claims.

## Scope and method

All three fixes land in the single `person_enrichment` table via
`etl3.enrichment.project.person_row()`. Verification method: walk every
`persons_unified` row, call the real, unmodified `person_row()` on it,
compute the same `input_hash` the production upsert path uses, and compare
against the stored `person_enrichment` row's hash. This is a full-table
comparison, not a sample.

```
persons_unified rows walked:                 53197
stored person_enrichment rows:                53182
matched (hash identical, unchanged):          53182
hash differs (needs review):                      0
recompute produced row but no stored row:          0  (orphan/new)
stored person_ids not found in the walk:           0
```

Run twice independently in this session (fresh each time, no cached
results reused) with identical output both times.

### What the 15-row gap is

`53197 - 53182 = 15` persons_unified rows produce no `person_enrichment`
row when recomputed — this is `person_row()`'s own, pre-existing, documented
behavior for rows with insufficient source data (e.g., no name and no
address to enrich), not a defect introduced by any of the three fixes. It
is unchanged from before the fixes and is not a reconciliation gap: those
15 rows correctly have no stored row either.

## Fix A — deterministic fuzzy address tie-breaking (commit `e3c9ff0`)

`etl3/enrichment/address.py`'s `_fuzzy_district`, `_fuzzy_district_any`,
`_fuzzy_mandal`, `_fuzzy_village_mandal` previously iterated a raw `set()`
of tied-trigram-score candidates, whose order depends on `PYTHONHASHSEED`
and therefore is not reproducible across process restarts. Fixed to use
`sorted()`.

- **Regression coverage:** `test_fuzzy_mandal_tie_break_is_deterministic`
  in `etl3/tests/test_enrichment.py` — passes.
- **Historical data impact:** one row was identified as affected by the
  pre-fix non-determinism (`person_id` redacted here; retained in the
  protected operational log, not in this report — village resolved to
  Dongargarh instead of the correct Dongargaon). It was corrected via the
  standard production write path (`upsert_rows`, `enrichment_run_log`,
  `change_log`), not a raw UPDATE.
- **Current state:** full-table reconciliation above confirms this row,
  and every other row, now matches what the deterministic function
  produces. Zero outstanding discrepancies.

## Fix B — name cleanup / deduplication (commit `d57f727`)

`etl3/enrichment/names.py`'s `clean_person_names()` /
`collapse_exact_repetition()`: collapses exact whole-string repetition in
`full_name` only (e.g. "China China" → "China"; never applied to
`given_name`/`surname`, since those can legitimately repeat — e.g. a real
surname "Dung Dung"), case-insensitive with first-occurrence casing kept,
trims dangling edge hyphens, handles `@`-alias and `s/o`/`d/o`/`w/o`
relative-name extraction.

- **Regression coverage:** `test_name_cleanup_for_v1_and_v2`,
  `test_collapse_exact_repetition_confirmed_real_records`,
  `test_collapse_exact_repetition_never_touches_legitimate_names`,
  `test_collapse_exact_repetition_idempotent_and_edge_inputs`,
  `test_duplicate_name_full_pipeline_preserves_raw_and_other_fields`,
  `test_trailing_hyphen_residue_trimmed`, `test_replay_hash_stable` — all
  pass.
- **Historical data impact:** 341 rows were reprocessed through the fixed
  cleaner in a prior session.
- **Current state:** full-table reconciliation above confirms all 341 (and
  every other row) now match the current cleaner's output exactly. Zero
  outstanding discrepancies.

## Fix C — nationality/domicile false-positive correction (commit `c4516a0`)

`etl3/enrichment/address.py`'s `resolve_name_country()`: previously any
bare collision between a name token and a country name in the geo-KB
(e.g. "Mali", "Israel", "Sudan", "China" — all common Indian
surnames/given names) resolved to that country. Fixed to require an
explicit marker (parenthesis, comma, dash, or the word "of") before a
name token is read as a nationality.

- **Regression coverage:** covered inside
  `test_address_kb_confirms_known_geography_only` in
  `etl3/tests/test_enrichment.py` — passes.
- **Historical data impact:** of 30 live rows with
  `domicile_method='name_country'`, 18 (60%) were false positives from the
  bare-collision bug and were corrected; 9 remain as genuine
  marker-qualified nationality resolutions (correctly left alone).
- **Current state:** full-table reconciliation above confirms all 18
  corrected rows (and every other row) now match the fixed resolver's
  output exactly. Zero outstanding discrepancies.

## Deployment status (all three fixes)

| | Local repo | GitHub (`origin/cctns-ai`) | Server (`dopams-etl`) deployed HEAD |
|---|---|---|---|
| Fix A (`e3c9ff0`) | present | present | present — confirmed identical, 0 ahead/behind |
| Fix B (`d57f727`) | present | present | present — confirmed identical, 0 ahead/behind |
| Fix C (`c4516a0`) | present | present | present — confirmed identical, 0 ahead/behind |

All three fixes are code-deployed on the server and their effects are
fully reflected in live `person_enrichment` data, confirmed by a fresh,
full-table, zero-discrepancy reconciliation (not a sample, not reused from
an earlier claim).

## Known, pre-existing, out-of-scope test failure

`test_ai_retry_and_success_does_not_invent_on_empty` in
`etl3/tests/test_enrichment.py` fails on a stale assertion
(`failing.calls == 2`) against the real, correct behavior of
`extract_with_retry()`, whose own docstring states that an `"invalid"`
status (schema/field validation failure) is *not* retried — so exactly 1
call is made, not 2. Reproduced directly: `calls made: 1`. This is drug-
extraction retry logic, unrelated to any of fixes A/B/C or to
multi-accused splitting, and is left unfixed as out of scope for this
task.
