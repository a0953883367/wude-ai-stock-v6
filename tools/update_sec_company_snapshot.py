#!/usr/bin/env python3
"""Build a small, integrity-checked SEC ticker snapshot for the active US universe."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from corporate_actions_shadow import (  # noqa: E402
    SOURCE_URLS,
    _combined_active_payload,
    _request_headers,
    _tracked_stocks,
)
from watchlist import load_watchlist  # noqa: E402


UNIVERSE = ROOT / "search_data.json"
OUTPUT = ROOT / "official_data" / "sec_company_tickers_snapshot.json"


def main() -> int:
    universe = json.loads(UNIVERSE.read_text(encoding="utf-8"))
    universe = _combined_active_payload(universe, load_watchlist())
    wanted = {
        symbol for symbol, row in _tracked_stocks(universe).items()
        if row.get("market") == "US"
    }
    response = requests.get(
        SOURCE_URLS["sec_registry"],
        headers=_request_headers(SOURCE_URLS["sec_registry"], accept="application/json"),
        timeout=60,
    )
    response.raise_for_status()
    source_bytes = response.content
    payload = response.json()
    fields = payload.get("fields") or ["cik", "name", "ticker", "exchange"]
    ticker_index = fields.index("ticker")
    rows = [
        row for row in (payload.get("data") or [])
        if isinstance(row, list)
        and len(row) > ticker_index
        and str(row[ticker_index] or "").strip().upper() in wanted
    ]
    canonical = json.dumps(
        rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    snapshot = {
        "schema": "wude.sec_company_tickers_snapshot.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_url": SOURCE_URLS["sec_registry"],
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "records_sha256": hashlib.sha256(canonical).hexdigest(),
        "tracked_us_count": len(wanted),
        "matched_count": len(rows),
        "fields": fields,
        "data": rows,
        "policy": {
            "identity_seed_only": True,
            "never_changes_formal_universe": True,
            "never_deletes_history": True,
        },
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"SEC snapshot: matched={len(rows)}/{len(wanted)} sha256={snapshot['records_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
