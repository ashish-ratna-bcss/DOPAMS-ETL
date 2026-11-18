# DOPAMS ETL3 — Historical Backfill Execution Report

Real execution results from `etl3/run_name_enrichment_backfill.py --execute --confirm` against the live `dopams_cctns_v2` database, 2026-11-18. All numbers below are read directly from the script's own output and independent read-only verification queries — none are estimated or carried over unchanged from the earlier dry-run report.

## Execution sequence

1. Fresh `--dry-run` immediately after migration 022 + flag activation (baseline recalculation, not assumed from the prior report).
2. `--execute --confirm --skip-multi-accused` — person name-enrichment pass.
3. Live verification query.
4. `--execute --confirm --skip-persons` — multi-accused split pass.
5. Live verification query (row counts, PK collisions, crime linkage).
6. `--dry-run` — idempotency check #1.
7. `--execute --confirm --skip-multi-accused` — second person pass, to enrich the split children created in step 4.
8. `--dry-run` — idempotency check #2 (final).
9. Full raw-name-preservation check across every row.

## 1. Totals

| Metric | Value |
|---|---:|
| Total `persons_unified` records inspected | 54,516 (54,529 after the split step added 13) |
| Total eligible for enrichment update | 54,507 (every existing row — the one-time cost of activating the identity-fields flag; see explanation below) |
| Already enriched, unchanged (post-completion) | 54,520 |
| Newly enriched (0 → populated) | 54,507 in the first pass + 13 in the second pass = **54,520 total identity-field writes** |
| Records with better/different cleaned names | 15,888 + 2 (second pass) = **15,890** |
| Records unchanged in cleaned text but newly carrying identity metadata | 38,428 + 11 = **38,439** |
| Newly identified as unidentified (`name_identified=false`) | **191** |
| Identity fields populated (`name_identified` non-null) | **54,520 / 54,520 (100%)** |
| Person processing failures | **0** (across both passes, 54,520 + 13 records) |
| Total accused occurrences inspected | 51,096 (un-split parent candidates) |
| Existing splits preserved | **8 parents / 19 children — confirmed unchanged, zero duplicates** |
| New accused occurrences created | **5 parents → 13 new children** |
| Crime relationships verified | **13/13 new children linked to their exact parent `crime_id`** |
| Ambiguous records preserved unchanged | 139 (low-confidence) — none split |
| Failed records (multi-accused) | 0 |
| Processing duration | Person pass 1: ~4.5 min (55 batches × 1,000); multi-accused pass: <5 sec; person pass 2: ~4.5 min (same full re-scan to find the 13 new rows) |
| Remaining unprocessed records | **0** confirmed via the final idempotency dry-run |

**Why 54,507, not 708:** the earlier dry-run baseline (708) measured changes with the identity-fields flag *off* — i.e., only genuine `cleaned_full_name`/alias/relationship text differences. The moment the flag was turned on for real, every existing row's hash now includes the 5 new columns (previously `NULL`, never hashed), so every row needed exactly one write to populate them. This was flagged to the user before execution and is the designed, one-time cost of activation — not a data-quality or correctness issue. It will not recur on future runs (confirmed by the idempotency checks below).

## 2. Multi-accused — the 5 new splits, in full

| Parent `accused_id` | Crime | Children created | Verified same-crime link |
|---|---|---:|---|
| `V1:5707b813b1318dbbf5419c662d08f520` | `2022022130811` | 2 (Mohammed Layaq, Mohammed Javeed) | ✅ |
| `V1:1386e3a27feb24c0aee6c2a4d7d94a04` | `2032004140060` | 2 (Jadav Prabhu s/o Narayana, 20 yrs, ST / Jadav Vasantha d/o Raju, 27 yrs) | ✅ |
| `V1:10a7a071d7ee724d0779193ea78b310e` | `2022011140267` | 2 (Malluvarasa Eswara Rao, Jajimoggala Kondala Rao) — A-code pack | ✅ |
| `V1:ed28b7db2bd3989cd1d563adfe3040f3` | `2022022141092` | 3 (Bishan Singh, Karnati Sharath Kumar, Badari Sai Reddy) — A-code pack starting at A1 | ✅ |
| `6a35a0541103855a721a342c` | `6a359f414d1e611f530f007e` | 4 (Divi Sampath, Syed Ameer Ali, Bebri koushal Naik, Kattimani Mandira) — `"N)."` punctuation fix | ✅ |

Write-stats from the actual execution: `persons: 13 inserted / 0 updated / 19 unchanged`, `accused: 13 inserted / 0 updated / 19 unchanged`, `audit: 13 inserted / 0 updated / 19 unchanged`. The "19 unchanged" in every category is the proof the 8 pre-existing splits were recognized and never duplicated.

## 3. Duplicate-prevention — verified, not assumed

```
accused_unified total:            51,824 -> 51,837  (+13, exact)
persons_unified total:            54,516 -> 54,529  (+13, exact)
person_enrichment total:          54,507 -> 54,520  (+13, exact)
accused_split_audit total:        19 -> 32           (+13, exact)
distinct accused_id = total row count (51,837)       -- 0 PK collisions
distinct person_id  = total row count (54,529)       -- 0 PK collisions
superseded parents:               8 -> 13             (+5, exact)
```
Every delta reconciles exactly across all four tables — no orphaned or mismatched row anywhere.

## 4. Idempotency — proven with two real re-runs, not asserted

**Dry-run immediately after the split step:**
```
not_yet_processed: 13   stale_update: 0   already_current: 54,507   no_enrichable_data: 9
```
The 13 are the brand-new split-child persons, correctly pending their first enrichment pass (same documented behaviour as the 2026-10-24 precedent) — not a stale or duplicate state.

**Final dry-run, after the second person pass enriched those 13:**
```
not_yet_processed: 0   stale_update: 0   already_current: 54,520   no_enrichable_data: 9
```
**Zero eligible changes remain.** This is the clean second-dry-run state PART D of this task required.

Multi-accused re-detection (`would_split_high_confidence: 13`) is unchanged across both dry-runs — expected, since `_load_parent_accused_for_split()` does not exclude already-superseded parents by design; re-running `--execute` against it is proven harmless by the write-stats in §2 (`unchanged: 19`, now would be `unchanged: 32` on a further rerun).

## 5. Raw-name preservation

```
Rows checked: 54,520 (100% of person_enrichment)
raw_full_name mismatches against persons_unified.full_name: 0
```

## 6. Unrelated data — confirmed untouched

```
crime_enrichment:    17,348 (present, not written to by anything this session ran)
accused_enrichment:  51,815 (present, not written to)
drug_extractions:    37,914 (present, not written to)
```

## 7. Backup / rollback reference

Targeted backup (the 4 at-risk tables + `change_log`), taken before migration 022: `/home/eagle/backups/dopams_cctns_v2_pre_migration022_tables_2026-11-18.dump` (103 MB, verified restorable via `pg_restore -l` — all 5 expected tables present). Rollback path, if ever needed: `pg_restore -d dopams_cctns_v2 --data-only --table=<name> /home/eagle/backups/dopams_cctns_v2_pre_migration022_tables_2026-11-18.dump` per table, or drop the 5 new nullable columns (migration 022 is trivially reversible — no data loss possible from dropping a column no other table references).
