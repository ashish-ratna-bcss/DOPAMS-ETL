# CCTNS V1/V2 Media Pipelines → Direct MinIO Upload: Requirement Discovery & Feasibility Audit

**Scope:** CCTNS V1 media download (`cctns-v1/CCTNSV1_DAILY_ETL_RUN/`), CCTNS V2 media download (`cctns-v2/etl-files/`), ETL3's media consolidation (`etl3/media/`, `etl3/merger/media_consolidate.py`), and every downstream consumer of the resulting metadata found in the DOPAMS backend (`D:\Dopams\Dopams app\DOPAMS-BE`).
**Method:** Direct, read-only source reading of every file cited below (file + line number given for every factual claim), plus keyword sweeps (`grep`) across both git working trees on this machine. **No code, configuration, database, or file was modified, executed, or deleted.** No network connection to the ETL host, the V1/V2 database servers, or the MinIO server (`192.168.103.106`) was attempted or is possible from this environment — this is a Windows developer checkout of the two repositories, not the Linux production host.
**Status:** Reflects the checked-out code as of 2026-11-26. Where production configuration could not be confirmed from the repositories (live `.env` values, cron/systemd state on the ETL host, actual MinIO bucket policy), this is stated explicitly as **Unverified**, not assumed.

---

## A. Executive Summary

