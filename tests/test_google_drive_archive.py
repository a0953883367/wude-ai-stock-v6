from pathlib import Path
import sys

import pytest

tools_dir = Path(__file__).resolve().parents[1] / "tools"
if tools_dir.exists():
    sys.path.insert(0, str(tools_dir))

from google_drive_archive import (
    ArchiveConflictError,
    DriveArchiveClient,
    archive_payload,
    gzip_bytes,
    quote_drive_query,
)


def test_gzip_bytes_is_deterministic_and_round_trips():
    first = gzip_bytes(b'{"answer": 42}\n')
    second = gzip_bytes(b'{"answer": 42}\n')
    assert first == second
    import gzip

    assert gzip.decompress(first) == b'{"answer": 42}\n'


def test_archive_payload_keeps_gzip_and_compresses_json(tmp_path: Path):
    source = tmp_path / "2026-09-05-morning.json"
    source.write_bytes(b'{"ok": true}\n')
    name, payload = archive_payload(source)
    assert name == "2026-09-05-morning.json.gz"
    assert payload == gzip_bytes(source.read_bytes())

    compressed = tmp_path / name
    compressed.write_bytes(payload)
    kept_name, kept_payload = archive_payload(compressed)
    assert kept_name == name
    assert kept_payload == payload


def test_quote_drive_query_escapes_backslash_and_quote():
    assert quote_drive_query("a\\b'c") == "a\\\\b\\'c"


def test_existing_verified_file_is_not_uploaded(tmp_path: Path, monkeypatch):
    source = tmp_path / "2026-09-05-evening.json"
    source.write_bytes(b'{"ok": true}\n')
    target_name, payload = archive_payload(source)
    import hashlib

    existing = {
        "id": "file-1",
        "name": target_name,
        "size": str(len(payload)),
        "md5Checksum": hashlib.md5(payload, usedforsecurity=False).hexdigest(),
        "appProperties": {"sha256": hashlib.sha256(payload).hexdigest()},
    }
    client = DriveArchiveClient("id", "secret", "refresh")
    monkeypatch.setattr(
        client,
        "ensure_archive_path",
        lambda root, year, month: {"id": "month-folder"},
    )
    monkeypatch.setattr(client, "find_file", lambda name, parent: existing)
    monkeypatch.setattr(
        client,
        "upload_verified",
        lambda *args, **kwargs: pytest.fail("duplicate was uploaded"),
    )
    result = client.archive_file(source, "archive")
    assert result.status == "verified_existing"
    assert result.drive_file_id == "file-1"


def test_existing_different_file_stops_without_overwrite(tmp_path: Path, monkeypatch):
    source = tmp_path / "2026-09-05-noon.json"
    source.write_bytes(b'{"ok": true}\n')
    client = DriveArchiveClient("id", "secret", "refresh")
    monkeypatch.setattr(
        client,
        "ensure_archive_path",
        lambda root, year, month: {"id": "month-folder"},
    )
    monkeypatch.setattr(
        client,
        "find_file",
        lambda name, parent: {
            "id": "file-2",
            "size": "1",
            "md5Checksum": "different",
            "appProperties": {"sha256": "different"},
        },
    )
    monkeypatch.setattr(
        client,
        "upload_verified",
        lambda *args, **kwargs: pytest.fail("conflict was overwritten"),
    )
    with pytest.raises(ArchiveConflictError):
        client.archive_file(source, "archive")
