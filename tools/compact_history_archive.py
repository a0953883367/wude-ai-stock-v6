#!/usr/bin/env python3
"""Audit or safely compress immutable report archives.

The default is deliberately non-destructive: it only audits.  ``--compress``
creates verified gzip copies while keeping JSON sources.  Source removal
requires both ``--compress`` and the explicit ``--remove-source-after-verify``.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from history_archive import (  # noqa: E402
    audit_manifest,
    build_health,
    compress_verified_json,
    eligible_plain_archives,
    load_manifest,
    update_manifest,
    write_json_atomic,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive-dir", type=Path, default=Path("reports/archive"))
    parser.add_argument("--reports-dir", type=Path, default=Path("reports"))
    parser.add_argument("--health-file", type=Path, default=Path("reports/history_archive_health.json"))
    parser.add_argument("--manifest-file", type=Path, default=Path("reports/history_archive_manifest.json"))
    parser.add_argument("--older-than-days", type=int, default=30)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--compress", action="store_true")
    parser.add_argument("--remove-source-after-verify", action="store_true")
    args = parser.parse_args()
    if args.remove_source_after_verify and not args.compress:
        parser.error("--remove-source-after-verify requires --compress")

    candidates = eligible_plain_archives(
        args.archive_dir, as_of=args.as_of, older_than_days=args.older_than_days,
    )
    results = []
    if args.compress:
        for source in candidates:
            # Manifest durability is established before any source can be
            # removed. A crash can therefore leave duplicates, never data loss.
            results.append(compress_verified_json(source, remove_source=False))
    manifest = update_manifest(load_manifest(args.manifest_file), results)
    if args.compress:
        write_json_atomic(args.manifest_file, manifest)
    audit_errors = audit_manifest(args.archive_dir, manifest)
    if args.remove_source_after_verify and not audit_errors:
        removed_results = []
        for item in results:
            source = args.archive_dir / item.source
            if item.verified and not item.error and source.exists():
                source.unlink()
                item = replace(item, source_removed=True)
            removed_results.append(item)
        results = removed_results
    mode = (
        "compress_and_remove_verified_source"
        if args.remove_source_after_verify
        else "compress_keep_source" if args.compress else "check_only"
    )
    health = build_health(
        args.reports_dir,
        args.archive_dir,
        as_of=args.as_of,
        older_than_days=args.older_than_days,
        results=results,
        audit_errors=audit_errors,
        eligible_count=len(candidates),
        mode=mode,
    )
    write_json_atomic(args.health_file, health)
    print(
        f"history archive: status={health['status']} mode={mode} "
        f"eligible={health['eligible_plain_count']} verified={health['verified_count']} "
        f"removed={health['source_removed_count']}"
    )
    return 1 if health["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