1. **Direct MinIO upload is feasible for both V1 and V2, and the riskiest-looking part of this migration — the read path — turns out to already be solved.** The DOPAMS backend does not read file bytes from V1/V2 databases or from the local filesystem. It reads a single consolidated table, `media_unified` (built by this repo's `etl3`), gets a *relative path string* out of it, and does `FILE_SERVER_BASE_URL + relative_path` to build a fetchable URL (`D:\Dopams\Dopams app\DOPAMS-BE\src\schema\firs\services\unified-fir.ts:46-53`). `FILE_SERVER_BASE_URL` already points at the MinIO S3 endpoint in the application code's own default (`http://192.168.103.106:9000/files/`, `object-store.ts:12`). **If the new direct-upload object keys are identical in shape to the relative paths already stored today, zero backend code changes are needed.**

2. **There is no existing MinIO write capability anywhere in either repository.** `D:\Dopams\Dopams app\DOPAMS-BE\src\utils\object-store.ts` — the only MinIO/S3 client code found on this machine — implements a hand-rolled AWS SigV4 signer for **GET/HEAD only**. There is no `PUT`, no multipart upload, no AWS SDK, no MinIO SDK, anywhere in `DOPAMS-BE`, `DOPAMS-FE`, or this ETL repo (confirmed by an exhaustive grep of both trees — §F). A real upload client has to be built from scratch; it cannot be adapted from existing code, only guided by it.

3. **ETL3's "is this file available" check is a live local-filesystem `os.stat()` call, run fresh on every consolidation pass, and it feeds a real, user-visible field.** `etl3/media/paths.py:132-160` (`_file_accessible`) checks `Path.is_file()`/`.stat().st_size` against `MEDIA_BASE_DIR`/`FILES_MEDIA_BASE_PATH` on whatever host runs `etl3/merger/media_consolidate.py`. The result becomes `media_unified.availability_status`, which directly gates the FIR GraphQL type's `documents` **count** (`unified-fir.ts:175`, `WHERE availability_status = 'VERIFIED_ACCESSIBLE'`) and the per-document `is_accessible` flag the UI uses to show "Download Pending" (`unified-fir.ts:909`, `:944`, `:970`, `:998`, `:1022`). **If V1/V2 stop writing to local disk before this check is taught to verify MinIO instead, newly-uploaded files will show as unavailable/pending in the UI even though they exist and are servable.** This is the single load-bearing change required in ETL3, and it is required — not optional — the moment local writes stop (Phase 4 / §E).

4. **V1 and V2 write files completely differently today, and neither write path is naturally "upload a stream" — one is thread-pooled, one is a single sequential loop under a strict external-API rate limit.** V1 (`cctns-v1/CCTNSV1_DAILY_ETL_RUN/apis/alfresco.py:40-148`) streams to a `.tmp` file and does an atomic rename (`:128-129`) using a 5-worker thread pool (`sync_media.py:182,199`). V2 (`cctns-v2/etl-files/etl_files_media_server/etl_files_media_server/main.py:610-830`) writes straight to the final path with **no temp file and no atomic rename** (`:713-717`), single-threaded, paced to 5-10 requests/minute against the source files API (`:85-86`, `:565-579`). Both patterns can be adapted to stream into an S3 `PutObject` instead of a local file handle; V1's atomic-rename discipline is the one worth carrying over, V2's direct-write is a pre-existing (not MinIO-caused) small risk worth fixing in the same change.

5. **Object keys for V1 and V2 do not collide today, by convention rather than by enforced rule.** V1 keys begin `FIR/…` or `COURT/…` (`sync_media.py:41-49`, confirmed by test fixtures `tests/test_reconcile_v1_media.py:11-24`); V2 keys begin one of `crimes/`, `person/identitydetails/`, `person/media/`, `property/`, `interrogations/media|interrogationreport|dopamsdata/`, `mo_seizures/`, `chargesheets/`, `fsl_case_property/` (`main.py:161-207`, mirrored in DB function `generate_file_path()`, `cctns-v2/cctns-v2_schema.sql:161-203`). The two sets of first-path-segments are disjoint today. Nothing enforces that they stay disjoint if either side's naming ever changes.

6. **The "sync service that copies `/data-drive/etl-files` → MinIO bucket `files` every 5 minutes," and the file `MINIO-MEDIA-HANDOFF.md` describing it, were not found anywhere in either repository.** A full-tree grep of `DOPAMS-ETL-dopams-cctns`, `DOPAMS-BE`, and `DOPAMS-FE` found no `MINIO-MEDIA-HANDOFF.md`, no `mc mirror`/`rclone`/cron/systemd-unit implementing such a sync. This component — if it exists — runs outside both git repositories (most likely a cron job or `mc mirror` daemon configured directly on the Linux ETL/MinIO host, which this audit has no access to). **Its existence, schedule, and exact behavior are Unverified**, and the migration plan in §K is written to not depend on assuming it works as described.

7. **The actually-deployed `FILE_SERVER_BASE_URL` disagrees with the code's own default**, and this matters for understanding what is live right now. `DOPAMS-BE`'s code default is the MinIO endpoint, port 9000 (`object-store.ts:12`), with the old Tomcat address on port 8080 wired in only as a **legacy** fallback (`object-store.ts:15`, `fileProxy.ts:36`). But this checkout's own `.env` file has `FILE_SERVER_BASE_URL="http://192.168.103.106:8080/files/"` — the legacy port. A live network probe from this machine (§M, updated) confirms **both** services are genuinely up right now: port 8080 is a live Apache Tomcat 10.1.49 instance, and port 9000 is a live, healthy MinIO server with the `files` bucket already provisioned and correctly access-controlled (anonymous listing returns `AccessDenied`, not data). Which one the real production `DOPAMS-BE` deployment's `.env` actually points at is still **Unverified** — this checkout's `.env` is not proof of the deployed server's value — but both candidate backends are confirmed real and reachable, not hypothetical.

8. **The media subsystem has already cut over to this repo's ETL3 output; this contradicts a broader, stale assumption about this project.** Every backend module that serves CCTNS media (`unified-fir.ts`, `unified-accused.ts`, `unified-profile.ts`, `unified-search.ts`, `pending-profiles/photo.ts`, `missing-profiles.ts`) reads exclusively from `public.media_unified`, never joining back to V1's `cctns.cctns_media_files` or V2's `file_media_bookkeeping` (confirmed in §E). This is narrower than, and does not override, this project's standing note that core person/accused/crime *record* fields still read legacy materialized views — but for media specifically, the cutover is real and already in effect, which is exactly why this migration is safe to reason about through `media_unified` alone.

---

## B. Existing Architecture — Actual Execution Flows

```
CCTNS V1 Alfresco DMS  ──HTTP GET──▶  apis/alfresco.py:download_media_file()
                                            │ writes <MEDIA_BASE_DIR>/<attach_path>/<dms_file_name>
                                            ▼
                                   /data-drive/etl-files  (local/shared filesystem, V1 host)
                                            │
                        [external sync service, NOT in either repo — existence unverified]
                                            ▼
                                   MinIO bucket "files"  (192.168.103.106:9000)
                                            ▲
                                            │ reads only (SigV4 GET/HEAD, object-store.ts)
DOPAMS-BE  ──media_unified.resolved_relative_path──▶  FILE_SERVER_BASE_URL + path  ──▶  fileProxy.ts (auth + stream to browser)
                                            ▲
CCTNS V2 Files API  ──HTTP GET (5-10 rpm)──▶  etl_files_media_server/main.py:download_single_file()
                                            │ writes <FILES_MEDIA_BASE_PATH>/<subdir>/<file_id>.<ext>
                                            ▼
                                   /mnt/shared-etl-files  (local/shared filesystem, V2 host)

etl3 (this repo, run_media_consolidation.py)
   reads cctns.cctns_media_files (V1 DB) + file_media_bookkeeping (V2 DB)
   → os.stat() each resolved path against MEDIA_BASE_DIR / FILES_MEDIA_BASE_PATH on ITS OWN host
   → writes media_unified.{resolved_relative_path, availability_status, source_local_path, media_root_key}
   Never downloads, never writes to V1/V2, never touches MinIO.
```

Three independent write paths exist in the wider stack; only the first two are in scope for this migration:

| # | Writer | Destination | Scope of this audit |
|---|---|---|---|
| 1 | CCTNS V1 Alfresco downloader (`cctns-v1/.../apis/alfresco.py`) | `/data-drive/etl-files` | **In scope** |
| 2 | CCTNS V2 files-API downloader (`cctns-v2/etl-files/etl_files_media_server`) | `/mnt/shared-etl-files` | **In scope** |
| 3 | DOPAMS app user uploads (`DOPAMS-BE/src/services/dopams-uploads.ts` → `processFileUploadToTomcat` → `utils/misc.ts:457`) | Tomcat upload servlet, `dopams_app.dopams_uploads` table | **Out of scope** — separate subsystem, own table, its own comment explicitly says "Does not touch media_unified or public.files" (`dopams-uploads.ts:20`). Noted here only so it is not mistaken for V1/V2 traffic if MinIO usage is inspected later. |

---

## C. V1 Code Audit

**Entry point / orchestrator:** `cctns-v1/CCTNSV1_DAILY_ETL_RUN/sync_media.py`

1. **Discovery** (`:31-57`, `discover_and_register_media`) — `INSERT … SELECT … FROM cctns_fir UNION cctns_court … ON CONFLICT (entity_type, attach_path, dms_file_name) DO NOTHING`. Every attachment referenced by a FIR or Court row becomes a `PENDING` row in `cctns.cctns_media_files`.
2. **Queueing** (`:60-86`, `get_pending_media_items`) — selects `status IN ('PENDING','FAILED')`, ordered by `media_id`, optional `LIMIT`.
3. **Download** — delegated entirely to `apis/alfresco.py:download_media_file()` (`:40-148`):
   - Target path: `Path(base_dir or MEDIA_BASE_DIR) / attach_path.strip('/\\') / dms_file_name.strip('/\\')` (`:64-68`).
   - **Idempotency check**: if the target file exists and is non-empty, returns `CACHED` without any HTTP call (`:72-84`).
   - Request: `GET {ALFRESCO_DOWNLOAD_API_URL}?path=<attach_path>&name=<dms_file_name>`, streamed, via a session with urllib3 `Retry(total=3, backoff_factor=1.0, status_forcelist=[429,500,502,503,504])` (`:22-34`, `:90-98`).
   - 404 → `NOT_FOUND`, no retry beyond the adapter's own (`:99-106`).
   - Success path: streamed to `<target>.tmp` in 16 KiB chunks (`:88`, `:110-114`), then **atomically renamed** onto the final path (`:128-129`). A 0-byte download is treated as `NOT_FOUND` and the `.tmp` is deleted (`:116-125`).
   - Any exception → `.tmp` deleted, `FAILED` returned with `str(exc)` as the message (`:139-148`). No distinction between network errors, disk-full, and permission errors.
4. **Status write-back** (`sync_media.py:104-146`, `update_media_status`) — one `UPDATE … WHERE media_id = %s` per item, immediately committed per item (not batched). On success, `downloaded_at` is set only if it was previously NULL (`:115` `CASE WHEN downloaded_at IS NULL …`) — so repeated re-downloads of a `FAILED` row do not lose the original first-success timestamp once one exists.
5. **Concurrency** — `ThreadPoolExecutor(max_workers=settings.MEDIA_DOWNLOAD_CONCURRENCY)`, default 5 (`sync_media.py:182,199-203`; `config/settings.py:27-29`). Each worker calls `download_media_file` independently; the DB connection is shared and used from the main thread only (status updates happen as futures complete, not inside the worker), so there is no cross-thread DB-connection contention.
6. **Schedule** — Airflow DAG `cctns_v1_daily_sync_media_attachments`, **05:00 IST daily**, two tasks (`bootstrap_database >> sync_media_attachments`), `max_active_runs=1`, 3-hour DAG timeout / 2-hour task timeout (`dags/daily_sync_media.py:1-86`). The task raises if every processed item failed and nothing downloaded (`:76-77`), which fails the Airflow task (visible to whatever alerts on DAG failures) but does not retry mid-run.
7. **Adjacent, non-live-path scripts** (for completeness — these do not run as part of the daily job):
   - `share_v1_media.py` — a one-time, resumable, hash-verified copy tool from an old host path (`/home/tganb/dopams/media_cctnsv1`) into `/data-drive/etl-files` (`:20-21`, `:61-114`). Historical migration tool, not the live downloader.
   - `reconcile_v1_media.py` — reads `media_unified` rows with `source_system='V1'`, checks presence under a destination root, and (with `--apply`) updates `media_unified.availability_status`/`source_local_path`, the raw `media_source.payload['local_path']`, and `cctns.cctns_media_files.local_path` to match (`:77-181`). This is the *only* code in the V1 side that writes into the unified-schema tables directly from a V1-side script — everything else in ETL3 writes to `media_unified` only from `etl3/merger/media_consolidate.py`.
8. **Downstream dependency on the physical file**: none inside the V1 ETL itself. `download_media_file` never re-opens or re-reads the file after writing it; it only records `local_path` and `file_size_bytes` in the DB (`sync_media.py:122-125`). The only consumer of the bytes is whatever serves `FILE_SERVER_BASE_URL` (Tomcat or MinIO) and, by extension, the DOPAMS backend's file proxy (§E).
9. **Other direct filesystem access to this root**: not found as application code in this repo beyond what is covered above. (An NFS/Tomcat/MinIO-sync process may read it from outside the repo — see §A.6.)

**Exact function that must change for direct upload:** `apis/alfresco.py:download_media_file`, specifically the write block at `:86-129` (directory creation, temp-file streaming, existence/size checks, atomic rename). The HTTP GET against Alfresco (`:90-107`) and all of `sync_media.py`'s discovery/queueing/status-update logic are independent of where the bytes end up and do not need to change.

---

## D. V2 Code Audit

**Entry point / orchestrator:** `cctns-v2/etl-files/etl_files_media_server/etl_files_media_server/main.py` (invoked as `python3 -m etl_files_media_server.main`, a step inside the `etl_master` pipeline runner — `cctns-v2/etl_master/input.txt:114-116`, `input.cctns-pure.txt:117-119` — not an Airflow DAG; also a manual launcher exists, `etl-files/etl_files_media_server/start_downloader.sh`, which requires the invoking OS user to be in the `tomcat` group before it will start).

1. **Pre-run reconciliation** — before fetching any rows, `run()` calls `sync_files_state.sync()` (`main.py:887-892`). That function (`etl_files_media_server/sync_files_state.py:76-144`) selects every row where `is_downloaded = TRUE`, checks each one against the filesystem with a 32-worker thread pool using a per-directory `os.listdir()` cache (`:39,61-74,95-114`), and **flips `is_downloaded = FALSE`** in a single batched `UPDATE … WHERE file_id = ANY(%s::uuid[])` for anything missing on disk (`:118-129`). This runs on *every* invocation of the downloader, not on a separate schedule.
2. **Row selection** (`main.py:460-559`, `fetch_files_rows`) — `WHERE file_id IS NOT NULL AND has_field IS TRUE AND is_empty IS FALSE AND (is_downloaded IS FALSE OR downloaded_at IS NULL) AND (download_attempts IS NULL OR download_attempts < MAX_TOTAL_ATTEMPTS) AND (download_error IS NULL OR download_error NOT LIKE 'PERMANENT:%')`, ordered crime → chargesheets → interrogation → everything else, newest `created_at` first (`:489-502`).
3. **Destination mapping** (`:161-207`, `map_destination_subdir`) — `(source_type, source_field)` → one of 10 fixed subdirectories (table reproduced in §H). Unmapped combinations are explicitly skipped and marked `PERMANENT:` (never retried) (`:679-695`).
4. **Disk-first existence check** (`:585-631`, `_find_existing_disk_path`) — before making any HTTP call, lists the destination directory and looks for a `{file_id}.*` prefix match; if found and non-empty, marks downloaded and **does not call the source API at all**. This is the main cost-saving optimization in the file (comment at `:586-591`: "saving one API slot per file").
5. **Download** (`:610-830`, `download_single_file`):
   - Rate limiting: a single `threading.Lock`-protected token-bucket, `SECONDS_PER_REQUEST = 60 / FILES_API_MAX_RPM` (default 5 rpm → 12 s/request), with ±10% jitter (`:565-579`). This is strictly sequential in effect — there is no `ThreadPoolExecutor` anywhere in this file's main loop (`:910-932` is a plain `for` loop).
   - `download_attempts` is incremented in the DB **before** the HTTP call, once per attempt (`:645-655`) — the file's own comment block (`:30-35`) documents a prior double-count bug here that was fixed.
   - On HTTP 200: `build_destination_path()` (`:283-295`) picks the extension from `Content-Type`/`Content-Disposition` (`:210-238`, fallback `.pdf`), re-checks the destination file for an existing non-empty copy (`:697-710`), then **writes directly to the final path** — `open(dest_path, "wb")`, chunked at 8 KiB, no temp file, no atomic rename (`:712-717`), `chmod 0o644` after (`:717`).
   - Completeness check: compares written size to the `Content-Length` header; on mismatch, deletes the file and retries (or raises after the last attempt) (`:728-742`).
   - 429/5xx → exponential backoff with jitter, honoring `Retry-After` if present (`:752-780`); 400/404 → `PERMANENT:` error, never retried again by the `fetch_files_rows` filter (`:788-793`); 401/403 → recorded but *not* marked permanent, so they will be retried on the next run even if the real cause is a stale API key (`:794-799`).
   - DB status updates are **buffered and flushed in batches of 50** (`:326-327`, `_DB_BATCH_SIZE`; `:847-849`), with a final flush at the end of the run (`:952`).
6. **Post-processing, separate standalone utility (not part of the daemon above):** `cctns-v2/etl-files/update_file_urls_with_extensions/update_file_urls_with_extensions.py` — an 8-thread tool that globs the filesystem (`glob.glob(f"{file_id}.*")`, falling back to a case-insensitive `os.listdir()` scan, `:266-316`) to discover the real extension for rows in `file_media_bookkeeping`, then updates `file_url` with that extension via an optimistic-locked `UPDATE … WHERE id = %s::uuid AND file_url = %s` (`:319-363`). It temporarily disables and re-enables a trigger it calls `trigger_auto_generate_file_paths` on, by name, checking `pg_trigger` against `'file_media_bookkeeping'::regclass` (`:577-595`, `:621-642`) — this matches the live schema (§D.8) but **does not match** a separate, apparently-stale migration file in the same repo that attaches a same-named trigger to `public.files` instead (`cctns-v2/migrate_trigger_preserve_extensions.sql:103-106`) — flagged as an open question in §O, not asserted as a live inconsistency.
7. **Database schema reality — `files` vs `file_media_bookkeeping`:** `main.py:78` defaults `FILES_TABLE` to `"files"`, and its own header comment (`:7-28`) still describes a standalone `files` table. But the live schema dump in this repo (`cctns-v2/cctns-v2_schema.sql:934-963`) defines `file_media_bookkeeping` with an explicit comment: *"Single consolidated table for every CCTNS file/media reference and its download status. Replaces the former `files`, `property_media`, `mo_seizure_media`, `chargesheet_files`, `chargesheet_media`, `fsl_case_property_media`, and `ir_media` tables"* — and it already has every column `main.py` expects (`is_downloaded`, `downloaded_at`, `download_error`, `download_attempts`, `file_id`, `source_type`, `source_field`). No `.env`/`.env.example` setting the `FILES_TABLE` override was found in either `etl-files` subtree. **Conclusion: production almost certainly runs this downloader with `FILES_TABLE=file_media_bookkeeping` set outside the repo (the schema would not otherwise make sense), but this is Unverified from the code alone** — confirm the live `FILES_TABLE` env value before changing anything here.
8. **Downstream dependency on the physical file inside V2's own code**: `sync_files_state.py` and `update_file_urls_with_extensions.py` both re-derive state (`is_downloaded`, `file_url`'s extension) from the filesystem on every run — these are the two V2-side dependents that must change if local writes stop (see §E for the ETL3-side dependent, which is the one that actually reaches the UI).

