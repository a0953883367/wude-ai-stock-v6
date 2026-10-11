"""Canonical-only status artifact writer; no providers or private task inputs."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

STATUS_FILE = "public_plan_status.json"


def write_public_plan_status(reports_dir: Path, artifact: dict) -> Path:
    """Write a fresh sanitized projection or fail without a stale local output.

    The public universe is rebuilt from this trusted checkout's watchlist. No
    supplemental task manifest, quote cache, or prospective ledger is read.
    Failure text is fixed; upstream exceptions can contain market values.
    """
    target = reports_dir / STATUS_FILE
    temporary = reports_dir / "public_plan_status.tmp"
    try:
        from watchlist_manifest import build_manifest
        from public_research_briefing_contract import build_canonical_public_research_briefing

        status = build_canonical_public_research_briefing(
            build_manifest(), artifact, as_of=datetime.now(timezone.utc).isoformat())
        payload = json.dumps(status, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False) + "\n"
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(target)
    except Exception:
        # Do not let the publisher reuse an earlier batch after failed generation.
        # Cleanup is best effort; the fixed exception always stops publication.
        for path in (temporary, target):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        raise RuntimeError("public_plan_status_generation_failed") from None
    return target
