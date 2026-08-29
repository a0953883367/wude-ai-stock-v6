"""Single, honest 60-trading-day forward-validation status report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


TARGET_DAYS = 60


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def _write(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def update_validation_60d(reports_dir: Path, *, updated_at: str) -> dict[str, Any]:
    reports_dir = Path(reports_dir)
    accuracy = _read(reports_dir / "accuracy.json")
    million = _read(reports_dir / "million_simulation.json")
    holding = _read(reports_dir / "holding_simulation.json")
    valuation = _read(reports_dir / "valuation_risk_shadow.json")
    rotation = _read(reports_dir / "market_rotation_shadow.json")
    calibration = accuracy.get("calibration") or {}
    days = min(TARGET_DAYS, int(calibration.get("trading_days_collected") or 0))
    eligible_samples = int(calibration.get("eligible_one_day_samples") or 0)

    market_days = {
        market: int((million.get("markets", {}).get(market) or {}).get("completed_days") or 0)
        for market in ("TW", "US")
    }
    rotation_days = {
        market: len((rotation.get("markets", {}).get(market) or {}).get("snapshots", []))
        for market in ("TW", "US")
    }
    valuation_sessions = {
        market: int((valuation.get("validation", {}).get(market) or {}).get("effective_sessions") or 0)
        for market in ("TW", "US")
    }
    ready = bool(
        days >= TARGET_DAYS
        and eligible_samples >= int(calibration.get("minimum_consensus_samples") or 200)
        and calibration.get("ready_for_model_selection")
    )
    payload = {
        "schema_version": 1,
        "updated_at": updated_at,
        "mode": "forward_validation_only",
        "status": "complete" if ready else "collecting",
        "trading_days_collected": days,
        "target_trading_days": TARGET_DAYS,
        "remaining_trading_days": max(TARGET_DAYS - days, 0),
        "progress_pct": round(days / TARGET_DAYS * 100, 1),
        "eligible_samples": eligible_samples,
        "minimum_consensus_samples": int(calibration.get("minimum_consensus_samples") or 200),
        "ready_for_model_selection": ready,
        "tracks": {
            "short_1_5d": {"status": "complete" if days >= TARGET_DAYS else "collecting", "days": days},
            "five_day_portfolios": {market: {"completed_days": market_days[market]} for market in market_days},
            "medium_45d": {
                market: {"status": (holding.get("medium", {}).get(market) or {}).get("status", "missing")}
                for market in ("TW", "US")
            },
            "long_6m": {"status": (holding.get("long") or {}).get("status", "missing")},
            "valuation": {market: {"effective_sessions": valuation_sessions[market]} for market in valuation_sessions},
            "rotation": {market: {"completed_sessions": rotation_days[market]} for market in rotation_days},
        },
        "rules": {
            "future_data_forbidden": True,
            "missing_sessions_not_counted": True,
            "historical_proxy_separate": True,
            "automatic_weight_changes": False,
            "automatic_orders": False,
            "note": "60日機制已完整啟用；實際結果只會隨完成交易日自然累積，不用歷史代理冒充。",
        },
    }
    _write(reports_dir / "validation_60d.json", payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reports-dir", default="reports")
    parser.add_argument("--updated-at", default="")
    args = parser.parse_args()
    payload = update_validation_60d(Path(args.reports_dir), updated_at=args.updated_at)
    print(f"60-day validation: {payload['trading_days_collected']}/{TARGET_DAYS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
