"""Mark copied CCTNS V1 files as accessible in the shared Tomcat root.

Dry-run is the default. Pass --apply after share_v1_media.py has copied files
into /data-drive/etl-files. EMPTY_AT_SOURCE rows (Alfresco missing) are left
unchanged.
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus


DEFAULT_DESTINATION = Path("/data-drive/etl-files")
OLD_PREFIX = "/home/tganb/dopams/media_cctnsv1"
NEW_PREFIX = "/data-drive/etl-files"


@dataclass
class ReconcileStats:
    downloaded_rows: int = 0
    verified: int = 0
    already_verified: int = 0
    missing_on_disk: int = 0
    empty_at_source: int = 0
    updated_unified: int = 0
    updated_source: int = 0
    updated_v1_files: int = 0


def rewrite_local_path(path: str | None) -> str | None:
    if not path:
        return path
    if path.startswith(OLD_PREFIX):
        return NEW_PREFIX + path[len(OLD_PREFIX) :]
    return path


def shared_path(destination_root: Path, relative: str | None) -> Path | None:
    if not relative or not relative.strip():
        return None
    cleaned = relative.strip().lstrip("/")
    return destination_root / cleaned


def file_is_present(path: Path | None) -> bool:
    return bool(path and path.is_file() and path.stat().st_size > 0)


def unified_dsn() -> str:
    raw = os.environ.get("UNIFIED_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not raw:
        raise RuntimeError("UNIFIED_DATABASE_URL or DATABASE_URL is required")
    return raw.split("?", 1)[0]


def v1_dsn() -> str | None:
    raw = os.environ.get("V1_DATABASE_URL")
    if raw:
        return raw.split("?", 1)[0]
    host = os.environ.get("PG_HOST")
    user = os.environ.get("PG_USER")
    password = os.environ.get("PG_PASSWORD")
    if not host or not user or not password:
        return None
    return (
        f"postgresql://{quote_plus(user)}:{quote_plus(password)}"
        f"@{host}:{os.environ.get('PG_PORT', '5432')}"
        f"/{os.environ.get('PG_DATABASE', 'cctns_v1')}"
    )


def reconcile(
    destination_root: Path,
    *,
    apply: bool,
    unified_url: str,
    v1_url: str | None,
) -> ReconcileStats:
    stats = ReconcileStats()
    destination_root = destination_root.resolve(strict=True)
    import psycopg2
    from psycopg2.extras import Json

    unified = psycopg2.connect(unified_url)
    v1 = psycopg2.connect(v1_url) if v1_url else None
    try:
        with unified.cursor() as cur:
            cur.execute(
                """
                SELECT media_id, source_record_id, availability_status,
                       resolved_relative_path, source_local_path
                FROM media_unified
                WHERE source_system = 'V1'
                  AND parent_entity_type IN ('fir', 'court')
                """
            )
            rows = cur.fetchall()

        for media_id, source_record_id, status, relative, local_path in rows:
            if status == "EMPTY_AT_SOURCE":
                stats.empty_at_source += 1
                continue
            stats.downloaded_rows += 1
            present = file_is_present(shared_path(destination_root, relative))
            if not present:
                stats.missing_on_disk += 1
                continue
            if status == "VERIFIED_ACCESSIBLE" and str(local_path or "").startswith(NEW_PREFIX):
                stats.already_verified += 1
                continue
            stats.verified += 1
            if not apply:
                continue
            new_local = str(shared_path(destination_root, relative))
            with unified.cursor() as cur:
                cur.execute(
                    """
                    UPDATE media_unified
                    SET availability_status = 'VERIFIED_ACCESSIBLE',
                        availability_detail = %s,
                        source_local_path = %s
                    WHERE media_id = %s
                    """,
                    (
                        f"Verified in shared Tomcat file root {destination_root}",
                        new_local,
                        media_id,
                    ),
                )
                stats.updated_unified += cur.rowcount
                if source_record_id:
                    cur.execute(
                        """
                        SELECT payload FROM media_source
                        WHERE source_system = 'V1' AND source_record_id = %s
                        """,
                        (str(source_record_id),),
                    )
                    source_row = cur.fetchone()
                    if source_row and isinstance(source_row[0], dict):
                        payload: dict[str, Any] = dict(source_row[0])
                        payload["local_path"] = rewrite_local_path(payload.get("local_path")) or new_local
                        cur.execute(
                            """
                            UPDATE media_source
                            SET payload = %s
                            WHERE source_system = 'V1' AND source_record_id = %s
                            """,
                            (Json(payload), str(source_record_id)),
                        )
                        stats.updated_source += cur.rowcount
            if v1 and source_record_id and str(source_record_id).isdigit():
                with v1.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE cctns.cctns_media_files
                        SET local_path = %s, updated_at = CURRENT_TIMESTAMP
                        WHERE media_id = %s
                        """,
                        (new_local, int(source_record_id)),
                    )
                    stats.updated_v1_files += cur.rowcount

        if apply:
            unified.commit()
            if v1:
                v1.commit()
        else:
            unified.rollback()
            if v1:
                v1.rollback()
    finally:
        unified.close()
        if v1:
            v1.close()
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--json-report", type=Path)
    args = parser.parse_args()

    try:
        stats = reconcile(
            args.destination,
            apply=args.apply,
            unified_url=unified_dsn(),
            v1_url=v1_dsn(),
        )
    except (OSError, RuntimeError) as error:
        print(json.dumps({"error": str(error)}))
        return 2

    report = {"mode": "apply" if args.apply else "dry-run", **asdict(stats)}
    rendered = json.dumps(report, indent=2, sort_keys=True)
    print(rendered)
    if args.json_report:
        args.json_report.parent.mkdir(parents=True, exist_ok=True)
        args.json_report.write_text(rendered + "\n", encoding="utf-8")
    return 1 if stats.missing_on_disk else 0


if __name__ == "__main__":
    raise SystemExit(main())
