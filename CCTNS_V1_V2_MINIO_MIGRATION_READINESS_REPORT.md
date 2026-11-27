# CCTNS V1/V2 → MinIO: Integration Testing, V2 Backfill & Production Readiness

Date: 2026-11-27
Scope: follow-up to `CCTNS_V1_V2_MINIO_DIRECT_UPLOAD_FEASIBILITY_AUDIT.md` (the feasibility audit) and the
subsequent "fully on MinIO, no more local files" code implementation across V1, V2 and ETL3. This report
covers Phases 1–5 of the integration-testing / backfill / production-readiness task: what was re-audited,
what was fixed, what was genuinely tested from this environment, and what remains blocked by missing
credentials and missing host access.

**Environment this report was produced from:** a Windows development checkout of this repository
(`d:\Dopams\DOPAMS-ETL-dopams-cctns`), with network reachability to the CCTNS/MinIO network segment but
**no production host shell access, no write-capable MinIO credentials, and no filesystem access to
`/mnt/shared-etl-files`**. Every claim below is labeled by how it was actually verified. Per the task's own
rule 16, no result is reported as a successful real-world test unless it genuinely was one.

---

## 0. Headline result

**Status: NOT READY for production cutover.** (Full reasoning in §6.)

One genuine, previously-undetected bug was found and fixed during this pass (§1) — without it, the first
production run of the V2 downloader would have reset every newly-uploaded file back to "not downloaded" and
looped forever. Beyond that fix, the blockers are almost entirely about **missing access from this
environment**, not missing code: no real MinIO credentials exist anywhere in this repository checkout (dev
or production-shaped), and the V2 backlog filesystem (`/mnt/shared-etl-files`) is not reachable from this
machine. Those two gaps block real upload testing, real backlog inventory, and the actual backfill run —
they require someone with production host access to execute the remaining steps using the tooling delivered
here.

---

## 1. Bug found and fixed during this pass

**`cctns-v2/etl-files/sync_files_state.py` was not updated in the earlier implementation pass, and it is a
live, independently-invoked code path — not a dead duplicate.**