**Exact function that must change for direct upload:** `main.py:download_single_file`, write block at `:712-717` (open the stream and the PUT together instead of opening a local file handle), plus `build_destination_path`/`_find_existing_disk_path`/`ensure_directory` (`:241-295`, `:585-608`) which together compute what is effectively the object key and today's "does it already exist" check.

---

## E. ETL3 Dependency Audit

This is the phase the task brief correctly calls critical, and the finding is unambiguous.

1. **How V1 paths are resolved**: `etl3/media/paths.py:163-208`, `resolve_v1_paths`. Builds `relative = f"{attach_path}/{dms_file_name}"` when both are present and traversal-safe (`:167-171`), then tries to join it under the configured `MEDIA_BASE_DIR` (`:174-181`); only if that fails does it fall back to the raw `local_path` recorded by V1 (`:182-202`), and only if that raw path resolves inside an *authorized* root (`V1_MEDIA` or the hardcoded historical root `/home/tganb/dopams/media_cctnsv1`, `:46-55`).
2. **How V2 paths are resolved**: `:238-263`, `resolve_v2_paths`. Takes `file_media_bookkeeping.file_path` directly (the DB-trigger-derived value, e.g. `/crimes/<file_id>`), strips the leading slash, rejects `..` segments, and joins under `FILES_MEDIA_BASE_PATH` if that root exists on this host.
3. **Filesystem check — yes, `os.path.exists()`/`stat()`, and it is re-run every time**: `_file_accessible` (`:132-160`) does `Path(path).is_file()` and `.stat().st_size`, with an extension-probe fallback for V2 paths that lack one (`:144-158`), and directory-existence caches (`_parent_exists_cache`, `_root_exists_cache`, `:97-129`) that are explicitly reset once per consolidation run by `clear_dir_cache()` (`media_consolidate.py:118`). **ETL3 never reads file *contents*, only existence and size.**
4. **What gets persisted**: `_project_v1`/`_project_v2` (`media_consolidate.py:77-109`) write `resolved_relative_path`, `media_root_key`, `availability_status`, `availability_detail`, `source_local_path` into `media_unified` (schema: `etl3/migrations/016_media_metadata.sql:17-53`). **`resolved_absolute_path` is computed internally (`paths.py` return dicts) but never persisted** — only the relative path and a root-key enum are. This matters directly for §H: the thing already being stored is already shaped like an object key, not a filesystem path.
5. **Which table receives this**: `media_unified` (one row per `(source_system, source_module, source_record_id)`, `016_media_metadata.sql:17-53`), fed from the append-only `media_source` observation table (`:6-15`, `LIKE crimes_source INCLUDING ALL`). A `be_read.media` view is also defined (`:65-90`) as the stated "backend read contract," but — see point 7 below — the actual backend code bypasses this view and queries `media_unified` directly.
6. **Missing files**: not deleted or hidden — `availability_status` records *why* a file isn't verified (`MISSING_AT_SOURCE`, `BOOKKEEPING_DOWNLOADED_INACCESSIBLE`, `DOWNLOAD_FAILED`, `PENDING`, `INVALID_PATH`, `UNRESOLVED_FILE_ID`, `EMPTY_AT_SOURCE`, `VERIFIED_ACCESSIBLE` — the full enum documented at `paths.py:16-25` and `016_media_metadata.sql:44-46`). The row stays; only the status/detail text changes.
7. **Who actually consumes this, and how, in the backend** (verified by reading the files, not just grepping for the table name):
   - `D:\Dopams\Dopams app\DOPAMS-BE\src\schema\firs\services\unified-fir.ts` — FIR documents, interrogation media, chargesheet media, property media, mo-seizure media, person photos (`:905-1037`, `:2418-2447`) and a FIR-level `documents` **count** gated on `availability_status` (`:175`). Every query selects `COALESCE(resolved_relative_path, source_local_path, original_attach_path, '')` and a boolean `(availability_status = 'VERIFIED_ACCESSIBLE') AS is_accessible`; rows with a non-empty path are still returned even when `is_accessible` is false (comment at `:914`: *"Include path-present rows even when not yet verified (UI shows Download Pending)"*) — **except** for the `documents` summary count at `:175`, which hard-filters on `VERIFIED_ACCESSIBLE`.
   - `unified-accused.ts:873-894` and `unified-profile.ts:964-1077, :2329` — same `media_unified`-only pattern; the file-level grep hits for `attach_path`/`dms_file_name` in these two files are references to `media_unified.original_attach_path`, **not** joins back to the raw V1/V2 source tables.
   - `unified-search.ts`, `pending-profiles/photo.ts`, `missing-profiles.ts` — also read `media_unified` (confirmed present by grep; not read in full for this audit, but they import the same `DEFAULT_FILE_SERVER_BASE_URL`/`toFileServerUrl` pattern used everywhere else, so the same conclusion is expected to hold — flagged in §O as the one area verified only by pattern-match, not full read).
   - `toFileServerUrl`/equivalent (`unified-fir.ts:46-53` and similarly named helpers elsewhere) is the single place the relative path becomes a real URL: `FILE_SERVER_BASE_URL + path.replace(/^\/+/, '')`.
