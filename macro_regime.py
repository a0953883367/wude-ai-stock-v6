"""Collect macro/FX risk data before it is allowed to affect AI scores."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


MINIMUM_TRADING_DAYS = 16
MACRO_KEYS = ("美元台幣", "美元指數", "VIX", "美國10年期公債殖利率")


def _number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def evaluate_macro_regime(market: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Return a bounded 0-100 risk score from transparent market rules."""
    score = 50.0
    reasons: list[str] = []
    valid = 0

    def item(name: str) -> tuple[float | None, float | None]:
        nonlocal valid
        row = market.get(name, {})
        price = _number(row.get("price"))
        change = _number(row.get("change_pct"))
        if price is not None:
            valid += 1
        return price, change

    twd, twd_change = item("美元台幣")
    dxy, dxy_change = item("美元指數")
    vix, vix_change = item("VIX")
    us10y, us10y_change = item("美國10年期公債殖利率")

    if vix is not None:
        if vix >= 30:
            score -= 20
            reasons.append("VIX高於30，市場避險升溫")
        elif vix >= 25:
            score -= 14
            reasons.append("VIX高於25，波動風險偏高")
        elif vix >= 20:
            score -= 7
            reasons.append("VIX高於20，市場波動增加")
        elif vix <= 15:
            score += 6
            reasons.append("VIX低於15，風險情緒較穩定")
        if vix_change is not None and vix_change >= 10:
            score -= 6
            reasons.append("VIX單日明顯上升")
        elif vix_change is not None and vix_change <= -10:
            score += 4
            reasons.append("VIX單日明顯回落")

    if us10y is not None:
        if us10y >= 5:
            score -= 6
            reasons.append("美國10年債殖利率高於5%")
        elif us10y >= 4.5:
            score -= 3
            reasons.append("美國10年債殖利率偏高")
        if us10y_change is not None and us10y_change >= 2:
            score -= 5
            reasons.append("美國10年債殖利率快速上升")
        elif us10y_change is not None and us10y_change <= -2:
            score += 3
            reasons.append("美國10年債殖利率回落")

    if dxy_change is not None:
        if dxy_change >= 0.5:
            score -= 4
            reasons.append("美元指數走強，風險資產承壓")
        elif dxy_change <= -0.5:
            score += 3
            reasons.append("美元指數走弱，資金環境較寬鬆")

    if twd_change is not None:
        if twd_change >= 0.5:
            score -= 3
            reasons.append("美元台幣上升，台幣轉弱")
        elif twd_change <= -0.5:
            score += 2
            reasons.append("美元台幣下降，台幣轉強")

    score = min(100.0, max(0.0, score))
    regime = "風險偏多" if score >= 60 else "風險偏空" if score < 40 else "中性"
    return {
        "score": round(score, 1),
        "regime": regime,
        "reasons": reasons or ["總體指標暫無明顯偏向"],
        "valid_indicator_count": valid,
        "data_available": valid >= 3,
        "indicators": {key: market.get(key, {}) for key in MACRO_KEYS},
    }


def update_macro_regime(
    reports_dir: Path,
    market: dict[str, dict[str, Any]],
    updated_at: str,
    period: str,
) -> dict[str, Any]:
    """Persist one latest snapshot per date and activate only after 16 valid days."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    history_path = reports_dir / "macro_history.json"
    try:
        payload = json.loads(history_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        payload = {"version": 1, "snapshots": []}

    evaluated = evaluate_macro_regime(market)
    trade_date = updated_at[:10]
    snapshot = {
        "date": trade_date,
        "updated_at": updated_at,
        "period": period,
        "score": evaluated["score"],
        "regime": evaluated["regime"],
        "data_available": evaluated["data_available"],
        "indicators": evaluated["indicators"],
    }
    snapshots = [
        row for row in payload.get("snapshots", [])
        if str(row.get("date")) != trade_date
    ]
    snapshots.append(snapshot)
    snapshots = sorted(snapshots, key=lambda row: str(row.get("date", "")))[-180:]
    payload["snapshots"] = snapshots
    history_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    collected = len({
        str(row.get("date"))
        for row in snapshots
        if row.get("data_available")
    })
    current_available = bool(evaluated["data_available"])
    active = collected >= MINIMUM_TRADING_DAYS and current_available
    evaluated["calibration"] = {
        "trading_days_collected": collected,
        "minimum_trading_days": MINIMUM_TRADING_DAYS,
        "remaining_trading_days": max(MINIMUM_TRADING_DAYS - collected, 0),
        "status": "active" if active else "collecting_only",
        "affects_ai_score": active,
    }
    return evaluated
