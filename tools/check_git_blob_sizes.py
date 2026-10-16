#!/usr/bin/env python3
"""Reject oversize staged blobs BEFORE creating an unpushable Git commit."""
from pathlib import Path
import subprocess

MAX_BLOB_BYTES = 95 * 1024 * 1024


def check_staged_blob_sizes(root: Path, limit: int = MAX_BLOB_BYTES) -> list[str]:
    index = subprocess.check_output(['git', 'ls-files', '--stage', '-z'], cwd=root)
    entries = []
    for record in index.split(b'\0'):
        if not record:
            continue
        metadata, path = record.split(b'\t', 1)
        mode, sha, stage = metadata.split()
        if mode != b'160000':
            entries.append((sha, path.decode('utf-8', errors='replace')))
    if not entries:
        return []
    sizes = subprocess.check_output(
        ['git', 'cat-file', '--batch-check=%(objectsize)'],
        input=b'\n'.join(sha for sha, _ in entries) + b'\n', cwd=root,
    ).splitlines()
    return [f'{path}: {int(size)} bytes (limit {limit})'
            for (_, path), size in zip(entries, sizes, strict=True) if int(size) >= limit]


if __name__ == '__main__':
    errors = check_staged_blob_sizes(Path.cwd())
    if errors:
        raise SystemExit('Refusing oversized Git blobs; preserve history and repair storage:\n' + '\n'.join(errors))
