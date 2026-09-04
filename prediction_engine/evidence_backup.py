"""Verified private backup for the complete shadow-learning database.

The backup is intentionally stored under ``.prediction_engine`` so it is
never published by GitHub Pages or committed to the public reports tree.  It
contains the logical SQLite rows in deterministic gzip JSON, which makes it
portable and independently verifiable without changing any formal model.
"""

from __future__ import annotations

from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

from .storage import PredictionStore, SCHEMA_VERSION


BACKUP_VERSION = 1
TABLE_ORDER = (
    "metadata",
    "market_sessions",
    "prices",
    "predictions",
    "model_versions",
    "portfolios",
    "portfolio_positions",
    "source_usage",
    "model_control",
    "model_control_events",
    "unit_learning_predictions",
    "unit_trust_control",
    "unit_trust_events",
    "forward_outcome_cohorts",
    "forward_outcome_candidates",
    "forward_outcome_results",
)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _atomic_bytes(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(raw)
    temporary.replace(path)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    _atomic_bytes(
        path,
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8"),
    )


def logical_snapshot(store: PredictionStore) -> dict[str, list[dict[str, Any]]]:
    """Return every governed learning row in a deterministic representation."""
    tables: dict[str, list[dict[str, Any]]] = {}
    with store.connect() as db:
        integrity = str(db.execute("PRAGMA integrity_check").fetchone()[0])
        if integrity != "ok":
            raise ValueError(f"prediction database integrity check failed: {integrity}")
        available = {
            str(row[0]) for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        missing = [name for name in TABLE_ORDER if name not in available]
        if missing:
            raise ValueError(f"prediction database is missing tables: {','.join(missing)}")
        for name in TABLE_ORDER:
            columns = [str(row[1]) for row in db.execute(f"PRAGMA table_info({name})")]
            order = ",".join(f'"{column}"' for column in columns)
            rows = db.execute(f'SELECT * FROM "{name}" ORDER BY {order}').fetchall()
            tables[name] = [dict(row) for row in rows]
    return tables


def create_verified_backup(
    store: PredictionStore,
    backup_path: Path,
    *,
    manifest_path: Path | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Write deterministic gzip JSON and verify an exact round trip."""
    backup_path = Path(backup_path)
    manifest_path = Path(manifest_path or backup_path.with_suffix(backup_path.suffix + ".manifest.json"))
    tables = logical_snapshot(store)
    payload = {
        "backup_version": BACKUP_VERSION,
        "database_schema_version": SCHEMA_VERSION,
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        "formal_v6_modified": False,
        "automatic_orders": False,
        "tables": tables,
    }
    raw = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    temporary = backup_path.with_suffix(backup_path.suffix + ".tmp")
    temporary.parent.mkdir(parents=True, exist_ok=True)
    with temporary.open("wb") as output:
        with gzip.GzipFile(fileobj=output, mode="wb", compresslevel=9, mtime=0) as stream:
            stream.write(raw)
    compressed = temporary.read_bytes()
    restored = gzip.decompress(compressed)
    if restored != raw:
        temporary.unlink(missing_ok=True)
        raise ValueError("prediction evidence backup round-trip mismatch")
    decoded = json.loads(restored.decode("utf-8"))
    if decoded != payload:
        temporary.unlink(missing_ok=True)
        raise ValueError("prediction evidence backup JSON verification failed")
    temporary.replace(backup_path)
    counts = {name: len(rows) for name, rows in tables.items()}
    manifest = {
        "backup_version": BACKUP_VERSION,
        "created_at": payload["created_at"],
        "backup_file": backup_path.name,
        "raw_bytes": len(raw),
        "compressed_bytes": len(compressed),
        "compression_reduction_pct": round((1 - len(compressed) / max(1, len(raw))) * 100, 2),
        "raw_sha256": _sha256(raw),
        "compressed_sha256": _sha256(compressed),
        "table_counts": counts,
        "verified": True,
        "private_backup": True,
        "formal_v6_modified": False,
        "automatic_orders": False,
    }
    _atomic_json(manifest_path, manifest)
    return manifest


def _verified_payload(backup_path: Path, manifest_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    compressed = backup_path.read_bytes()
    if _sha256(compressed) != str(manifest.get("compressed_sha256") or ""):
        raise ValueError("prediction evidence compressed checksum mismatch")
    try:
        raw = gzip.decompress(compressed)
    except (gzip.BadGzipFile, OSError) as exc:
        raise ValueError("prediction evidence backup is not valid gzip") from exc
    if _sha256(raw) != str(manifest.get("raw_sha256") or ""):
        raise ValueError("prediction evidence raw checksum mismatch")
    payload = json.loads(raw.decode("utf-8"))
    if int(payload.get("backup_version") or 0) != BACKUP_VERSION:
        raise ValueError("unsupported prediction evidence backup version")
    if int(payload.get("database_schema_version") or 0) != SCHEMA_VERSION:
        raise ValueError("prediction evidence schema version mismatch")
    tables = payload.get("tables")
    if not isinstance(tables, dict) or any(name not in tables for name in TABLE_ORDER):
        raise ValueError("prediction evidence backup is incomplete")
    counts = {name: len(tables[name]) for name in TABLE_ORDER}
    if counts != manifest.get("table_counts"):
        raise ValueError("prediction evidence table count mismatch")
    return payload, manifest


def restore_verified_backup(
    store: PredictionStore,
    backup_path: Path,
    *,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    """Restore a verified backup into an otherwise empty initialized store."""
    backup_path = Path(backup_path)
    manifest_path = Path(manifest_path or backup_path.with_suffix(backup_path.suffix + ".manifest.json"))
    payload, manifest = _verified_payload(backup_path, manifest_path)
    tables = payload["tables"]
    with store.connect() as db:
        occupied = {
            name: int(db.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])
            for name in TABLE_ORDER if name != "metadata"
        }
        if any(occupied.values()):
            raise ValueError("refusing to restore over a non-empty prediction database")
        for name in TABLE_ORDER:
            rows = tables[name]
            if not rows:
                continue
            allowed = {str(row[1]) for row in db.execute(f"PRAGMA table_info({name})")}
            columns = list(rows[0])
            if not columns or any(column not in allowed for column in columns):
                raise ValueError(f"prediction evidence contains invalid columns for {name}")
            if any(list(row) != columns for row in rows):
                raise ValueError(f"prediction evidence column order changed within {name}")
            placeholders = ",".join("?" for _ in columns)
            names = ",".join(f'"{column}"' for column in columns)
            db.executemany(
                f'INSERT OR REPLACE INTO "{name}" ({names}) VALUES ({placeholders})',
                [tuple(row[column] for column in columns) for row in rows],
            )
    restored = logical_snapshot(store)
    expected_counts = manifest["table_counts"]
    actual_counts = {name: len(restored[name]) for name in TABLE_ORDER}
    if actual_counts != expected_counts:
        raise ValueError("prediction evidence restore count verification failed")
    return {
        "restored": True,
        "backup_file": backup_path.name,
        "created_at": payload.get("created_at"),
        "table_counts": actual_counts,
        "integrity_check": "ok",
        "formal_v6_modified": False,
        "automatic_orders": False,
    }


def open_store_with_private_backup(
    database_path: Path,
    *,
    max_bytes: int,
) -> tuple[PredictionStore, dict[str, Any]]:
    """Open the store and recover from its private sidecar only when empty."""
    database_path = Path(database_path)
    store = PredictionStore(database_path, max_bytes=max_bytes)
    backup_path = database_path.parent / "prediction_evidence_backup.json.gz"
    manifest_path = backup_path.with_suffix(backup_path.suffix + ".manifest.json")
    if store.session_count() or not (backup_path.is_file() and manifest_path.is_file()):
        return store, {"attempted": False, "restored": False}
    result = restore_verified_backup(store, backup_path, manifest_path=manifest_path)
    return store, {"attempted": True, **result}
