from pathlib import Path
import sys

import pytest

tools_dir = Path(__file__).resolve().parents[1] / "tools"
if tools_dir.exists():
    sys.path.insert(0, str(tools_dir))

from google_drive_archive import (
    ArchiveConflictError,
    ArchiveResult,
    DriveArchiveClient,
    archive_payload,
    gzip_bytes,
    quote_drive_query,
    write_result,
)


def test_archive_workflow_runs_one_file_after_archive_code_merge() -> None:
    workflow = Path(".github/workflows/google-drive-archive.yml").read_text(
        encoding="utf-8"
    )

    assert "push:" in workflow
    assert "branches: [main]" in workflow
    assert 'github.event_name }}" = "push"' in workflow
    assert 'echo "limit=1"' in workflow


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


def test_existing_file_without_sha_metadata_uses_drive_checksum(
    tmp_path: Path, monkeypatch
):
    source = tmp_path / "2026-09-05-evening.json"
    source.write_bytes(b'{"ok": true}\n')
    target_name, payload = archive_payload(source)
    import hashlib

    existing = {
        "id": "legacy-file",
        "name": target_name,
        "size": str(len(payload)),
        "md5Checksum": hashlib.md5(payload, usedforsecurity=False).hexdigest(),
        "appProperties": {},
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
        lambda *args, **kwargs: pytest.fail("matching legacy file was uploaded"),
    )

    result = client.archive_file(source, "archive")

    assert result.status == "verified_existing"
    assert result.drive_file_id == "legacy-file"


def test_existing_different_file_is_preserved_as_revision(
    tmp_path: Path, monkeypatch
):
    source = tmp_path / "2026-09-05-noon.json"
    source.write_bytes(b'{"ok": true}\n')
    target_name, payload = archive_payload(source)
    import hashlib

    sha256 = hashlib.sha256(payload).hexdigest()
    revision_name = f"2026-09-05-noon.revision-{sha256[:12]}.json.gz"
    client = DriveArchiveClient("id", "secret", "refresh")
    monkeypatch.setattr(
        client,
        "ensure_archive_path",
        lambda root, year, month: {"id": "month-folder"},
    )
    canonical = {
            "id": "file-2",
            "size": "1",
            "md5Checksum": "different",
            "appProperties": {"sha256": "different"},
    }
    monkeypatch.setattr(
        client,
        "find_file",
        lambda name, parent: canonical if name == target_name else None,
    )
    uploaded_names = []
    monkeypatch.setattr(
        client,
        "upload_verified",
        lambda name, data, parent, **kwargs: (
            uploaded_names.append(name) or {"id": "revision-file"}
        ),
    )

    result = client.archive_file(source, "archive")

    assert uploaded_names == [revision_name]
    assert result.status == "conflict_revision_uploaded_verified"
    assert result.drive_file_id == "revision-file"
    assert result.warning


def test_existing_revision_with_unexpected_content_stops(tmp_path: Path, monkeypatch):
    source = tmp_path / "2026-09-05-noon.json"
    source.write_bytes(b'{"ok": true}\n')
    target_name, payload = archive_payload(source)
    import hashlib

    revision_name = DriveArchiveClient._revision_name(
        target_name, hashlib.sha256(payload).hexdigest()
    )
    client = DriveArchiveClient("id", "secret", "refresh")
    monkeypatch.setattr(
        client,
        "ensure_archive_path",
        lambda root, year, month: {"id": "month-folder"},
    )

    def find_file(name, parent):
        if name in (target_name, revision_name):
            return {
                "id": name,
                "size": "1",
                "md5Checksum": "different",
                "appProperties": {"sha256": "different"},
            }
        return None

    monkeypatch.setattr(client, "find_file", find_file)
    monkeypatch.setattr(
        client,
        "upload_verified",
        lambda *args, **kwargs: pytest.fail("unexpected revision was overwritten"),
    )

    with pytest.raises(ArchiveConflictError):
        client.archive_file(source, "archive")


def test_result_is_warning_for_preserved_conflict_revision(tmp_path: Path):
    result_file = tmp_path / "result.json"
    write_result(
        result_file,
        [
            ArchiveResult(
                source="local.json",
                destination="archive/revision.json.gz",
                status="conflict_revision_uploaded_verified",
                size=10,
                sha256="abc",
                warning="canonical preserved",
            )
        ],
        [],
    )
    import json

    payload = json.loads(result_file.read_text(encoding="utf-8"))
    assert payload["status"] == "warning"
    assert payload["counts"]["conflict_revisions"] == 1
    assert payload["counts"]["errors"] == 0