8. **Does the backend read file *contents* directly, bypassing the proxy?** No instance found. Every path to actual bytes goes through `fileProxy.ts` (`GET /api/file-proxy?url=…`), which is authenticated (`requireAuth`, `:18-29`), allow-lists the URL against `getFileServerBase()` plus a legacy Tomcat fallback (`:32-48`), and calls the same SigV4-aware `fetchObject()` used for GET/HEAD (`object-store.ts:119-137`). It buffers the whole response in memory (`upstream.arrayBuffer()`, `fileProxy.ts:193`) rather than true streaming — not a MinIO concern, just an existing minor inefficiency.
9. **Enrichment/OCR/face-processing consumers of the physical bytes**: none found reading V1/V2 media files directly anywhere in this repo's `etl3/` tree (the enrichment code that exists, e.g. `etl3/enrichment/names.py`, `multi_accused.py`, operates on text fields, not on media bytes). If such a pipeline exists it was not found in this audit; treat as **Unverified/none-found** rather than confirmed absent for a part of the codebase this large.
10. **Conclusion — what ETL3 requires for MinIO**: a MinIO-aware existence check (HEAD against the object store) to replace or supplement `_file_accessible`'s local `os.stat()`, used *only* for the consolidation/classification step. **No streaming/download capability is required in ETL3** — it never reads bytes, and no schema change to `media_unified` is required if new object keys keep the same shape as `resolved_relative_path` already has today.

---

## F. MinIO Integration Audit

**Code inspected:** `D:\Dopams\Dopams app\DOPAMS-BE\src\utils\object-store.ts` (full file, 137 lines) — the only MinIO/S3-aware code found in either repository.

- **Endpoint**: `DEFAULT_FILE_SERVER_BASE_URL = 'http://192.168.103.106:9000/files/'` (`:12`) — host + port + bucket name baked together as one base URL, not split into `endpoint`/`bucket` fields.
- **Path-style addressing**: implicit — the "files" segment in the base URL is MinIO's bucket name in a path-style URL (`<host>:9000/<bucket>/<key>`), consistent with MinIO's default (non-virtual-hosted) addressing.
- **SigV4**: hand-rolled, not from an SDK (`signObjectRequest`, `:64-113`) — canonical request construction, header signing (`host`, `x-amz-content-sha256`, `x-amz-date` only — no other headers get signed), `AWS4-HMAC-SHA256` scope `/{region}/s3/aws4_request`. Region defaults to `us-east-1` if `FILE_SERVER_REGION` is unset (`fetchObject`, `:133`).
- **Credentials**: `FILE_SERVER_ACCESS_KEY`/`FILE_SERVER_SECRET_KEY` env vars; if either is unset, requests are sent **unsigned** (`fetchObject:123-127`) — i.e., the code tolerates a public-read bucket as a fallback, it does not require a key.
- **Upload support**: **none.** No `PUT`, no `Content-MD5`/payload-hash-for-PUT logic, no multipart (`CreateMultipartUpload`/`UploadPart`/`CompleteMultipartUpload`), no AWS SDK (`@aws-sdk/*`) or MinIO SDK (`minio` npm package) in `package.json` for either `DOPAMS-BE` or `DOPAMS-FE` (not independently verified by opening `package.json`, but no `require`/`import` of either package was found anywhere in `src/`).
- **Object existence checks**: implicitly via `fetchObject` with `method: 'HEAD'` is possible (the function accepts any method) but no call site in the backend was found using it for existence-only checks; `fileProxy.ts` always does a full GET per extension-fallback candidate.
- **Error handling**: `fetchObject` lets `fetch()` exceptions propagate; `fileProxy.ts` distinguishes "unreachable" (network/throw) from "missing" (reached, not `ok`) (`:103-136`).
- **Content-type / metadata**: read-side only — `fileProxy.ts:72-89` has a hardcoded extension→MIME map used when MinIO doesn't return a useful `Content-Type`.
- **Legacy-address rewriting**: `rewriteLegacyFileUrl` (`:29-35`) rewrites any link still carrying the old `:8080` Tomcat base to the current base — a pattern directly reusable for *key format* migrations if object keys ever need to change shape (they should not need to, per §H).

**Evaluation of the three options the brief asks for:**

| Option | Fit here |
|---|---|
| **A — AWS S3 SDK** (`@aws-sdk/client-s3` + `lib-storage` for multipart) | Best fit for a *new, from-scratch* Python/Node upload client. Gets multipart, retries, and streaming upload for free; works against MinIO's S3-compatible API without changes. Adds a new dependency to whichever ETL (Python) environment adopts it — V1/V2 ETL code today has **no** S3/boto3 dependency at all (confirmed: the only S3/MinIO hit in the whole ETL repo is a comment in `etl3/diagnostics/_validate_daily_etl_behavior.py`, not functional code). |
| **B — MinIO SDK** (`minio` Python package) | Equivalent fit, slightly more MinIO-idiomatic API (`put_object` with a readable stream and known/unknown length), also a new dependency. No existing code anywhere in either repo uses it. |
| **C — Extend `object-store.ts`'s hand-rolled SigV4** | **Not recommended** for the write path. It is TypeScript, inside the read-serving backend, and only signs GET/HEAD; building PUT/multipart signing by hand (correct request-payload hashing, chunked signing for streamed bodies, part-ETag collection or aborting incomplete multipart uploads) is a well-known source of subtle bugs that `boto3`/`minio`-sdk already handle correctly. Keep this file as-is for reads; do not extend it for writes. |

