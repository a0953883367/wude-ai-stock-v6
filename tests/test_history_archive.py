from __future__ import annotations

from datetime import date
import gzip
import json
from pathlib import Path

from history_archive import (
    audit_manifest,
    build_health,
    compress_verified_json,
    iter_archive_documents,
    load_manifest,
    read_json_document,
    update_manifest,
    write_json_atomic,
)


def _write(path: Path, marker: str, count: int = 3) -> bytes:
    payload = {"period": "evening", "marker": marker, "data": [{"id": i} for i in range(count)]}
    raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    path.write_bytes(raw)
    return raw


def test_verified_gzip_round_trip_keeps_source_by_default(tmp_path: Path) -> None:
    source = tmp_path / "2026-01-01-evening.json"
    raw = _write(source, "frozen")

    result = compress_verified_json(source)

    assert result.verified is True
    assert result.source_removed is False
    assert result.record_count == 3
    assert source.read_bytes() == raw
    with gzip.open(tmp_path / "2026-01-01-evening.json.gz", "rb") as stream:
        assert stream.read() == raw
    assert read_json_document(tmp_path / "2026-01-01-evening.json.gz")["marker"] == "frozen"


def test_source_is_removed_only_after_verified_round_trip(tmp_path: Path) -> None:
    source = tmp_path / "2026-01-01-evening.json"
    _write(source, "frozen")

    result = compress_verified_json(source, remove_source=True)

    assert result.verified is True and result.source_removed is True
    assert not source.exists()
    assert read_json_document(tmp_path / "2026-01-01-evening.json.gz")["marker"] == "frozen"


def test_conflicting_gzip_never_removes_original(tmp_path: Path) -> None:
    source = tmp_path / "2026-01-01-evening.json"
    raw = _write(source, "original")
    with gzip.open(source.with_suffix(".json.gz"), "wt", encoding="utf-8") as stream:
        json.dump({"marker": "different"}, stream)

    result = compress_verified_json(source, remove_source=True)

    assert result.verified is False
    assert result.error == "compressed_copy_conflict"
    assert source.read_bytes() == raw


def test_migration_reader_deduplicates_and_prefers_plain_json(tmp_path: Path) -> None:
    source = tmp_path / "2026-01-01-evening.json"
    _write(source, "plain")
    compress_verified_json(source)
    compressed_only = tmp_path / "2026-01-02-evening.json"
    _write(compressed_only, "gzip")
    compress_verified_json(compressed_only, remove_source=True)

    paths = iter_archive_documents(tmp_path)

    assert [path.name for path in paths] == [
        "2026-01-01-evening.json",
        "2026-01-02-evening.json.gz",
    ]


def test_health_is_non_destructive_and_reports_eligible_files(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    archive = reports / "archive"
    archive.mkdir(parents=True)
    source = archive / "2026-01-01-evening.json"
    raw = _write(source, "old")

    health = build_health(
        reports, archive, as_of=date(2026, 2, 15), older_than_days=30,
    )

    assert health["status"] == "ok"
    assert health["eligible_plain_count"] == 1
    assert health["verified_count"] == 0
    assert health["source_removed_count"] == 0
    assert source.read_bytes() == raw


def test_manifest_rechecks_compressed_content_after_source_is_gone(tmp_path: Path) -> None:
    source = tmp_path / "2026-01-01-evening.json"
    _write(source, "frozen")
    result = compress_verified_json(source)
    manifest_path = tmp_path / "manifest.json"
    manifest = update_manifest(load_manifest(manifest_path), [result])
    write_json_atomic(manifest_path, manifest)
    source.unlink()

    assert audit_manifest(tmp_path, load_manifest(manifest_path)) == []

    compressed = tmp_path / result.compressed
    raw = bytearray(compressed.read_bytes())
    raw[-1] ^= 1
    compressed.write_bytes(bytes(raw))
    errors = audit_manifest(tmp_path, load_manifest(manifest_path))
    assert errors and errors[0]["compressed"] == result.compressed


def test_unmanifested_compressed_file_is_an_error(tmp_path: Path) -> None:
    with gzip.open(tmp_path / "2026-01-01-evening.json.gz", "wt", encoding="utf-8") as stream:
        json.dump({"data": []}, stream)

    assert audit_manifest(tmp_path, {"version": 1, "entries": {}}) == [{
        "compressed": "2026-01-01-evening.json.gz",
        "error": "compressed_file_not_in_manifest",
    }]
