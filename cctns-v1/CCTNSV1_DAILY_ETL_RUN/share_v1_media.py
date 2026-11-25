"""Safely publish downloaded CCTNS V1 media into the shared Tomcat file root.

The operation is resumable: an existing destination file with the same size is
treated as already copied. Conflicting files are reported and never overwritten.
Dry-run is the default; pass --apply to copy files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator


DEFAULT_SOURCE = Path("/home/tganb/dopams/media_cctnsv1")
DEFAULT_DESTINATION = Path("/data-drive/etl-files")


@dataclass
class MigrationStats:
    discovered_files: int = 0
    discovered_bytes: int = 0
    copied_files: int = 0
    copied_bytes: int = 0
    existing_files: int = 0
    existing_bytes: int = 0
    conflicts: int = 0
    failures: int = 0


def iter_files(root: Path) -> Iterator[Path]:
    for directory, dirnames, filenames in os.walk(root):
        dirnames.sort()
        filenames.sort()
        base = Path(directory)
        for filename in filenames:
            path = base / filename
            if path.is_file() and not path.is_symlink():
                yield path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def same_file(source: Path, destination: Path, verify_hash: bool) -> bool:
    if source.stat().st_size != destination.stat().st_size:
        return False
    return not verify_hash or sha256(source) == sha256(destination)


def atomic_copy(source: Path, destination: Path, verify_hash: bool) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.v1-sharing-{os.getpid()}.tmp")
    try:
        shutil.copy2(source, temporary)
        if not same_file(source, temporary, verify_hash):
            raise OSError("copied file failed verification")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def migrate(
    source_root: Path,
    destination_root: Path,
    *,
    apply: bool,
    verify_hash: bool,
) -> MigrationStats:
    stats = MigrationStats()
    source_root = source_root.resolve(strict=True)
    destination_root = destination_root.resolve(strict=True)

    if source_root == destination_root or destination_root.is_relative_to(source_root):
        raise ValueError("destination must not be the source directory or a child of it")

    for source in iter_files(source_root):
        relative = source.relative_to(source_root)
        destination = destination_root / relative
        size = source.stat().st_size
        stats.discovered_files += 1
        stats.discovered_bytes += size

        if destination.exists():
            if destination.is_file() and same_file(source, destination, verify_hash):
                stats.existing_files += 1
                stats.existing_bytes += size
            else:
                stats.conflicts += 1
                print(f"CONFLICT\t{relative}", file=sys.stderr)
            continue

        if not apply:
            continue

        try:
            atomic_copy(source, destination, verify_hash)
            stats.copied_files += 1
            stats.copied_bytes += size
        except OSError as error:
            stats.failures += 1
            print(f"FAILED\t{relative}\t{error}", file=sys.stderr)

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    parser.add_argument("--apply", action="store_true", help="copy files; otherwise perform a dry-run")
    parser.add_argument(
        "--verify-hash",
        action="store_true",
        help="compare SHA-256 for existing and newly copied files (slower)",
    )
    parser.add_argument("--json-report", type=Path)
    args = parser.parse_args()

    try:
        stats = migrate(
            args.source,
            args.destination,
            apply=args.apply,
            verify_hash=args.verify_hash,
        )
    except (OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2

    report = {
        "mode": "apply" if args.apply else "dry-run",
        "source": str(args.source),
        "destination": str(args.destination),
        "verify_hash": args.verify_hash,
        **asdict(stats),
    }
    rendered = json.dumps(report, indent=2, sort_keys=True)
    print(rendered)
    if args.json_report:
        args.json_report.parent.mkdir(parents=True, exist_ok=True)
        args.json_report.write_text(rendered + "\n", encoding="utf-8")

    return 1 if stats.conflicts or stats.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
