"""Lossless storage for the complete prediction ledger, never a retention policy.

Small/test ledgers retain the legacy JSON path. Large ledgers use deterministic
standard gzip at the same logical path plus ``.gz``. Corrupt or ambiguous
storage fails closed so a writer cannot silently replace history with emptiness.
"""
from __future__ import annotations

import gzip
import json
import zlib
from pathlib import Path
from typing import Any

COMPRESS_AT_BYTES = 8 * 1024 * 1024
MAX_STORED_BYTES = 80 * 1024 * 1024


class HistoryStorageError(RuntimeError):
    """Existing history is unreadable, ambiguous, or exceeds safe Git capacity."""


def _decode(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict) or not isinstance(value.get("snapshots", []), list):
            raise ValueError("invalid history shape")
        if "version" in value:
            int(value["version"])
        return value
    except (UnicodeError, ValueError, TypeError) as exc:
        raise HistoryStorageError("Prediction history is invalid; refusing to reset it") from exc


def read_prediction_history(path: Path) -> dict[str, Any]:
    """Read the logical JSON ledger in either representation; absent means new."""
    compressed = path.with_suffix(path.suffix + ".gz")
    try:
        plain = path.read_bytes() if path.exists() else None
        packed = gzip.decompress(compressed.read_bytes()) if compressed.exists() else None
    except (OSError, EOFError, ValueError, zlib.error) as exc:
        raise HistoryStorageError("Prediction history cannot be read; refusing to reset it") from exc
    if plain is not None and packed is not None and plain != packed:
        raise HistoryStorageError("Conflicting plain/compressed prediction history; preserve both for recovery")
    raw = plain if plain is not None else packed
    return _decode(raw) if raw is not None else {}


def write_prediction_history(path: Path, payload: dict[str, Any]) -> None:
    """Verify bytes before atomic replacement; remove no records or fields."""
    # Independently refuse to overwrite corrupt/conflicting existing storage.
    read_prediction_history(path)
    raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    _decode(raw)
    compressed = path.with_suffix(path.suffix + ".gz")
    # Once migrated, retain compressed storage even if a caller writes less.
    use_gzip = len(raw) >= COMPRESS_AT_BYTES or compressed.exists()
    destination = compressed if use_gzip else path
    stored = gzip.compress(raw, compresslevel=9, mtime=0) if use_gzip else raw
    if len(stored) >= MAX_STORED_BYTES:
        raise HistoryStorageError("Prediction history exceeds safe Git capacity; existing history preserved")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    try:
        temporary.write_bytes(stored)
        check = temporary.read_bytes()
        restored = gzip.decompress(check) if use_gzip else check
        if restored != raw or _decode(restored) != payload:
            raise HistoryStorageError("Prediction history round-trip verification failed")
        temporary.replace(destination)
        if use_gzip and path.exists():
            path.unlink()
    finally:
        temporary.unlink(missing_ok=True)
