# ETL-3 Phase 0 — Environment and Source Discovery

Phase 0 of `ETL3_IMPLEMENTATION_PHASES.md`. Read-only against V1/V2, as required. This document is the acceptance gate for Phase 1 — **one genuine blocker was found and is called out explicitly rather than worked around** (§6).

**As-of:** 2026-10-01, ~21:15 local. Repo: `D:\Dopams\DOPAMS-ETL-dopams-cctns`, branch `dopams-cctns`, clean except the untracked design docs from this same planning effort. V1 `cctns-v1` branch HEAD `179fb2a`, V2 `cctns-v2` branch HEAD `a05f6c5` — unchanged since the last check this session.

---

## 1. Source DBs

| | V1 | V2 |
|---|---|---|
| Host | `192.168.103.106` (same physical cluster as V2 — confirmed via identical `pg_roles` listing from both connections) | `192.168.103.106` |
| Database | `cctns_v1` | `cctns-v2` |
| Schema | `cctns` (business) + `airflow` (metadata) | `public` |
| Credentials source | `cctns-v1/CCTNSV1_DAILY_ETL_RUN/.env` | `cctns-v2/.env` |
| Credential privilege level | Unknown/not inspected — these are the V1 ETL's **own** production credentials, which plausibly have write access to `cctns_v1` since that ETL writes to it | Same caveat, for `cctns-v2` |
| How ETL-3 should connect | **Never directly with these credentials.** Must go through a connection wrapper enforcing `default_transaction_read_only=on` + `conn.set_session(readonly=True)` — exactly the pattern already proven in this session's own read-only query tooling — or, better, a dedicated read-only DB role (§13 of the implementation plan) | Same |

**Confirmation required by this phase: V1/V2 are read-only from ETL-3 — YES, by construction.** Every query this entire planning effort has run against V1/V2 (dozens of sessions' worth) went through a wrapper that forces the Postgres session itself into read-only mode, not just an application-level promise. ETL-3's source adapters must use the same enforcement, not merely "be careful."

---

## 2. Destination DB

**`dopams_cctns` does not exist yet.** No connection string, `.env` file, hostname, or credential for it exists anywhere in this repository — confirmed by a full-repo search for the strings `dopams_cctns`/`dopams_unified` outside the markdown design docs themselves, and a full-repo search for any `.env*` file beyond the two already known (V1's and V2's). This is the blocker detailed in §6.

---

## 3. Source tables, incremental signals, provenance availability

Already fully catalogued, not repeated here — see `ETL3_SOURCE_COMPATIBILITY_MATRIX.md` for the complete table-by-table breakdown. Summary:

- **V1**: no per-row run-id on any business table. Incremental detection requires a join through `cctns_v1_etl_row_action(run_id, table_name, record_key, action_at)` back to the business table's PK. Confirmed again this phase: `cctns_v1_etl_row_action` = 69,539 rows, 56 runs in `cctns_v1_etl_run_log`, 0 rows currently `status='running'` (source is quiescent, safe to baseline against).
- **V2**: all 14 business tables carry `etl_run_id`/`fetched_at`/`source_system`/`source_endpoint` directly. Confirmed again this phase via the same baseline queries — counts unchanged from the prior check, no drift.

---

## 4. Baseline counts (this phase's snapshot)

**V1** (`cctns_v1`, schema `cctns`):

| Table | Rows |
|---|---|
| `cctns_fir` | 7,305 |
| `cctns_accused` | 34,384 |
| `cctns_accused_details` | 20,198 |
| `cctns_court` | 7,531 |
| `cctns_v1_etl_run_log` | 56 |
| `cctns_v1_etl_row_action` | 69,539 |
| `cctns_v1_failed_fetch_window` | 180 (all OPEN) |

**V2** (`cctns-v2`, schema `public`):

| Table | Rows |
|---|---|
| `crimes` | 9,535 |
| `accused` | 32,867 |
| `persons` | 32,790 |
| `arrests` | 32,862 |
| `chargesheets` | 7,061 |
| `charge_sheet_updates` | 6,163 |
| `disposal` | 470 |
| `mo_seizures` | 3,534 |
| `properties` | 7,646 |
| `fsl_case_property` | 2,003 |
| `interrogation_reports` | 19,497 |
| `hierarchy` | 816 |
| `file_media_bookkeeping` | 165,864 |

All counts identical to the previous snapshot taken earlier this session — the environment is stable, nothing drifted between design and this discovery pass.

---

## 5. Unresolved compatibility issues

Carried from `ETL3_RISK_REGISTER.md`, not re-litigated here: V1 stale-duplicate churn (R7/R11), V2 NULL-`person_id` rows (R1 context), V2's largest FK-retry backlog in `fsl_case_property` (R14), `file_media_bookkeeping`'s missing business timestamps (R13, resolved at design level — known, not blocking). None of these block Phase 0→Phase 1 progression; they're handled in the design already reviewed.

---

## 6. Blocker — read before proceeding to Phase 1

**Phase 1 requires creating a new database (`dopams_cctns`) on the live Postgres cluster and then writing to it. This session has no credentials capable of either action.**

Everything used throughout this entire planning effort — `q.py`/`batch.py` and the `.env` files they read — is deliberately, structurally read-only: the connection itself is forced into a read-only Postgres session (`default_transaction_read_only=on` + `conn.set_session(readonly=True)`), and a client-side regex additionally blocks any `CREATE`/`INSERT`/`UPDATE`/`GRANT`/etc. statement before it's even sent. This was a safety measure established at the very start of this effort precisely so that months of read-only audit work could never accidentally mutate production — and it means, as a direct consequence, that **I cannot create `dopams_cctns`, cannot grant roles, and cannot write to it, with anything currently available in this environment.**

This is a database-provisioning action on a shared production cluster (the same one hosting live `cctns_v1`/`cctns-v2` traffic) — exactly the category of hard-to-reverse, shared-system action that calls for explicit authorization rather than me sourcing or guessing at elevated credentials on my own. I won't attempt to use the V1/V2 ETLs' own credentials for this even though they likely have sufficient privilege on their own databases, both because that's a different database (`dopams_cctns`, not `cctns_v1`/`cctns-v2`) their credentials may not even reach, and because repurposing a source ETL's production credentials for a new, unrelated database is exactly the kind of boundary-blurring the whole "ETL-3 must never be able to write to V1/V2" design principle (§13 of the implementation plan, §3 of this prompt) exists to prevent.

**What I need from you to proceed past this point:**
- Either a connection string / `.env` for a role that can `CREATE DATABASE` (or an already-created empty `dopams_cctns` database plus a role with full DML rights on it), which I'd keep in the same pattern as the existing `cctns-v1`/`cctns-v2` `.env` files, **not** committed to git; or
- A decision to have me build the full ETL-3 codebase and the `dopams_cctns` migration (schema creation + each phase's logic) ready to run, without me executing anything against the live server myself — someone with provisioning access runs `createdb`/applies the migration as a deploy step, the same way the original `schema.sql` was always a design artifact rather than something I ran.

Phase 0 is otherwise complete and found no other blocker. I'm not proceeding to Phase 1 (schema creation) or writing ETL-3 application code against a live target until this is resolved, per the explicit instruction to stop at a boundary like this and report it rather than invent a way around it.
