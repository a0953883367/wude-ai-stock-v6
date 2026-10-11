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



def _uploaded_metadata(payload):
    import hashlib
    return {
        "id": "uploaded-file", "size": str(len(payload)),
        "md5Checksum": hashlib.md5(payload, usedforsecurity=False).hexdigest(),
        "appProperties": {"sha256": hashlib.sha256(payload).hexdigest()},
    }


def _http_error(status):
    import requests
    response = requests.Response()
    response.status_code = status
    return requests.HTTPError("test failure", response=response)


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_transient_upload_retries_after_lookup(status, monkeypatch):
    client = DriveArchiveClient("id", "secret", "refresh")
    events = []
    payload = b"archive"
    def upload(*args, **kwargs):
        events.append("upload")
        if events.count("upload") == 1:
            raise _http_error(status)
        return _uploaded_metadata(payload)
    monkeypatch.setattr(client, "_upload_verified_once", upload)
    monkeypatch.setattr(client, "find_file", lambda *a: events.append("lookup"))
    monkeypatch.setattr("google_drive_archive.time.sleep", lambda delay: events.append(delay))
    result = client.upload_verified("name", payload, "parent", source="source")
    assert result["id"] == "uploaded-file"
    assert events == ["upload", 1, "lookup", "upload"]


def test_ambiguous_upload_checks_existing_instead_of_duplicate(monkeypatch):
    client = DriveArchiveClient("id", "secret", "refresh")
    calls = []
    payload = b"archive"
    def upload(*args, **kwargs):
        calls.append("upload")
        raise _http_error(502)
    monkeypatch.setattr(client, "_upload_verified_once", upload)
    monkeypatch.setattr(client, "find_file", lambda *a: _uploaded_metadata(payload))
    monkeypatch.setattr("google_drive_archive.time.sleep", lambda _: None)
    assert client.upload_verified("name", payload, "parent", source="source")["id"] == "uploaded-file"
    assert calls == ["upload"]


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_permanent_upload_errors_are_not_retried(status, monkeypatch):
    import requests
    client = DriveArchiveClient("id", "secret", "refresh")
    def upload(*args, **kwargs):
        raise _http_error(status)
    monkeypatch.setattr(client, "_upload_verified_once", upload)
    monkeypatch.setattr(client, "find_file", lambda *a: pytest.fail("lookup for permanent error"))
    monkeypatch.setattr("google_drive_archive.time.sleep", lambda _: pytest.fail("permanent retry"))
    with pytest.raises(requests.HTTPError):
        client.upload_verified("name", b"archive", "parent", source="source")


def test_upload_retry_exhaustion_is_bounded_and_reconciles_final_attempt(monkeypatch):
    import requests
    client = DriveArchiveClient("id", "secret", "refresh")
    events = []
    def upload(*args, **kwargs):
        events.append("upload")
        raise _http_error(502)
    monkeypatch.setattr(client, "_upload_verified_once", upload)
    monkeypatch.setattr(client, "find_file", lambda *a: events.append("lookup"))
    monkeypatch.setattr("google_drive_archive.time.sleep", lambda delay: events.append(delay))
    with pytest.raises(requests.HTTPError):
        client.upload_verified("name", b"archive", "parent", source="source")
    assert events == ["upload", 1, "lookup", "upload", 2, "lookup", "upload", 4, "lookup"]


@pytest.mark.parametrize("conflict", [True, False])
def test_reconciliation_conflict_or_failure_stops_upload(monkeypatch, conflict):
    import requests
    client = DriveArchiveClient("id", "secret", "refresh")
    calls = []
    def upload(*args, **kwargs):
        calls.append("upload")
        raise _http_error(502)
    def lookup(*args):
        if conflict:
            return _uploaded_metadata(b"different")
        raise _http_error(503)
    monkeypatch.setattr(client, "_upload_verified_once", upload)
    monkeypatch.setattr(client, "find_file", lookup)
    monkeypatch.setattr("google_drive_archive.time.sleep", lambda _: None)
    with pytest.raises(ArchiveConflictError if conflict else requests.HTTPError):
        client.upload_verified("name", b"archive", "parent", source="source")
    assert calls == ["upload"]


@pytest.mark.parametrize("error_type", ["Timeout", "ConnectionError"])
def test_transport_failures_reconcile_without_duplicate(error_type, monkeypatch):
    import requests
    client = DriveArchiveClient("id", "secret", "refresh")
    payload = b"archive"
    calls = []
    def upload(*args, **kwargs):
        calls.append("upload")
        raise getattr(requests, error_type)("test failure")
    monkeypatch.setattr(client, "_upload_verified_once", upload)
    monkeypatch.setattr(client, "find_file", lambda *a: _uploaded_metadata(payload))
    monkeypatch.setattr("google_drive_archive.time.sleep", lambda _: None)
    assert client.upload_verified("name", payload, "parent", source="source")["id"] == "uploaded-file"
    assert calls == ["upload"]


def test_successful_upload_preserves_response_checksum_validation(monkeypatch):
    from google_drive_archive import ArchiveError
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return {"id": "wrong", "size": "1", "md5Checksum": "wrong"}
    class Session:
        def post(self, *args, **kwargs):
            return Response()
    client = DriveArchiveClient("id", "secret", "refresh", session=Session())
    client.access_token = "test-token"
    monkeypatch.setattr(client, "find_file", lambda *a: pytest.fail("checksum failure retried"))
    monkeypatch.setattr("google_drive_archive.time.sleep", lambda _: pytest.fail("checksum failure retried"))
    with pytest.raises(ArchiveError, match="size verification failed"):
        client.upload_verified("name", b"archive", "parent", source="source")
