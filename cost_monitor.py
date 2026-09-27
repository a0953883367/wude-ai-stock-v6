"""Sanitized cost-governance contract for the public control tower.

Raw prices, invoices, account identifiers and payment details stay in the
ignored private input.  The public report exposes only status bands.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


PROVIDERS = {
    "alpaca": "Alpaca 行情",
    "railway": "Railway 即時服務",
    "market_data": "其他市場資料 API",
}
ALLOWED_BANDS = {"within_budget", "near_limit", "over_budget", "unknown"}


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def _write(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def update_cost_monitor(reports_dir: Path, *, updated_at: str = "") -> dict[str, Any]:
    reports_dir = Path(reports_dir)
    private = _read(reports_dir / "private_cost_usage.json")
    source = private.get("providers") if isinstance(private.get("providers"), dict) else {}
    providers = []
    for provider_id, label in PROVIDERS.items():
        row = source.get(provider_id) if isinstance(source.get(provider_id), dict) else {}
        band = str(row.get("budget_status") or "unknown")
        if band not in ALLOWED_BANDS:
            band = "unknown"
        renewal_days = row.get("renewal_days")
        providers.append({
            "id": provider_id,
            "label": label,
            "budget_status": band,
            "renewal_days": int(renewal_days) if isinstance(renewal_days, (int, float)) else None,
            "usage_visibility": "private_band_only" if row else "not_connected",
        })
    known = [row for row in providers if row["budget_status"] != "unknown"]
    if any(row["budget_status"] == "over_budget" for row in providers):
        status = "critical"
    elif any(row["budget_status"] == "near_limit" for row in providers):
        status = "warning"
    elif len(known) == len(providers):
        status = "ok"
    else:
        status = "setup_required"
    payload = {
        "schema_version": 1,
        "updated_at": updated_at,
        "status": status,
        "providers": providers,
        "privacy": {
            "raw_amounts_public": False,
            "invoices_public": False,
            "payment_details_public": False,
            "account_identifiers_public": False,
            "private_input_file": "reports/private_cost_usage.json (git ignored)",
        },
    }
    _write(reports_dir / "cost_monitor.json", payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reports-dir", default="reports")
    parser.add_argument("--updated-at", default="")
    args = parser.parse_args()
    payload = update_cost_monitor(Path(args.reports_dir), updated_at=args.updated_at)
    print(f"cost monitor: {payload['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