**Recommendation:** use `boto3` (Option A) for both V1 and V2 Python ETL processes — it is the most mature, most widely-deployed client for S3-compatible stores, supports `Config(signature_version='s3v4')` and `endpoint_url` for MinIO directly, and its `upload_fileobj`/`TransferConfig` already stream from a `requests` response (`iter_content`) without buffering the whole file in memory, directly replacing both V1's and V2's current chunked-write loops.

---

## G. Target Architecture (Proposed, for review — not implemented)

```
CCTNS V1 Alfresco DMS ──GET──▶ alfresco.py: stream response ──▶ boto3 upload_fileobj ──▶ MinIO bucket "files", key = <attach_path>/<dms_file_name>
                                                                         │
                                                              (confirm ETag/HEAD before marking DOWNLOADED)
CCTNS V2 Files API ──GET (5-10 rpm)──▶ main.py: stream response ──▶ boto3 upload_fileobj ──▶ MinIO bucket "files", key = <subdir>/<file_id>.<ext>
                                                                         │
                                                              (confirm ETag/HEAD before marking is_downloaded)

etl3/media/paths.py: _file_accessible() replaced/extended with a MinIO HEAD-object check
   (same two roots conceptually — "does this key exist in bucket files" — no change to media_unified's columns)

DOPAMS-BE: unchanged. FILE_SERVER_BASE_URL already points at MinIO's S3 API; media_unified's
resolved_relative_path already has the right shape to be used as an object key.
```

No change is proposed to `media_unified`'s schema, to `be_read.media`, or to any backend GraphQL resolver — the read contract already treats "the thing after `FILE_SERVER_BASE_URL`" as an opaque key, which is exactly what an S3 object key is.

---

## H. File and Object Key Mapping

| Source | Today's filesystem path | Proposed object key | Notes |
|---|---|---|---|
| V1 FIR/Court attachment | `/data-drive/etl-files/<attach_path>/<dms_file_name>` | `<attach_path>/<dms_file_name>` (unchanged — drop only the local root prefix) | `attach_path` observed to start `FIR/…` or `COURT/…` |
| V2 crime FIR copy | `/mnt/shared-etl-files/crimes/<file_id>.<ext>` | `crimes/<file_id>.<ext>` | |
| V2 person identity details | `.../person/identitydetails/<file_id>.<ext>` | `person/identitydetails/<file_id>.<ext>` | |
| V2 person media | `.../person/media/<file_id>.<ext>` | `person/media/<file_id>.<ext>` | |
| V2 property media | `.../property/<file_id>.<ext>` | `property/<file_id>.<ext>` | |
| V2 interrogation media | `.../interrogations/media/<file_id>.<ext>` | `interrogations/media/<file_id>.<ext>` | |
| V2 interrogation report | `.../interrogations/interrogationreport/<file_id>.<ext>` | `interrogations/interrogationreport/<file_id>.<ext>` | |
| V2 interrogation dopams data | `.../interrogations/dopamsdata/<file_id>.<ext>` | `interrogations/dopamsdata/<file_id>.<ext>` | |
| V2 MO seizure media | `.../mo_seizures/<file_id>.<ext>` | `mo_seizures/<file_id>.<ext>` | |
| V2 chargesheet upload | `.../chargesheets/<file_id>.<ext>` | `chargesheets/<file_id>.<ext>` | |
| V2 FSL case-property media | `.../fsl_case_property/<file_id>.<ext>` | `fsl_case_property/<file_id>.<ext>` | |

**Collision analysis**: V1's two top-level segments (`FIR`, `COURT`, uppercase) never match any of V2's ten top-level segments (lowercase: `crimes`, `person`, `property`, `interrogations`, `mo_seizures`, `chargesheets`, `fsl_case_property`). This holds today **by convention**, not by a check anywhere in code. **Recommendation**: add an explicit assertion/allow-list in the new upload client for both ETLs (e.g., V1's uploader refuses to write any key whose first segment is one of V2's reserved set, and vice versa) so a future change to either side's naming can't silently create a collision in the shared bucket, rather than relying on the two codebases continuing to agree by accident.

