"""Safe storage helpers for immutable point-in-time report archives.

The live reports remain normal JSON.  Older immutable checkpoints may be
stored as ``.json.gz`` after an exact byte-for-byte verification.  Readers use
this module so the storage representation never changes the learning input.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
import gzip
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable


ARCHIVE_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})-")
MANIFEST_VERSION = 1
WARNING_BYTES = 600 * 1024 * 1024
CRITICAL_BYTES = 750 * 1024 * 1024


def read_json_document(path: Path) -> dict[str, Any]:
    """Read a JSON object from plain JSON or gzip without changing semantics."""
    try:
        if path.name.endswith(".json.gz"):
            with gzip.open(path, "rt", encoding="utf-8") as stream:
                payload = json.load(stream)
        else:
            payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, gzip.BadGzipFile, json.JSONDecodeError, OSError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _logical_name(path: Path) -> str:
    return path.name[:-3] if path.name.endswith(".json.gz") else path.name


def iter_archive_documents(archive_dir: Path) -> list[Path]:
    """Return one readable document per checkpoint, preferring original JSON.

    During migration both forms can coexist.  Deduplication prevents a model
    from learning the same frozen checkpoint twice.
    """
    selected: dict[str, Path] = {}
    if not archive_dir.is_dir():
        return []
    candidates = list(archive_dir.glob("*.json")) + list(archive_dir.glob("*.json.gz"))
    for path in sorted(candidates, key=lambda item: item.name):
        logical = _logical_name(path)
        prior = selected.get(logical)
        if prior is None or (prior.name.endswith(".gz") and path.suffix == ".json"):
            selected[logical] = path
    return [selected[name] for name in sorted(selected)]


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _record_count(payload: dict[str, Any]) -> int:
    for key in ("data", "watchlist", "snapshots", "rows"):
        value = payload.get(key)
        if isinstance(value, list):
            return len(value)
    return 1


@dataclass(frozen=True)
class CompressionResult:
    source: str
    compressed: str
    source_sha256: str
    compressed_sha256: str
    source_bytes: int
    compressed_bytes: int
    record_count: int
    verified: bool
    source_removed: bool
    error: str = ""


def compress_verified_json(source: Path, *, remove_source: bool = False) -> CompressionResult:
    """Create a deterministic gzip copy and remove source only after verification."""
    destination = source.with_suffix(source.suffix + ".gz")
    base = {
        "source": source.name,
        "compressed": destination.name,
        "source_sha256": "",
        "compressed_sha256": "",
        "source_bytes": 0,
        "compressed_bytes": 0,
        "record_count": 0,
        "verified": False,
        "source_removed": False,
    }
    if source.suffix != ".json" or not source.is_file():
        return CompressionResult(**base, error="source_missing_or_not_json")
    try:
        raw = source.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("archive root must be a JSON object")
        base.update({
            "source_sha256": _sha256(raw),
            "source_bytes": len(raw),
            "record_count": _record_count(payload),
        })
        if destination.exists():
            with gzip.open(destination, "rb") as stream:
                existing = stream.read()
            if existing != raw:
                return CompressionResult(**base, error="compressed_copy_conflict")
        else:
            temporary = destination.with_suffix(destination.suffix + ".tmp")
            with temporary.open("wb") as output:
                with gzip.GzipFile(fileobj=output, mode="wb", compresslevel=9, mtime=0) as stream:
                    stream.write(raw)
            temporary.replace(destination)
        compressed_raw = destination.read_bytes()
        with gzip.open(destination, "rb") as stream:
            restored = stream.read()
        restored_payload = json.loads(restored.decode("utf-8"))
        verified = restored == raw and restored_payload == payload
        base.update({
            "compressed_sha256": _sha256(compressed_raw),
            "compressed_bytes": len(compressed_raw),
            "verified": verified,
        })
        if not verified:
            return CompressionResult(**base, error="round_trip_verification_failed")
        if remove_source:
            source.unlink()
            base["source_removed"] = True
        return CompressionResult(**base)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return CompressionResult(**base, error=f"{type(exc).__name__}: {exc}")


def archive_date(path: Path) -> date | None:
    match = ARCHIVE_DATE.match(_logical_name(path))
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def eligible_plain_archives(archive_dir: Path, *, as_of: date, older_than_days: int) -> list[Path]:
    cutoff = as_of - timedelta(days=max(0, older_than_days))
    result = []
    for path in sorted(archive_dir.glob("*.json")) if archive_dir.is_dir() else []:
        checkpoint_date = archive_date(path)
        if checkpoint_date is not None and checkpoint_date < cutoff:
            result.append(path)
    return result


def directory_bytes(directory: Path) -> int:
    if not directory.exists():
        return 0
    return sum(path.stat().st_size for path in directory.rglob("*") if path.is_file())


def capacity_level(size_bytes: int) -> str:
    if size_bytes >= CRITICAL_BYTES:
        return "critical"
    if size_bytes >= WARNING_BYTES:
        return "warning"
    return "ok"


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def load_manifest(path: Path) -> dict[str, Any]:
    payload = read_json_document(path)
    if int(payload.get("version") or 0) != MANIFEST_VERSION:
        return {"version": MANIFEST_VERSION, "entries": {}}
    entries = payload.get("entries")
    return {
        "version": MANIFEST_VERSION,
        "entries": entries if isinstance(entries, dict) else {},
    }


def update_manifest(
    manifest: dict[str, Any], results: Iterable[CompressionResult],
) -> dict[str, Any]:
    entries = dict(manifest.get("entries") or {})
    for item in results:
        if not item.verified or item.error:
            continue
        entries[item.compressed] = {
            "source": item.source,
            "compressed": item.compressed,
            "source_sha256": item.source_sha256,
            "compressed_sha256": item.compressed_sha256,
            "source_bytes": item.source_bytes,
            "compressed_bytes": item.compressed_bytes,
            "record_count": item.record_count,
        }
    return {"version": MANIFEST_VERSION, "entries": entries}


def audit_manifest(archive_dir: Path, manifest: dict[str, Any]) -> list[dict[str, str]]:
    """Recheck every compressed archive against its durable manifest entry."""
    errors: list[dict[str, str]] = []
    entries = manifest.get("entries") if isinstance(manifest.get("entries"), dict) else {}
    compressed_names = {
        path.name for path in archive_dir.glob("*.json.gz")
    } if archive_dir.is_dir() else set()
    for unknown in sorted(compressed_names - set(entries)):
        errors.append({"compressed": unknown, "error": "compressed_file_not_in_manifest"})
    for name, entry in sorted(entries.items()):
        path = archive_dir / name
        try:
            compressed_raw = path.read_bytes()
            if _sha256(compressed_raw) != str(entry.get("compressed_sha256") or ""):
                raise ValueError("compressed_sha256_mismatch")
            with gzip.open(path, "rb") as stream:
                restored = stream.read()
            if _sha256(restored) != str(entry.get("source_sha256") or ""):
                raise ValueError("source_sha256_mismatch")
            payload = json.loads(restored.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("archive_root_not_object")
            if _record_count(payload) != int(entry.get("record_count") or 0):
                raise ValueError("record_count_mismatch")
            source_name = str(entry.get("source") or "")
            source_path = archive_dir / source_name
            if source_path.exists() and source_path.read_bytes() != restored:
                raise ValueError("plain_and_compressed_content_mismatch")
        except (FileNotFoundError, gzip.BadGzipFile, OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            errors.append({"compressed": name, "error": str(exc)})
    return errors


def build_health(
    reports_dir: Path,
    archive_dir: Path,
    *,
    as_of: date,
    older_than_days: int,
    results: Iterable[CompressionResult] = (),
    audit_errors: Iterable[dict[str, str]] = (),
    eligible_count: int | None = None,
    mode: str = "check_only",
) -> dict[str, Any]:
    result_rows = [asdict(item) for item in results]
    errors = [item for item in result_rows if item.get("error") or not item.get("verified")]
    errors.extend(dict(item) for item in audit_errors)
    reports_size = directory_bytes(reports_dir)
    level = "critical" if errors else capacity_level(reports_size)
    return {
        "version": MANIFEST_VERSION,
        "checked_on": as_of.isoformat(),
        "mode": mode,
        "status": level,
        "reports_bytes": reports_size,
        "warning_bytes": WARNING_BYTES,
        "critical_bytes": CRITICAL_BYTES,
        "plain_archive_count": len(list(archive_dir.glob("*.json"))) if archive_dir.is_dir() else 0,
        "compressed_archive_count": len(list(archive_dir.glob("*.json.gz"))) if archive_dir.is_dir() else 0,
        "eligible_plain_count": (
            eligible_count if eligible_count is not None else len(eligible_plain_archives(
                archive_dir, as_of=as_of, older_than_days=older_than_days,
            ))
        ),
        "older_than_days": older_than_days,
        "verified_count": sum(bool(item.get("verified")) for item in result_rows),
        "source_removed_count": sum(bool(item.get("source_removed")) for item in result_rows),
        "errors": errors,
        "results": result_rows,
        "formal_v6_changed": False,
        "broker_orders": False,
    }
