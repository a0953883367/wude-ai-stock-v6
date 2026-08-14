"""Persist predictions and measure their later returns without look-ahead data."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


HORIZONS = (1, 5, 10, 20)


def _business_days_between(start: date, end: date) -> int:
    """Count weekdays after start through end (exchange holidays are handled on next run)."""
    days = 0
    current = start + timedelta(days=1)
    while current <= end:
        if current.weekday() < 5:
            days += 1
        current += timedelta(days=1)
    return days


def _metric(returns: list[float], positive_is_hit: bool = True) -> dict[str, float | int]:
    if not returns:
        return {"samples": 0, "win_rate_pct": 0.0, "avg_return_pct": 0.0, "worst_return_pct": 0.0}
    return {
        "samples": len(returns),
        "win_rate_pct": round(
            sum((value > 0) if positive_is_hit else (value <= 0) for value in returns)
            / len(returns) * 100,
            1,
        ),
        "avg_return_pct": round(sum(returns) / len(returns), 2),
        "worst_return_pct": round(min(returns), 2),
    }


def _summary(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    horizons: dict[str, Any] = {}
    signals: dict[str, Any] = {"🟢": {}, "🟡": {}, "🔴": {}}
    for horizon in HORIZONS:
        key = str(horizon)
        all_returns: list[float] = []
        by_signal: dict[str, list[float]] = {signal: [] for signal in signals}
        for snapshot in snapshots:
            for row in snapshot.get("predictions", []):
                value = row.get("outcomes", {}).get(key)
                if value is None:
                    continue
                result = float(value)
                all_returns.append(result)
                signal = str(row.get("signal", "🟡"))[:1]
                if signal in by_signal:
                    by_signal[signal].append(result)
        horizons[key] = _metric(all_returns)
        for signal in signals:
            signals[signal][key] = _metric(by_signal[signal], positive_is_hit=signal != "🔴")
    trading_dates = sorted({
        str(snapshot.get("date"))
        for snapshot in snapshots
        if snapshot.get("date")
        and date.fromisoformat(str(snapshot.get("date"))).weekday() < 5
        and snapshot.get("predictions")
    })
    collected = len(trading_dates)
    one_day_samples = int(horizons["1"]["samples"])
    active = one_day_samples > 0
    return {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "snapshot_count": len(snapshots),
        "horizons": horizons,
        "signals": signals,
        "calibration": {
            "trading_days_collected": collected,
            "minimum_trading_days": 0,
            "remaining_trading_days": 0,
            "status": "active" if active else "waiting_for_first_outcome",
            "affects_ai_score": active,
            "eligible_one_day_samples": one_day_samples,
        },
        "note": "只使用系統實際留存後產生的報酬，不偽造過去AI判斷；少量樣本以統計收縮限制影響。",
    }


def update_performance(
    reports_dir: Path,
    predictions: list[dict[str, Any]],
    current_rows: list[dict[str, Any]],
    updated_at: str,
    period: str,
) -> dict[str, Any]:
    """Evaluate old predictions, append the current snapshot, and save summary files."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    history_path = reports_dir / "prediction_history.json"
    try:
        history = json.loads(history_path.read_text(encoding="utf-8"))
        snapshots = list(history.get("snapshots", []))
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        snapshots = []

    current_date = date.fromisoformat(updated_at[:10])
    prices = {
        str(row.get("symbol")): float(row["price"])
        for row in current_rows
        if row.get("symbol") and row.get("price") not in (None, 0)
    }
    for snapshot in snapshots:
        try:
            snapshot_date = date.fromisoformat(str(snapshot["date"]))
        except (KeyError, ValueError):
            continue
        elapsed = _business_days_between(snapshot_date, current_date)
        for row in snapshot.get("predictions", []):
            symbol = str(row.get("symbol", ""))
            base_price = float(row.get("price") or 0)
            if not base_price or symbol not in prices:
                continue
            outcomes = row.setdefault("outcomes", {})
            for horizon in HORIZONS:
                key = str(horizon)
                if elapsed >= horizon and key not in outcomes:
                    outcomes[key] = round((prices[symbol] / base_price - 1) * 100, 2)

    cutoff = current_date - timedelta(days=180)
    snapshots = [
        item for item in snapshots
        if str(item.get("date", "")) >= cutoff.isoformat()
    ]
    snapshot_id = f"{updated_at}-{period}"
    if not any(item.get("id") == snapshot_id for item in snapshots):
        snapshots.append({
            "id": snapshot_id,
            "date": current_date.isoformat(),
            "period": period,
            "predictions": [
                {
                    "symbol": row.get("symbol"),
                    "name": row.get("name"),
                    "market": row.get("market"),
                    "group": row.get("backtest_group"),
                    "rank": row.get("backtest_rank"),
                    "score": row.get("score"),
                    "signal": str(row.get("action", "🟡"))[:1],
                    "price": row.get("price"),
                    "outcomes": {},
                }
                for row in predictions
            ],
        })

    summary = _summary(snapshots)
    history_path.write_text(
        json.dumps({"version": 1, "snapshots": snapshots}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (reports_dir / "performance.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def load_performance_context(reports_dir: Path) -> dict[str, Any]:
    """Load only verified outcomes saved by earlier briefing runs."""
    try:
        return json.loads(
            (reports_dir / "performance.json").read_text(encoding="utf-8")
        )
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        return {}