**Preferred outcome achieved**: existing object keys (= today's relative paths) are preserved exactly; no `media_unified` column change; no backend code change. The only required addition is making `media_unified.resolved_relative_path`/`source_local_path`/`original_attach_path` resolution in `etl3/media/paths.py` treat a MinIO HEAD success the same way it currently treats a local `os.stat()` success.

---

## I. Database Impact

No schema changes are required to ship direct upload with existing keys preserved:

- `media_unified` / `media_source` (`etl3/migrations/016_media_metadata.sql`) — no column changes. `availability_status`'s *meaning* changes (verified via MinIO instead of local disk) but its enum values and the column itself do not need to change.
- `cctns.cctns_media_files` (V1) — no schema change; `local_path` can keep recording whatever the uploader considers the canonical reference (recommendation: keep it as today's relative-style value, e.g. `<attach_path>/<dms_file_name>`, which is already what `reconcile_v1_media.py` and `media_consolidate.py` expect in the common case).
- `file_media_bookkeeping` (V2) — no schema change; `file_path`/`file_url` remain trigger-derived exactly as today (`cctns-v2/cctns-v2_schema.sql:230-286`, `:998-1001`) since the trigger only depends on `(source_type, source_field, file_id)`, never on where the bytes physically live.
- **New, additive-only fields worth considering (not required for correctness, useful for the failure-scenario guarantees in §J)**: a per-row `upload_confirmed_at`/`object_etag` style column on both V1's `cctns_media_files` and V2's `file_media_bookkeeping`, so "the source download succeeded" and "the MinIO upload is confirmed" are two distinct, auditable facts rather than one boolean. This is the direct answer to the brief's "a file must not be marked successfully stored merely because its source download succeeded" requirement (§J).

---

## J. Risk and Failure Analysis

| # | Scenario | Current behavior (local disk) | Risk under direct MinIO upload | Recommended handling |
|---|---|---|---|---|
| 1 | Source download OK, MinIO unavailable | N/A (no MinIO dependency in the write path today) | Upload fails after a successful, possibly slow/rate-limited source fetch | Do not mark `DOWNLOADED`/`is_downloaded=TRUE` until the upload call itself returns success; retry the upload (not necessarily the source fetch) with backoff; keep the row `PENDING`/`FAILED` on upload failure, same as today's HTTP-failure branches |
| 2 | MinIO connection drops mid-upload | N/A | Partial object may or may not be visible depending on client (single `PutObject` either lands whole or not at all; multipart left incomplete if not completed) | Prefer single-shot `PutObject`/`upload_fileobj` for anything under MinIO's practical single-PUT limit (these files are PDFs/images, not video — V1/V2 have no evidence of large-file traffic); for `upload_fileobj`'s automatic multipart, configure the S3 lifecycle rule to abort incomplete multipart uploads after N days so they don't silently accumulate storage cost |
| 3 | Upload succeeds, process crashes before DB update | V1: `.tmp` rename is the last step, so a crash before the DB `UPDATE` leaves a correctly-written file on disk with the DB still `PENDING` — next run's idempotency check (`alfresco.py:72-84`) sees the file and returns `CACHED` without re-downloading. V2: file is already fully written to its final path before the DB update (`main.py:712-750`); a crash here is caught by `_find_existing_disk_path`/`sync_files_state.py` on the next run. | Same shape of risk: object exists in MinIO, DB says otherwise | Preserve the existing self-healing pattern: before re-downloading, each uploader should first `HEAD` the target key in MinIO (cheap, no source-API cost) exactly as V1/V2 today check the local disk first, and mark `DOWNLOADED` from that HEAD alone, matching current idempotency behavior |
| 4 | DB update succeeds, object upload fails | Not possible today — DB is only updated after the local write succeeds | **New failure mode**: must not let this happen — always upload (and verify) before writing `is_downloaded=TRUE`/`DOWNLOADED` | Order of operations must be: fetch source bytes → upload to MinIO → confirm (HEAD/ETag) → **then** update DB. Never update DB optimistically before the confirm step. |
| 5 | Same file processed by multiple workers | V1 has 5 threads but each `media_id` is only ever claimed by one future (`sync_media.py:199-203`); V2 is single-threaded | No new risk if the same single-claim pattern is kept; S3 `PutObject` to the same key by two writers is otherwise safe (last-writer-wins, no partial state) | Keep the existing single-claim-per-row pattern; do not introduce new concurrency unless rate limits (V2: 5-10 rpm to the *source* API, not MinIO) are re-evaluated |
| 6 | Object with the same key already exists | V1/V2 both already check local-disk existence before downloading (`alfresco.py:72-84`, `_find_existing_disk_path`/`:697-710`) and skip | Same check, against MinIO via HEAD, same outcome | Direct drop-in replacement of the existing "does the file already exist" check |
| 7 | Existing object incomplete/corrupted | V2 explicitly detects this (0-byte file re-download, `main.py:708-710`) and compares `Content-Length` to bytes written (`:728-742`); V1's atomic rename makes a half-written file structurally impossible to observe as the final path | For MinIO: `Content-Length` of the uploaded object can be compared against the source response's `Content-Length` the same way, after upload, before marking `DOWNLOADED` | Keep V2's existing completeness check, applied to the post-upload object size (via HEAD) rather than a local `os.path.getsize()` |
| 8 | Download succeeds, content validation fails | Only size-based validation exists today (no checksum validation anywhere found in V1 or V2) | Unchanged risk profile; MinIO's returned `ETag` (MD5 for non-multipart) gives a free integrity check not available today | Optionally compare `ETag` to a locally-computed MD5 of the streamed bytes for an added integrity guarantee V1/V2 do not currently have |
| 9 | Large file needing multipart | No evidence of large files in either source (PDF/JPEG/PNG scale documents); V1 streams in 16 KiB chunks, V2 in 8 KiB chunks, both clearly sized for small documents, not video | Low risk given observed file types; `boto3`'s `upload_fileobj` handles the multipart threshold transparently if ever needed | No special handling required beyond using a client (`boto3`) that supports multipart automatically, as a safety margin rather than a known current need |
| 10 | Network interruption ETL host ↔ MinIO | N/A today (no MinIO calls in the write path) | New dependency; if MinIO is on the same LAN as the Tomcat host already serving reads (`192.168.103.106`), this is likely low-risk, but **Unverified** — this audit cannot test connectivity from this environment | Confirm LAN reachability and typical latency from both the V1 and V2 ETL hosts to `192.168.103.106:9000` before cutover (§M); size retry/backoff accordingly |
| 11 | MinIO credentials expire/invalid | N/A today; read-side already tolerates missing credentials by sending unsigned requests (`object-store.ts:123-127`), which is itself a latent risk for a bucket that should not be public-write | Upload credentials must be write-scoped and **separate** from the read-only key already in use (the brief is correct to require this) | Use a dedicated MinIO access key/secret with `s3:PutObject`/`s3:GetObject` (for HEAD) on the `files` bucket only, provisioned by whoever administers MinIO; rotate independently of the read key |
| 12 | ETL restarts mid-batch | V1: thread pool simply doesn't finish all futures; next run re-queries `PENDING`/`FAILED`. V2: batched DB flush every 50 rows (`main.py:326-327,847-849`) means up to 49 already-written files can have stale `is_downloaded=FALSE` after a crash — self-healed on the next run by the disk-existence fast path and by `sync_files_state.py` | Same shape; MinIO HEAD replaces disk-listdir as the self-healing mechanism | No new design needed — same two safety nets (pre-flight existence check + a periodic reconciliation pass) carry over unchanged in spirit |

**On "preventing false-success database statuses" specifically**: the current V1/V2 code is already careful about this *for the source-download step* (atomic rename in V1; size-validation retry in V2) but has **zero** concept of a second, independent "stored durably" step, because today writing the local file *is* the durable step. The single most important discipline to carry into the new design is risk #4 above: **the DB row only becomes `DOWNLOADED`/`is_downloaded=TRUE` after the upload is independently confirmed (HEAD/ETag), never merely after the source HTTP GET returns 200.**

---

## K. Migration Plan

**Starting state (as given in the brief, Unverified from this audit per §A.6):** MinIO's `files` bucket already holds V1 objects synchronized from `/data-drive/etl-files` by an external process; V2's `/mnt/shared-etl-files` has not yet been synchronized into MinIO.

1. **Do not touch the existing filesystem sync service.** Leave it running exactly as-is through every step below — it is the current production read path's safety net, and this audit could not even confirm its implementation, let alone safely modify it.
2. **Stand up the shared upload client first, used by neither ETL yet.** A small internal library (Python, `boto3`-based per §F) with one function: `upload_and_confirm(bytes_or_stream, bucket, key) -> (etag, size)`, raising on any failure, doing the HEAD-confirm step from §J internally. No V1/V2 behavior changes in this step.
3. **V1 cutover**: change `apis/alfresco.py:download_media_file`'s write block (§C, `:86-129`) to call the new upload client with `key = f"{attach_clean}/{file_clean}"` instead of writing to `base_dir`. Keep every other function (discovery, queueing, status update, retry adapter) unchanged. Mark `DOWNLOADED` only after the upload client confirms (per §J risk #4). Run in parallel with the existing filesystem write for one full day's DAG run (write to **both** local disk and MinIO) before removing the local write, so the existing sync service and any other NFS consumer keep working during validation.
4. **V2 cutover**: same pattern inside `main.py:download_single_file`'s write block (§D, `:712-717`), `key` built from the existing `map_destination_subdir()` + `file_id` + extension. Also update `_find_existing_disk_path` (§D.4) to check MinIO via HEAD instead of (or in addition to, during the parallel-write period) `os.listdir()`.
5. **Existing V2 media migration**: once V2's uploader is confirmed working for *new* downloads, backfill the existing `/mnt/shared-etl-files` tree into MinIO with the exact same keys as §H — this can reuse `share_v1_media.py`'s resumable, hash-verified, "skip if already present and same size" pattern (§C.7) adapted to upload-instead-of-copy, rather than writing a new migration tool from scratch.
6. **ETL3 update**: extend `etl3/media/paths.py:_file_accessible` (or add a parallel MinIO-aware check used alongside it during the transition) so `availability_status` reflects MinIO object existence. Run this change *before* either ETL's local-write removal (step 7), so there is never a window where files are MinIO-only but ETL3 still only checks local disk.
7. **Retire local filesystem writes**, V1 then V2, only after: (a) step 6 has been live and verified against real data for a full consolidation cycle, (b) the existing sync service (or a manual one-off `mc mirror`) has been confirmed to have fully caught up, (c) spot-checks confirm `media_unified.availability_status = VERIFIED_ACCESSIBLE` rows are actually fetchable through `fileProxy.ts` end-to-end.
8. **Retire the external filesystem→MinIO sync service last**, only after step 7 for both V1 and V2, and only once nothing is writing to `/data-drive/etl-files` or `/mnt/shared-etl-files` anymore for new files (both local roots can be kept, read-only, as a rollback safety net for some retention period — see §L).

This sequencing directly satisfies the brief's 9 migration requirements: nothing is re-uploaded unnecessarily (step 5 skips existing-and-matching objects), V2 is migrated (step 5), keys are preserved exactly (§H), DB references stay valid (§I — no schema change), new downloads go straight to MinIO only after validation (steps 3-4 run parallel-write first), the sync service stays up throughout (step 1, step 8 is last), original filesystem data is never deleted by this plan (§L), and rollback stays possible at every step because the local write is only *removed*, never required to be *reverted*, until the very end.

---

## L. Rollback Plan

- **Before step 7 (local writes removed)**: trivial — nothing has changed about the read path or about what's on local disk; simply stop calling the new upload client, no data was deleted.
- **After step 7, before step 8 (local writes removed, sync service still running)**: still straightforward — the sync service (if it is confirmed to still be running and additive) continues mirroring MinIO-authoritative new files back toward local disk is not actually how the data flows (sync is filesystem→MinIO, not the reverse), so rollback here means: re-enable the local write path in the ETL code (revert the §K step 3/4 change) and let new downloads resume writing locally; already-uploaded MinIO-only objects remain servable since reads never depended on local disk.
- **After step 8 (sync service retired)**: rollback requires re-standing-up a filesystem→MinIO (or MinIO→filesystem, if something now needs local files again) bridge, or reverting ETL writes back to local disk as above — either way, **original filesystem data must not have been deleted** (§K explicitly does not delete `/data-drive/etl-files` or `/mnt/shared-etl-files` at any step) — the data already on local disk when migration started remains the rollback's safety net indefinitely, or at minimum for a defined retention window agreed with whoever owns the storage budget.
- **At every stage**: because reads go through `media_unified.resolved_relative_path` + `FILE_SERVER_BASE_URL` (§E), a rollback never requires a backend deploy or a `media_unified` data migration — only the ETL write-path code and (if needed) `FILE_SERVER_BASE_URL`'s target need to change.

---

## M. Performance and Infrastructure Assessment

**Current vs proposed:**

```
Current:  Source API → local/shared filesystem (streamed, chunked) → [external sync, ~5 min lag, unverified] → MinIO → DOPAMS read
Proposed: Source API → direct MinIO upload (streamed) → DOPAMS read
```

- **Streaming without full-memory buffering is achievable for both ETLs**: V1 already uses `response.iter_content(chunk_size=16384)` (`alfresco.py:111`) and V2 uses `chunk_size=8192` (`main.py:714`) — both patterns feed directly into `boto3`'s `upload_fileobj`, which itself streams from a file-like object, so neither ETL needs to buffer a whole file in memory to upload it (unlike `fileProxy.ts`'s existing `arrayBuffer()` read-side buffering, which is unrelated to this migration).
- **Throughput is bounded by the *source* APIs, not by MinIO, in both cases**: V1's bottleneck is Alfresco + 5 worker threads with urllib3 retry; V2's bottleneck is a deliberately throttled 5-10 requests/minute against the CCTNS V2 files API (`main.py:85-86`). Adding a MinIO PUT per file (same LAN, small files) is very unlikely to become the new bottleneck for either pipeline — but this audit cannot measure actual LAN latency to `192.168.103.106:9000` from the real ETL hosts (§J risk #10, Unverified).
- **Worker concurrency**: no change needed. V1 keeps its 5-thread pool; V2 keeps its strict single-threaded rate limiter — MinIO's own concurrency ceiling for a handful of files/minute is not a practical constraint.
- **Temporary disk usage**: can be reduced to zero for V1 (today it needs space for the `.tmp` file; a direct-stream-to-MinIO upload needs no local temp file at all) and stays at zero for V2 (it already streams without a temp file, it just writes the wrong destination type). Net effect: **less** local disk I/O than today, not more.
- **A case where local storage might still be needed**: if the completeness-check-by-re-read pattern (§J risk #8, optional MD5 integrity check) is implemented by reading the object back after upload rather than trusting MinIO's returned `ETag`, that would require buffering; recommendation is to trust the `ETag` instead and avoid this.
- **Operational simplification, not just equivalence**: V2's `start_downloader.sh` and `ensure_directory`'s `chmod 0o775`/tomcat-group requirement (`main.py:241-280`) — an entire POSIX-permissions/Unix-group dependency — disappears with object storage. This is a real, if secondary, benefit of the migration.
- **Live network probe (2026-11-26, from this Windows checkout machine, same LAN/VPN segment as the ETL repos' documented IPs)**: `192.168.103.106:8080` and `:9000` are both **open and answering**. Port 8080 returns Apache Tomcat 10.1.49's default welcome page on `GET /` (200 OK, `<title>Apache Tomcat/10.1.49</title>`) — `GET /files/` 404s, which is expected Tomcat behavior for a directory with no index file and directory-listing disabled, **not** evidence the `/files` content mapping is gone. Port 9000 identifies as `Server: MinIO`, `GET /minio/health/live` returns `200 OK`, and an unauthenticated `GET /files/` correctly returns an S3 `AccessDenied` XML error naming the `files` bucket — i.e. the bucket exists and anonymous access is correctly refused (contrary to `object-store.ts`'s unsigned-request fallback assuming a possibly-public bucket, §F). **This confirms both candidate backends are real, live infrastructure, not hypothetical** — it does not by itself prove which one `DOPAMS-BE`'s deployed `.env` currently points at (§O.3), and it was only tested from this machine, not from the actual Linux ETL hosts, so cross-host reachability (§J risk #10) remains otherwise unconfirmed.
- **Infrastructure items needing sysadmin involvement, not just application code** (all **Unverified** from this audit — no access to the hosts):
  - Confirm the real ETL host(s) (not just this dev machine) can reach `192.168.103.106:9000` (firewall/network segment) — now more likely given this machine's own successful probe, but not the same thing as confirming it from production.
  - Confirm available MinIO storage capacity for the backlog (all of `/mnt/shared-etl-files` not yet migrated) plus ongoing growth.
  - Provision a write-capable MinIO credential, scoped to the `files` bucket only, distinct from the existing read-only key.
  - Decide whether TLS is required/available for the MinIO endpoint (currently plain `http://`, per both the code default and the checked-out `.env`).
  - Decide retention/backup policy for the `files` bucket going forward (today's backup posture for `/data-drive/etl-files`/`/mnt/shared-etl-files` is likewise Unverified from this audit).
- **Items an application developer can implement without sysadmin involvement**: the upload client itself, the V1/V2 write-path changes, the ETL3 availability-check change, the parallel-write validation period.

---

## N. Implementation Roadmap

| Phase | Files/modules likely to change | Dependencies | Complexity | Risks | Testing | Rollback |
|---|---|---|---|---|---|---|
| 1. Shared MinIO upload layer | New module (e.g. `etl_common/object_store.py`), `requirements.txt`/venv for V1 and V2 (add `boto3`) | Write-capable MinIO credential provisioned | Low — new code, no existing behavior touched | Wrong endpoint/region config caught early if isolated here | Unit tests against a local MinIO/minio-mock or docker container; HEAD/PUT/confirm round-trip | Trivial — unused until wired into V1/V2 |
| 2. V1 direct upload | `cctns-v1/.../apis/alfresco.py` (`download_media_file`), `sync_media.py` (no change expected beyond passing a flag) | Phase 1 | Medium — must preserve atomic-rename-equivalent confirm semantics (§J #3-4) | Breaking the existing `.tmp`-then-rename idempotency guarantee if confirm-before-DB-update is implemented carelessly | Run against a staging/sandboxed Alfresco+MinIO pair; compare `media_unified` rows before/after for a sample of FIRs | Revert the one function; local-disk write resumes exactly as before |
| 3. V2 direct upload | `cctns-v2/etl-files/etl_files_media_server/etl_files_media_server/main.py` (`download_single_file`, `_find_existing_disk_path`), `sync_files_state.py` | Phase 1 | Medium — rate-limit logic, retry/backoff, and the 400/404-permanent-error classification must be preserved unchanged | V2's existing non-atomic write means the "confirm before DB update" discipline (§J #4) is a *new* guarantee being added here, not merely relocated — review carefully | Same as V1; additionally re-run `update_file_urls_with_extensions.py`-equivalent logic against MinIO-resolved keys to confirm extension handling still works | Revert the one function |
| 4. ETL3 MinIO compatibility | `etl3/media/paths.py` (`_file_accessible` or a parallel MinIO-aware twin), `etl3/merger/media_consolidate.py` (wiring only) | Phases 2-3 in progress (parallel-write period) | Medium — must not regress the existing V1_HISTORICAL/legacy-root fallback behavior | A bug here directly changes what the UI shows as "available" (§A.3) — highest blast-radius phase in this roadmap | Run consolidation against a snapshot with known MinIO-only and known local-only rows; assert `availability_status` matches expectations for both | Revert to local-disk-only check; MinIO-only files would show as unavailable until re-reverted — acceptable only as a short-lived rollback, not a steady state |
| 5. Existing V2 media migration | New one-off backfill script (adapt `share_v1_media.py`'s pattern, §K step 5) | Phase 3 validated | Medium — volume unknown (Unverified file count/size in `/mnt/shared-etl-files`) | Long-running job; must be resumable and must not re-upload already-present objects | Dry-run mode first (counts only), then apply with hash/size verification per object | Nothing destructive — objects already in MinIO are simply left as-is on re-run |
| 6. Validation and production rollout | None (operational) | Phases 1-5 | Low (process, not code) | Skipping validation is the main risk | End-to-end: trigger a real FIR/crime document upload through each ETL, confirm it appears correctly in the DOPAMS UI through `fileProxy.ts` | N/A — this phase is the gate before phase 7 |
| 7. Retirement of filesystem sync | External to both repos (§A.6) | All of the above complete and stable for an agreed soak period | Unknown — the service's own implementation is Unverified | Retiring an unverified service without understanding what else might depend on it | Confirm via the MinIO access logs (or equivalent) that no writes to the `files` bucket still originate from the sync service's source/IP before disabling it | Keep the local filesystem data; re-enable the sync service if anything unexpected surfaces |

---

## O. Open Questions

1. **Does `MINIO-MEDIA-HANDOFF.md` exist, and where?** Not found in `DOPAMS-ETL-dopams-cctns`, `DOPAMS-BE`, or `DOPAMS-FE`. If it exists only outside these repositories (wiki, ticket, or directly on a server), its contents should be obtained and reconciled with this report before planning further.
2. **What implements "sync `/data-drive/etl-files` → MinIO every 5 minutes," and is it actually running?** Not found in either repository. Confirm its implementation (cron? `mc mirror`? a systemd service? something else entirely?), its current health, and whether it is one-directional (filesystem→MinIO only, as the brief states) before relying on it during the parallel-write validation period (§K steps 3-4).
3. **Is production `FILE_SERVER_BASE_URL` currently MinIO (`:9000`) or legacy Tomcat (`:8080`)?** **Partially resolved**: both ports are confirmed live (§M) — Tomcat 10.1.49 on 8080, a healthy MinIO with the `files` bucket on 9000 — so this is a real choice between two real running services, not a hypothetical one. Which one the *deployed* `DOPAMS-BE`'s `.env` actually points at is still unconfirmed (this checkout's local `.env` is not proof of the production value).
4. **Is `FILES_TABLE` actually set to `file_media_bookkeeping` in the live V2 ETL environment?** `main.py` defaults to `"files"`, but the live schema strongly implies the override is in use (§D.7). No `.env`/`.env.example` confirming this was found.
5. **Does the `trigger_auto_generate_file_paths` trigger on `public.files` (per `migrate_trigger_preserve_extensions.sql`) still exist on a live `files` table anywhere, or is that migration file fully superseded by the `file_media_bookkeeping`-based trigger in the live schema dump?** (§D.6, §D.7) — affects nothing in this migration directly, but is worth resolving so no one is confused about which trigger governs which table during later schema work.
6. **What is the actual current volume (file count, total bytes) under `/mnt/shared-etl-files`?** Needed to size the Phase 5 backfill (§N) and to estimate MinIO storage headroom (§M) — not discoverable from source code.
7. **Network path from the V1 and V2 ETL hosts to `192.168.103.106:9000`**: a live probe from this dev checkout machine succeeded (TCP connect + MinIO health-check 200 OK, §M) — so the service itself is reachable from at least this network segment. Whether the actual Linux ETL hosts sit on that same segment, or behind a different firewall path, is still not verifiable from this environment.
8. **Backup/retention policy, both for the current local filesystem roots and for the MinIO `files` bucket going forward** — not found in either repository.
9. **Are `unified-search.ts`, `pending-profiles/photo.ts`, and `missing-profiles.ts` (§E.7) confirmed to follow the exact same `media_unified`-only read pattern verified in full for `unified-fir.ts`/`unified-accused.ts`/`unified-profile.ts`?** Grep-confirmed as likely; not read in full for this audit.
10. **Is there any enrichment/OCR/face-processing pipeline, anywhere in the broader DOPAMS stack, that reads V1/V2 media bytes directly?** None found in `etl3/`; absence elsewhere in the stack was not exhaustively verified (§E.9).

---

## Final Assessment

1. **Is direct MinIO integration technically feasible for V1?** Yes. One function (`alfresco.py:download_media_file`) owns the entire write path; everything else is independent of storage destination.
2. **Is direct MinIO integration technically feasible for V2?** Yes, with slightly more care — V2's write is currently non-atomic (§D.5, §J #3-4) and that gap should be closed (confirm-before-DB-update) as part of this change, not just carried forward.
3. **Can both pipelines use the same MinIO bucket safely?** Yes, in principle — their object-key prefixes do not collide today (§H) — but add an explicit guard rather than relying on the current accidental disjointness continuing forever.
4. **Can the existing object keys and database references be preserved?** Yes. `media_unified.resolved_relative_path` already stores paths shaped exactly like the proposed object keys (§E.4, §H); no `media_unified`, `cctns_media_files`, or `file_media_bookkeeping` schema change is required.
5. **What changes are required in ETL3?** One change: `etl3/media/paths.py`'s filesystem-existence check must be taught to check MinIO (§E.10, §K step 6). This is the single highest-blast-radius change in the whole migration because it directly feeds a user-visible document count and a per-document "available"/"pending" flag (§A.3, §E.7).
6. **Which downstream modules currently depend on physical files?** Only `etl3/media/paths.py` (via `os.stat()`) and V2's own `sync_files_state.py`/`update_file_urls_with_extensions.py` (via `os.listdir()`/`glob.glob()`). The DOPAMS backend depends on *URLs built from `media_unified`*, not on physical files directly — it was never a physical-file dependent to begin with (§E.7-8).
7. **Can the changes be implemented without disrupting existing ETL operations?** Yes, if done via the parallel-write period described in §K (write to both local disk and MinIO during validation) rather than a hard cutover.
8. **What is the safest implementation sequence?** Shared upload client → V1 (parallel-write) → V2 (parallel-write) → ETL3 MinIO-aware availability check → validate end-to-end in the real UI → backfill V2's existing media → retire local writes → retire the external sync service last (§K, §N).
9. **What are the major production risks?** (a) Marking a DB row "downloaded" before the MinIO upload is independently confirmed (§J #4) — the one genuinely new failure mode this migration introduces; (b) retiring the external sync service, or ETL3's availability check update, before the other is validated, producing a window where real files are invisible/pending in the UI (§A.3, §N phase 4/7); (c) object-key collisions if V1/V2 naming conventions ever drift without the guard recommended in §H.
10. **What must be completed before retiring the existing filesystem sync?** Both ETLs writing directly and exclusively to MinIO and confirmed stable; ETL3's availability check confirmed MinIO-aware and validated; the V2 backfill complete; end-to-end UI verification done; and, ideally, independent confirmation of what the sync service actually does today (§O.2), since this audit could not verify it.

**This audit recommends no implementation yet.** The findings above — especially §A.3/§E (the ETL3 availability-check dependency) and §A.6/§O.1-2 (the unverified external sync service and missing handoff document) — should be reviewed and the open questions in §O resolved or consciously accepted as risk before any code is written.
