#!/usr/bin/env python3
"""Upload verified gzip copies of immutable report archives to Google Drive."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import gzip
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import sys
from typing import Any
from uuid import uuid4

import requests


TOKEN_URL = "https://oauth2.googleapis.com/token"
DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"
DRIVE_UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
ARCHIVE_DATE_RE = re.compile(r"^(?P<year>\d{4})-(?P<month>\d{2})-\d{2}-.+\.json(?:\.gz)?$")


class ArchiveError(RuntimeError):
    """Base error for archive operations."""


class ArchiveConflictError(ArchiveError):
    """Raised when a Drive filename exists with different content."""


@dataclass
class ArchiveResult:
    source: str
    destination: str
    status: str
    size: int
    sha256: str
    drive_file_id: str = ""
    error: str = ""


def gzip_bytes(data: bytes) -> bytes:
    """Return deterministic gzip bytes for stable duplicate detection."""
    output = BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as stream:
        stream.write(data)
    return output.getvalue()


def archive_payload(path: Path) -> tuple[str, bytes]:
    if path.name.endswith(".json.gz"):
        return path.name, path.read_bytes()
    if path.name.endswith(".json"):
        return path.name + ".gz", gzip_bytes(path.read_bytes())
    raise ArchiveError(f"Unsupported archive file: {path}")


def quote_drive_query(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


class DriveArchiveClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        *,
        session: requests.Session | None = None,
        timeout: int = 30,
    ) -> None:
        if not all((client_id, client_secret, refresh_token)):
            raise ArchiveError("Google Drive OAuth credentials are incomplete")
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.session = session or requests.Session()
        self.timeout = timeout
        self.access_token = ""

    def authenticate(self) -> None:
        response = self.session.post(
            TOKEN_URL,
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        self.access_token = str(response.json().get("access_token") or "")
        if not self.access_token:
            raise ArchiveError("Google OAuth did not return an access token")

    def _headers(self) -> dict[str, str]:
        if not self.access_token:
            raise ArchiveError("Google Drive client is not authenticated")
        return {"Authorization": f"Bearer {self.access_token}"}

    def find_file(
        self,
        name: str,
        parent_id: str,
        *,
        mime_type: str | None = None,
    ) -> dict[str, Any] | None:
        clauses = [
            f"name = '{quote_drive_query(name)}'",
            f"'{quote_drive_query(parent_id)}' in parents",
            "trashed = false",
        ]
        if mime_type:
            clauses.append(f"mimeType = '{quote_drive_query(mime_type)}'")
        response = self.session.get(
            DRIVE_FILES_URL,
            headers=self._headers(),
            params={
                "q": " and ".join(clauses),
                "spaces": "drive",
                "pageSize": 10,
                "fields": "files(id,name,size,md5Checksum,mimeType,appProperties,webViewLink)",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        files = response.json().get("files") or []
        if len(files) > 1:
            raise ArchiveConflictError(
                f"Multiple Drive files named {name!r} exist in one archive folder"
            )
        return files[0] if files else None

    def create_folder(self, name: str, parent_id: str = "root") -> dict[str, Any]:
        response = self.session.post(
            DRIVE_FILES_URL,
            headers={**self._headers(), "Content-Type": "application/json"},
            params={"fields": "id,name,mimeType,webViewLink"},
            json={
                "name": name,
                "mimeType": FOLDER_MIME_TYPE,
                "parents": [parent_id],
                "appProperties": {"wude_archive": "true"},
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def ensure_folder(self, name: str, parent_id: str = "root") -> dict[str, Any]:
        existing = self.find_file(name, parent_id, mime_type=FOLDER_MIME_TYPE)
        return existing or self.create_folder(name, parent_id)

    def ensure_archive_path(
        self, root_name: str, year: str, month: str
    ) -> dict[str, Any]:
        root = self.ensure_folder(root_name)
        year_folder = self.ensure_folder(year, str(root["id"]))
        return self.ensure_folder(month, str(year_folder["id"]))

    def upload_verified(
        self,
        name: str,
        payload: bytes,
        parent_id: str,
        *,
        source: str,
    ) -> dict[str, Any]:
        sha256 = hashlib.sha256(payload).hexdigest()
        md5 = hashlib.md5(payload, usedforsecurity=False).hexdigest()
        metadata = {
            "name": name,
            "parents": [parent_id],
            "mimeType": "application/gzip",
            "appProperties": {
                "source": source[-120:],
                "sha256": sha256,
                "size": str(len(payload)),
            },
        }
        boundary = "wude-" + uuid4().hex
        delimiter = ("--" + boundary + "\r\n").encode("ascii")
        body = b"".join(
            (
                delimiter,
                b"Content-Type: application/json; charset=UTF-8\r\n\r\n",
                json.dumps(metadata, ensure_ascii=False).encode("utf-8"),
                b"\r\n",
                delimiter,
                b"Content-Type: application/gzip\r\n\r\n",
                payload,
                b"\r\n",
                ("--" + boundary + "--\r\n").encode("ascii"),
            )
        )
        response = self.session.post(
            DRIVE_UPLOAD_URL,
            headers={
                **self._headers(),
                "Content-Type": f"multipart/related; boundary={boundary}",
            },
            params={
                "uploadType": "multipart",
                "fields": "id,name,size,md5Checksum,appProperties,webViewLink",
            },
            data=body,
            timeout=max(self.timeout, 120),
        )
        response.raise_for_status()
        uploaded = response.json()
        if int(uploaded.get("size") or -1) != len(payload):
            raise ArchiveError(f"Drive size verification failed for {name}")
        if str(uploaded.get("md5Checksum") or "").lower() != md5:
            raise ArchiveError(f"Drive checksum verification failed for {name}")
        if (uploaded.get("appProperties") or {}).get("sha256") != sha256:
            raise ArchiveError(f"Drive SHA-256 metadata verification failed for {name}")
        return uploaded

    def archive_file(self, path: Path, root_name: str) -> ArchiveResult:
        match = ARCHIVE_DATE_RE.match(path.name)
        if not match:
            raise ArchiveError(f"Archive filename has no YYYY-MM-DD prefix: {path.name}")
        target_name, payload = archive_payload(path)
        sha256 = hashlib.sha256(payload).hexdigest()
        md5 = hashlib.md5(payload, usedforsecurity=False).hexdigest()
        folder = self.ensure_archive_path(
            root_name, match.group("year"), match.group("month")
        )
        destination = f"{root_name}/{match.group('year')}/{match.group('month')}/{target_name}"
        existing = self.find_file(target_name, str(folder["id"]))
        if existing:
            same_size = int(existing.get("size") or -1) == len(payload)
            same_md5 = str(existing.get("md5Checksum") or "").lower() == md5
            same_sha256 = (
                (existing.get("appProperties") or {}).get("sha256") == sha256
            )
            if not (same_size and same_md5 and same_sha256):
                raise ArchiveConflictError(
                    f"Drive already contains different content at {destination}"
                )
            return ArchiveResult(
                source=str(path),
                destination=destination,
                status="verified_existing",
                size=len(payload),
                sha256=sha256,
                drive_file_id=str(existing["id"]),
            )
        uploaded = self.upload_verified(
            target_name,
            payload,
            str(folder["id"]),
            source=str(path),
        )
        return ArchiveResult(
            source=str(path),
            destination=destination,
            status="uploaded_verified",
            size=len(payload),
            sha256=sha256,
            drive_file_id=str(uploaded["id"]),
        )


def write_result(path: Path, results: list[ArchiveResult], errors: list[str]) -> None:
    payload = {
        "schema": "wude.google_drive_archive_result.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "error" if errors else "ok",
        "counts": {
            "total": len(results),
            "uploaded": sum(item.status == "uploaded_verified" for item in results),
            "verified_existing": sum(
                item.status == "verified_existing" for item in results
            ),
            "errors": len(errors),
        },
        "results": [asdict(item) for item in results],
        "errors": errors,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, default=Path("reports/archive"))
    parser.add_argument("--root-folder", default="武得AI歷史庫")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--result-file", type=Path, default=Path("google_drive_archive_result.json")
    )
    args = parser.parse_args()
    results: list[ArchiveResult] = []
    errors: list[str] = []
    try:
        candidates = sorted(
            (
                *args.source_dir.glob("*.json"),
                *args.source_dir.glob("*.json.gz"),
            ),
            key=lambda item: item.name,
            reverse=True,
        )
        if args.limit > 0:
            candidates = candidates[: args.limit]
        if not candidates:
            raise ArchiveError(f"No archive files found in {args.source_dir}")
        client = DriveArchiveClient(
            os.environ.get("GOOGLE_DRIVE_CLIENT_ID", ""),
            os.environ.get("GOOGLE_DRIVE_CLIENT_SECRET", ""),
            os.environ.get("GOOGLE_DRIVE_REFRESH_TOKEN", ""),
        )
        client.authenticate()
        for candidate in candidates:
            try:
                item = client.archive_file(candidate, args.root_folder)
                results.append(item)
                print(f"{item.status}: {item.destination} ({item.size} bytes)")
            except Exception as exc:
                message = f"{candidate}: {exc}"
                errors.append(message)
                print(f"ERROR: {message}", file=sys.stderr)
    except Exception as exc:
        errors.append(str(exc))
        print(f"ERROR: {exc}", file=sys.stderr)
    write_result(args.result_file, results, errors)
    print(
        f"Google Drive archive: files={len(results)} errors={len(errors)} "
        f"result={args.result_file}"
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