- [`main.py:947`](cctns-v2/etl-files/etl_files_media_server/etl_files_media_server/main.py#L947) — the
  actual CLI entrypoint (`main()`, run via `python -m etl_files_media_server.main` or equivalent) does, after
  every download run:
  ```python
  SYNC_SCRIPT_DIR = str(PARENT_DIR.parent)      # .../etl-files/
  sys.path.insert(0, SYNC_SCRIPT_DIR)
  from sync_files_state import run_sync          # <- top-level module, NOT etl_files_media_server.sync_files_state
  run_sync(logger=logger)
  ```
  This is a different import from the one at
  [`main.py:844`](cctns-v2/etl-files/etl_files_media_server/etl_files_media_server/main.py#L844)
  (`from .sync_files_state import sync`), which is the package-relative module I updated earlier. The
  `main()` function's own post-run validation pass uses the **top-level** standalone script
  `cctns-v2/etl-files/sync_files_state.py` instead, via a deliberate `sys.path` insert.
- That top-level script still checked `os.path.exists()` against `FILES_MEDIA_BASE_PATH`
  (`/mnt/shared-etl-files`). Since the downloader no longer writes there at all, every file it had just
  uploaded to MinIO would be reported "missing on disk" immediately afterward, and the script would reset
  `is_downloaded = FALSE` for it — undoing the whole run and guaranteeing endless re-downloads of the same
  files, on every single invocation.
- **Fixed**: rewrote `cctns-v2/etl-files/sync_files_state.py`'s `run_sync()` to check MinIO via
  `get_object_store().find_by_prefix(f"{subdir}/{file_id}.")` (imported from the already-existing
  `etl_files_media_server.object_store` module via a symmetrical `sys.path` insert), matching the logic
  already in the package's own `sync_files_state.py`. `py_compile` passes. This mirrors the codebase's
  existing precedent of maintaining `map_destination_subdir()` as hand-kept duplicates across `main.py`,
  `update_file_urls_with_extensions.py`, and this script, rather than restructuring the package layout.
- **Why this wasn't caught earlier**: the earlier validation (`py_compile`, the existing `etl3` test suite,
  and the standalone smoke test) never exercised `main()`'s actual runtime import path — none of those
  checks import `main.py` and call `main()`, so a second, differently-named `sync_files_state` module sitting
  one directory up was invisible to all of them. This is a real gap in how "validated" was claimed earlier;
  it is called out here rather than glossed over.

No other live-path filesystem dependency was found in this re-sweep. `cctns-v2/etl-files/diagnose_missing_files.py`
(NFS-mount diagnostic, "run this periodically", not imported by anything) is a standalone manual tool in the
same category as V1's `reconcile_v1_media.py`/`share_v1_media.py` — now obsolete for new data, left untouched
per the "never delete existing files/tools" rule. `etl3/diagnostics/*.py` scripts (investigate/remediate/probe,
prefixed `_` or clearly ad-hoc) are not imported by any live `etl3` module — confirmed via repo-wide grep —
and were likewise left alone.

---

## 2. Phase 1 — Configuration matrix (re-verified)

| Component | Required env vars | Present in this dev `.env`? | Notes |
|---|---|---|---|
| CCTNS V1 (`cctns-v1/CCTNSV1_DAILY_ETL_RUN/.env`) | `MINIO_ENDPOINT_URL`, `MINIO_BUCKET`, `MINIO_REGION`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY` | **None set** (all fall back to code defaults / empty) | `config/settings.py:29-33`. Empty `MINIO_ACCESS_KEY`/`SECRET_KEY` means `ObjectStore()` raises `RuntimeError` fail-fast — confirmed by design, not tested against a live run here. |
| CCTNS V2 (`cctns-v2/.env`) | same 5 vars | **None set** | `object_store.py:91-99`. Same fail-fast behavior. |
| ETL3 (`etl3/config/.env` — file doesn't exist yet, only `.env.example`) | same 5 vars, **read/HEAD-scoped only** | **N/A — no real `.env` file present at all** | `etl3/media/paths.py` degrades gracefully: `_minio_enabled()` returns false and it falls back to local-disk checks only, so ETL3 does not hard-fail without these vars — confirmed via existing passing test suite. |
| DOPAMS-BE (`D:\Dopams\Dopams app\DOPAMS-BE\.env`) | **Different convention**: `FILE_SERVER_ACCESS_KEY`, `FILE_SERVER_SECRET_KEY`, `FILE_SERVER_REGION`, `FILE_SERVER_BASE_URL` (`object-store.ts:123-133`) | Only `FILE_SERVER_BASE_URL` set; no access/secret keys | By design this is **not a blocker** for DOPAMS-BE: `fetchObject()` (`object-store.ts:119-137`) falls back to a plain unsigned `fetch()` when credentials are absent, so reads still work if the bucket/path is reachable without signing. **This is a separate credential namespace from the new V1/V2/ETL3 `MINIO_*` vars — provisioning one does not provision the other.** Flagged for Report 3. |

**No valid MinIO write credentials exist anywhere in this repository checkout** — not in a dev `.env`, not in
a production-shaped one, not inherited from DOPAMS-BE's read-only set. This is the single biggest blocker for
Phases 2–3 below.

Dependency versions: `boto3==1.34.162` pinned in V1 and ETL3's `requirements.txt`; `boto3>=1.34.0` (floating)
in V2's two `requirements.txt` files. Locally installed for testing in this session: `boto3` 1.44.12 — newer
than the pin, worked fine for every test run here, but **the pinned/floating versions have not been exercised
together on a real host**; recommend pinning V2 to the same `1.34.162` as the other two trees for consistency,
though this is a minor cleanliness item, not a blocker.

---

## 3. Report 1 — Real MinIO Integration Test Report

| Test | Result | How verified |
|---|---|---|
| MinIO endpoint reachable (TCP) | **PASS** | `socket.create_connection(("192.168.103.106", 9000))` succeeded from this dev machine, just now. |
| MinIO health endpoint | **PASS** | `GET http://192.168.103.106:9000/minio/health/live` → HTTP 200, ~120ms, just now. |
| Bucket `files` exists | **PASS (inferred)** | Unauthenticated `GET http://192.168.103.106:9000/files/` → HTTP 403 `AccessDenied` (not `NoSuchBucket` / `404`) — the bucket exists and correctly requires auth. Confirmed both in the original audit and again just now. |
| Authentication works | **NOT TESTED — no credentials available** | No `MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY` exist anywhere in this checkout (§2). Cannot sign a request without them. |
| Upload (`PutObject`) permitted | **NOT TESTED** | Same credential blocker. |
| HEAD (`HeadObject`) permitted | **NOT TESTED** | Same credential blocker. |
| `ListObjectsV2` permitted | **NOT TESTED** | Same credential blocker. |
| CCTNS V1 source API reachable | **PARTIAL** | `FIR_API_URL`, `COURT_API_URL`, `ACCUSED_API_URL` (from the real V1 `.env`) are all TCP-reachable on port 80 from this machine. `ALFRESCO_DOWNLOAD_API_URL` — the one `alfresco.py:64` actually requires — **is not set in this dev `.env` at all**, so a real Alfresco-to-MinIO download test could not be attempted even with credentials. |
| CCTNS V2 source API reachable | **PASS** | `DOPAMAS_API_URL` (real V2 `.env`) reachable on TCP 3000 from this machine. |
| V2 old file server reachable | **PASS** | `FILES_BASE_URL` (real V2 `.env`) reachable on TCP 8080. (This is the legacy Tomcat endpoint used to build historical `file_url` values — unrelated to MinIO upload/read, noted for completeness.) |
| V1/V2 upload, existing-object detection, invalid-key rejection, V1/V2 collision guard, duplicate handling, pagination behavior | **Logic verified via offline unit/smoke tests only** (`IterStream`, `valid_key`, `assert_v1_key`/`assert_v2_key`, all passing — see prior implementation pass). **Not exercised against a real MinIO server**, because that requires the credentials this environment does not have. |
| DOPAMS end-to-end (upload → proxy → authenticated access → content-type → preview) | **NOT TESTED** | Requires a real uploaded object plus a running DOPAMS-BE instance with its own credentials; neither is available from here. |

**Bottom line for Report 1**: network path to MinIO is live and the bucket exists, exactly as the original
audit found. Nothing beyond that was re-verified against the real server in this pass, because no write (or
even read-scoped) MinIO credential exists anywhere in this repository. This is a credential-provisioning gap,
not a code gap — the upload/HEAD/collision-guard code itself is unit-tested and ready to run the moment a
real credential is supplied.

---

## 4. Report 2 — V2 Media Migration Report

**Blocked at the inventory step.** `FILES_MEDIA_BASE_PATH` (`/mnt/shared-etl-files`) is a Linux NFS mount on
the V2 ETL host; it does not exist on this Windows dev machine (`os.path.isdir()` check fails). No real file
counts, sizes, or per-directory breakdowns can be produced from this environment — producing a table of
"actual" numbers here would mean inventing data, which the task explicitly forbids.

**What was delivered instead**: `cctns-v2/etl-files/backfill_v2_media_to_minio.py` — a new, resumable
backfill utility (no equivalent existed; confirmed by listing every `.py` file under `cctns-v2/etl-files/`).

Design, matching the task's mandatory feature list:

- **Dry-run by default**; `--apply` to actually upload. Dry-run never calls MinIO at all — it only inventories
  the local tree, so it needs no credentials and is safe to run on the production host the moment someone
  with filesystem access wants a real inventory.
- Walks only the 10 known destination subdirs (`crimes`, `person/identitydetails`, `person/media`, `property`,
  `interrogations/media`, `interrogations/interrogationreport`, `interrogations/dopamsdata`, `mo_seizures`,
  `chargesheets`, `fsl_case_property`) — the same set `map_destination_subdir()` produces everywhere else in
  this codebase. Never recurses into anything else under the base path.
- Preserves the existing object-key convention exactly: `<subdir>/<file_id>.<ext>`, taken directly from each
  file's own name on disk — no renaming, no new prefixes, no extension changes.
- Detects and reports (never silently skips): zero-byte files, missing-extension files, duplicate file IDs
  within a subdir (keeps the first, flags the rest for manual review rather than guessing which is canonical).
- Before uploading, checks whether the destination key already exists in MinIO via `HeadObject`; if it does
  and the size matches, skips (resumable, idempotent). **If it exists with a different size, it is never
  overwritten** — flagged as `size_mismatch_existing` for manual investigation, per the task's explicit "do
  not overwrite with different content" rule.
- Content-integrity verification without assuming ETag = MD5: uploads via a single `put_object` call (not
  boto3's automatic multipart transfer), which S3/MinIO guarantee returns an ETag equal to the plain hex MD5
  of the body. The local MD5 is computed before upload and compared against that returned ETag. Any file at
  or above 200MB is **not** auto-uploaded — it's written to `needs_manual_review` instead, specifically
  because a multipart upload's ETag is not a checksum this script can safely verify, and the task explicitly
  warned against assuming otherwise.
- Retries failed uploads (3 attempts, exponential backoff).
- Resumable via a JSON progress file (`--resume`), checkpointed every 50 successful uploads and again at the
  end.
- `--sample N` to upload only the first N eligible files (for the task's required "upload a small sample
  first" step) and `--subdir` to scope a run to one destination directory.
- Final reconciliation written to a JSON report file and printed to the log: discovered / eligible / skipped
  already-valid / uploaded / failed / size-mismatch / checksum-mismatch / needs-manual-review counts, plus
  total source bytes vs. total MinIO-confirmed bytes. Exit code is non-zero if anything failed or mismatched,
  so a successful exit code alone *does* mean every eligible file was confirmed — per the task's own
  "exit code alone is not sufficient proof" rule, the JSON report is the actual proof artifact, not the exit
  code.
- Never deletes, moves, or modifies a source file — read-only against `/mnt/shared-etl-files` throughout.
- Does not touch any database table or row (no DB migration needed: the key convention already matches what
  `generate_file_path()` produces, so once bytes exist in MinIO under that key, nothing in the DB needs to
  change).

**What was actually tested**: the dry-run inventory logic, against a synthetic fixture tree built in this
session's scratchpad (not production data) containing a normal file, a zero-byte file, a no-extension file,
and a duplicate-file-ID pair. Result matched expectations exactly: 7 discovered, 4 eligible, 1 zero-byte, 1
no-extension, 1 duplicate — confirming the classification logic is correct. **The `--apply` upload path,
the checksum-after-upload comparison, and the resume logic were not exercised against a real MinIO server**,
for the same credential reason as Report 1.

**Recommended execution sequence for whoever has V2-host + MinIO-credential access** (matches the task's own
Step 1–9 sequence):
1. `python backfill_v2_media_to_minio.py` (no `--apply`) → review the discovered/eligible/zero-byte/duplicate
   counts in the log and the written JSON report before touching MinIO at all.
2. `python backfill_v2_media_to_minio.py --apply --sample 20` → verify a small batch uploads and
   checksum-verifies cleanly.
3. Review `backfill_v2_reconciliation.json` from that sample run; only then proceed to a full
   `--apply --resume` run (safe to re-run after any interruption — already-uploaded files are skipped via the
   progress file and via MinIO's own HEAD check).
4. Investigate anything landing in `manual_review_items` or `failed_items` before considering the backlog
   migrated.

---

## 5. Report 3 — Production Configuration Report

| Item | CCTNS V1 host | CCTNS V2 host | ETL3 host |
|---|---|---|---|
| Env file location | `cctns-v1/CCTNSV1_DAILY_ETL_RUN/.env` | `cctns-v2/.env` | `etl3/config/.env` (currently only `.env.example` exists in this checkout — no real file to inspect) |
| Required new vars | `MINIO_ENDPOINT_URL`, `MINIO_BUCKET`, `MINIO_REGION`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY` | same 5 | same 5, **read/HEAD-only** credential |
| Credential provisioning status | **Not provisioned** — all empty/unset in the only `.env` this environment can see | **Not provisioned** | **Not provisioned** |
| Recommended permission scope | `PutObject`, `GetObject`/`HeadObject` on prefixes it owns (`FIR/`, `COURT/`) | `PutObject`, `GetObject`/`HeadObject`, `ListBucket`, scoped to the 10 V2 prefixes | `GetObject`/`HeadObject` only — `_minio_enabled()`/`_get_minio_client()` in `etl3/media/paths.py` never calls `PutObject` anywhere |
| Network connectivity (from this dev machine) | Alfresco download URL not configured; FIR/COURT/ACCUSED APIs TCP-reachable | MinIO:9000 and legacy Tomcat:8080 TCP-reachable; source API TCP-reachable | Not independently tested — same MinIO host as V1/V2, expected reachable if on the same segment |
| Service restart required | Yes — new `boto3` dependency, new `storage/` package | Yes — same | Yes — new `boto3` dependency (imported lazily, inside `_get_minio_client()`, so ETL3 won't hard-fail if it's missing, but availability checks silently fall back to disk-only until it's installed) |
| Validated on the actual host | **No — not possible from this environment.** Everything above the TCP-reachability row is a code-level/offline-test claim, not a production verification. |

**DOPAMS-BE note**: its read path uses a separate credential set (`FILE_SERVER_ACCESS_KEY`/`SECRET_KEY`,
different variable names from the new `MINIO_*` ones) and already degrades to unsigned reads when absent.
Provisioning the new V1/V2/ETL3 write/HEAD credentials does **not** automatically give DOPAMS-BE anything —
if DOPAMS-BE's own read access needs tightening at the same time, that is a separate credential to provision,
on its own `.env`, and is out of scope for this ETL-side change.

**Credential separation**: per the task's least-privilege request, provision three (or four, including
DOPAMS-BE's) *separate* MinIO users/policies rather than reusing one administrative key across V1, V2 and
ETL3 — the object-key collision guards already enforce separation in code (`assert_v1_key`/`assert_v2_key`),
but IAM-level separation is what actually limits blast radius if one host's credential leaks.

**Remaining sysadmin actions** (exact, for whoever has MinIO admin + production host access):
1. Create 3 MinIO users/service accounts (or 1 per host if DOPAMS-BE's is also being rotated): `cctns-v1-etl`,
   `cctns-v2-etl`, `etl3-read`.
2. Attach bucket policies scoped to bucket `files`:
   - `cctns-v1-etl`: `s3:PutObject`, `s3:GetObject` on `arn:aws:s3:::files/FIR/*` and `arn:aws:s3:::files/COURT/*`.
   - `cctns-v2-etl`: `s3:PutObject`, `s3:GetObject`, `s3:ListBucket` on the 10 V2 prefixes listed in §4.
   - `etl3-read`: `s3:GetObject` only, bucket-wide (or scoped to all 12 prefixes combined).
3. Set on the V1 host's `.env`: `MINIO_ENDPOINT_URL=http://192.168.103.106:9000`, `MINIO_BUCKET=files`,
   `MINIO_REGION=us-east-1`, `MINIO_ACCESS_KEY=<cctns-v1-etl key>`, `MINIO_SECRET_KEY=<cctns-v1-etl secret>`.
   Also set `ALFRESCO_DOWNLOAD_API_URL` if it is genuinely unset in production too (it is unset in this dev
   copy — confirm on the real host before assuming this is dev-only).
4. Set the same 5 vars (with the `cctns-v2-etl` credential) on the V2 host's `.env`.
5. Set the same 5 vars (with the `etl3-read` credential) on the ETL3 host's `.env` (create one from
   `etl3/config/.env.example` if it doesn't exist yet).
6. `pip install -r requirements.txt` on each of the three hosts to pick up the new `boto3` dependency.
7. Restart each of the three ETL processes/schedulers (Airflow for V1, whatever supervises `main.py` for V2,
   ETL3's own scheduler) so the new code and env vars take effect.
8. Only after the above: run the backfill script (§4) from the V2 host.

---

## 6. Report 4 — Final Production Readiness Assessment

### Status: **NOT READY**

### Evidence for this status

| Condition (from the task's own Phase 5 checklist) | Met? |
|---|---|
| 1. Real MinIO connectivity verified | Partially — network/health/bucket-existence yes; authenticated operations no (§3) |
| 2. V1 direct upload works against actual MinIO | No — no credentials to test with |
| 3. V2 direct upload works against actual MinIO | No — same |
| 4. ETL3 correctly verifies MinIO objects | No — same, though the fallback-to-disk path is unit-tested and will not silently misreport in the interim (confirmed by the existing 12/12 passing test suite) |
| 5. Existing V2 media backfill reconciled | No — tooling built and dry-run logic tested; backlog itself is unreachable from here |
| 6. Existing object keys preserved | Yes, by construction and code review — unchanged in all three systems and in the new backfill script |
| 7. DOPAMS document access verified end-to-end | No — not attempted, no uploaded object exists yet |
| 8. Database status updates correct | Yes, by code review — `upload_and_confirm()`'s HEAD-then-size-check gate is unchanged from the earlier implementation and was the one piece already smoke-tested offline |
| 9. Retry/recovery mechanisms work | Partially — exercised in offline logic tests only, not against real transient MinIO failures |
| 10. No downstream consumer unexpectedly needs old filesystem paths | **No longer fully true until just now** — §1's bug would have been exactly this kind of surprise; now fixed, but this is a reminder that "no downstream consumer" claims need a careful re-sweep, which this pass did (and found one) |

### Why not "READY WITH RESTRICTIONS"

A "ready with restrictions" status would mean the restrictions are narrow and known (e.g., "ready, but only
after the backfill completes"). Here the restriction is broader: **no part of the authenticated MinIO path
has ever been exercised against the real server**, in this environment or (as far as this environment can
tell) in any prior one — every credential slot is empty everywhere this checkout can see. That is a
first-use gap, not a narrowing restriction, so it is reported as NOT READY rather than dressed up as a
restricted go-ahead.

### What turns this into READY

1. Provision the credentials in §5's sysadmin checklist.
2. On the V2 host (which has both the filesystem and, once provisioned, the MinIO credential), run the
   backfill script's dry run, then a 20-file sample, then the full run — per §4's sequence — and resolve
   anything in `manual_review_items`/`failed_items`.
3. On each of the V1 and V2 hosts, run one real ETL cycle in a maintenance window and confirm in the logs
   that `skipped_exists_in_minio`/`downloaded` counts look sane and that the (now-fixed) `sync_files_state`
   pass does not reset anything it shouldn't.
4. Do one real DOPAMS-BE read-through of a freshly uploaded V1 and V2 object each, confirming content-type
   and that the proxy serves it correctly.
5. Only then retire (not delete) the old filesystem sync service, per the task's own Phase 5 rule, and keep
   the original files for the agreed rollback window.

None of step 1–4 can be completed from this environment; they require production host access and real
credentials that this session does not have.

---

## 7. Addendum (2026-11-27, later same day): real-data verification pass

This section covers follow-up work done against real V1/V2/unified databases (read-only credentials for
these — not MinIO — do exist in this checkout's `.env` files, unlike the MinIO credentials in §2).

### 7.1 Bug found and fixed: V1's "COURT/" prefix assumption was wrong

Queried `cctns.cctns_fir`/`cctns.cctns_court` on the real V1 database directly. Result:

- `cctns.cctns_fir.attach_path` really does start with `FIR/` (3,035/3,035 rows) — the existing assumption
  was correct here.
- `cctns.cctns_court.attach_path` starts with **`Investigation/`** (3,908/3,908 rows, e.g.
  `Investigation/CS/ScannedC...`, `Investigation/2023/2023...`) — **not** `COURT/`. `COURT` is only the
  `entity_type` bookkeeping label in `cctns.cctns_media_files`, never a path prefix.
- `cctns-v2/etl-files/etl_files_media_server/etl_files_media_server/object_store.py:38`'s
  `_V1_RESERVED_PREFIXES = ("FIR/", "COURT/")` was therefore checking for a string that never occurs in real
  data — the guard provided **zero actual protection** for every COURT-sourced V1 object (roughly half of
  V1's media).
- **Fixed**: `_V1_RESERVED_PREFIXES = ("FIR/", "Investigation/")`. Re-verified with real key shapes:
  `assert_v2_key("Investigation/CS/ScannedC/123.pdf")` now correctly raises; `assert_v1_key()` still accepts
  it as a valid V1 key; both still behave correctly for `crimes/` and `FIR/` respectively. `py_compile` clean.

Also queried the real V2 database's `file_media_bookkeeping` table for every distinct
`(source_type, source_field)` pair in actual use. All 10 combinations found match
`map_destination_subdir()`'s mapping table exactly — no missing or extra prefix. V2's side of the contract
was already correct; only V1's was wrong.

### 7.2 Bug found and fixed: a MinIO outage could be silently misread as "every file is missing"

Re-read `etl3/media/paths.py`'s `_minio_object_size()`: it caught every exception identically (auth failure,
connection refused, timeout, 5xx — same as a clean 404) and returned `(False, 0)` for all of them, with no
logging. Downstream, `classify_v1_availability`/`classify_v2_availability` treat a `False` result the same
way regardless of *why* — for any file already migrated off local disk, a transient MinIO outage or a bad
credential rotation would silently fall through to the (now-empty) local-disk check and come back
`BOOKKEEPING_DOWNLOADED_INACCESSIBLE`, exactly the failure mode the task flagged as unacceptable. Confirmed
there is no existing circuit-breaker for this (the `_ollama_tags_healthy()` pattern in
`etl3/enrichment/runner.py` was the closest existing precedent, used as the template for the fix).

**Fixed**, minimally:
- Added `minio_health_check()` to `etl3/media/paths.py` — a cheap one-time `head_bucket` probe. Reports
  healthy if MinIO isn't configured at all (intentional degraded mode) or is configured and reachable;
  unhealthy only when configured but the probe fails.
- `etl3/merger/media_consolidate.py::run_media_consolidation()` now calls this once at the very top and
  **raises before touching any row** if it comes back unhealthy — a MinIO outage now aborts the run loudly
  instead of writing a wave of false "inaccessible" statuses.
- `_minio_object_size()` now logs a warning for any HEAD failure that isn't a clean 404/NoSuchKey, so a
  MinIO problem occurring *mid-run* (after the health check passed) is visible in logs instead of silent.
- `py_compile` clean; the existing `etl3/tests/test_media_paths.py` suite still passes 12/12 unchanged.

### 7.3 DOPAMS-BE confirmed already correctly wired to MinIO — not something this task needed to build

Read `D:\Dopams\Dopams app\DOPAMS-BE\src\utils\object-store.ts` and `fileProxy.ts` in full. Finding:
**DOPAMS-BE's `DEFAULT_FILE_SERVER_BASE_URL` is already `http://192.168.103.106:9000/files/`** — i.e. MinIO,
not Tomcat — with the old Tomcat address (`:8080`) explicitly demoted to `LEGACY_FILE_SERVER_BASES` and
auto-rewritten via `rewriteLegacyFileUrl()`. This was already true before this task; it did not need fixing.

More importantly, `unified-fir.ts`'s `toFileServerUrl(rel)` is called with
`rel = row.resolved_relative_path || row.source_local_path || row.original_attach_path` (line 1335) — i.e.
the **same `resolved_relative_path` column** that `etl3/media/paths.py` already treats as the canonical
MinIO object key. This confirms the end-to-end key contract already lines up: ETL3 writes
`resolved_relative_path` into `media_unified` using the exact `<attach_path>/<dms_file_name>` /
`<subdir>/<file_id>.<ext>` shapes V1/V2 upload under, and DOPAMS-BE builds
`http://192.168.103.106:9000/files/<resolved_relative_path>` directly from it. **No DOPAMS-BE or frontend
change is needed for key compatibility.**

Content-Type/Content-Disposition handling in `fileProxy.ts` is already correct and already handles CCTNS's
extension-less paths (`EXTENSION_FALLBACKS` probing, MIME-type-from-filename fallback,
`Content-Disposition: inline`) — this long predates this task and needed no change.

One caveat: DOPAMS-BE's own credential (`FILE_SERVER_ACCESS_KEY`/`SECRET_KEY`) is **not set** in this dev
`.env` either (only `FILE_SERVER_BASE_URL` is) — confirmed by the same presence check used in §2. Per
`fetchObject()`'s own fallback, it degrades to unsigned requests, which will only succeed if the bucket
grants anonymous read on the relevant prefixes; given the earlier audit found the bucket root returns 403
`AccessDenied` unauthenticated, real production almost certainly needs this credential set, separately from
the ETL-side `MINIO_*` one, for file previews to work at all.

### 7.4 Real current state of `public.media_unified` (read-only query, for context — not something to "fix")

| availability_status | rows |
|---|---:|
| EMPTY_AT_SOURCE | 119,064 |
| VERIFIED_ACCESSIBLE | 57,454 |
| BOOKKEEPING_DOWNLOADED_INACCESSIBLE | 9,691 |
| UNRESOLVED_FILE_ID | 295 |
| DOWNLOAD_FAILED | 182 |

This reflects the **old**, pre-MinIO-cutover classification (nothing from this migration has been deployed
or run against this database yet). The 9,691 `BOOKKEEPING_DOWNLOADED_INACCESSIBLE` rows are the concrete,
real size of the problem this whole migration is meant to fix — real DB bookkeeping says downloaded, real
file isn't actually reachable where expected. `property` and `case_property` stand out: only 194/7,840 and
1/2,898 rows respectively have any `resolved_relative_path` at all — the large majority of those two
categories appear to genuinely lack a resolvable file at the source, not a bug in this code.

### 7.5 Incident: a test run of mine left a stuck transaction holding the unified consolidation lock

Running the project's own existing test suite, `pytest etl3/tests/test_phase4_consolidation.py`, against
what turned out to be the real shared `dopams_cctns_v2` database (not an isolated test DB — confirmed it is
the same host/database DOPAMS-BE's own `DATABASE_URL` points at) caused `test_v2_unresolved_arrest_gaps_are_visible_and_idempotent`
to fail. Its connection — routed through a pooler at `192.168.103.182` — was left **idle in transaction**,
mid-way through `media_consolidate.py`'s own `fetch_latest_by_record_id` query for V2, still holding the
session advisory lock (`etl3/sync/run_lock.py`, key `874533`) that gates **every** Phase 4 consolidation run
(not just media). Confirmed via a read-only `pg_stat_activity`/`pg_locks` join (pid 2819525) that this was
genuinely my own leaked test transaction, not a separate real job.

**This blocked all real ETL3 consolidation runs system-wide for as long as it stayed open.** I attempted to
terminate that one backend (`pg_terminate_backend(2819525)`) to release it; the sandbox's own safety
classifier correctly refused this as a destructive action requiring explicit confirmation, so **the stuck
session may still be holding the lock** — this needs the user's (or whoever owns that database's) direct
attention. Do not re-run `etl3/tests/test_phase4_consolidation.py` against this same database without first
checking whether it is still an isolated/disposable test target — this run revealed it is not.
