"""Forward-only Taiwan signal confirmation research ledger.

This module keeps three requested diagnostics isolated from formal V6:
intraday signals stay provisional until the close, daily/weekly stochastic
direction is compared, and a neutral RSI is interpreted together with ADX,
volatility and the 20-day moving-average slope.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


VERSION = 1
MAX_SESSIONS = 126
MAX_SIGNALS = 5000
POLICY = {
    "shadow_only": True,
    "formal_v6_unchanged": True,
    "formal_rankings_unchanged": True,
    "formal_weights_unchanged": True,
    "historical_data_never_deleted": True,
    "automatic_orders": False,
    "future_data_forbidden": True,
}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _series(frame: pd.DataFrame, name: str) -> pd.Series:
    if frame is None or frame.empty or name not in frame:
        return pd.Series(dtype=float)
    return pd.to_numeric(frame[name], errors="coerce").dropna()


def _stochastic(frame: pd.DataFrame, window: int = 9) -> tuple[float | None, float | None]:
    close, high, low = (_series(frame, key) for key in ("close", "high", "low"))
    if min(len(close), len(high), len(low)) < window:
        return None, None
    aligned = pd.concat({"close": close, "high": high, "low": low}, axis=1).dropna()
    if len(aligned) < window:
        return None, None
    lowest = aligned["low"].rolling(window).min()
    highest = aligned["high"].rolling(window).max()
    rsv = (aligned["close"] - lowest) / (highest - lowest).replace(0, np.nan) * 100
    k = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    d = k.ewm(alpha=1 / 3, adjust=False).mean()
    if pd.isna(k.iloc[-1]) or pd.isna(d.iloc[-1]):
        return None, None
    return round(float(k.iloc[-1]), 2), round(float(d.iloc[-1]), 2)


def _weekly_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty or not isinstance(frame.index, pd.DatetimeIndex):
        return pd.DataFrame()
    source = frame.copy()
    source.columns = [str(column).lower() for column in source.columns]
    required = {"high", "low", "close"}
    if not required.issubset(source.columns):
        return pd.DataFrame()
    weekly = source.resample("W-FRI").agg({"high": "max", "low": "min", "close": "last"})
    return weekly.dropna(subset=["high", "low", "close"])


def _adx(frame: pd.DataFrame, window: int = 14) -> float | None:
    high, low, close = (_series(frame, key) for key in ("high", "low", "close"))
    aligned = pd.concat({"high": high, "low": low, "close": close}, axis=1).dropna()
    if len(aligned) < window * 2:
        return None
    up = aligned["high"].diff()
    down = -aligned["low"].diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    true_range = pd.concat(
        [
            aligned["high"] - aligned["low"],
            (aligned["high"] - aligned["close"].shift()).abs(),
            (aligned["low"] - aligned["close"].shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = true_range.rolling(window).mean().replace(0, np.nan)
    plus_di = 100 * plus_dm.rolling(window).mean() / atr
    minus_di = 100 * minus_dm.rolling(window).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    value = dx.rolling(window).mean().iloc[-1]
    return None if pd.isna(value) else round(float(value), 2)


def _direction(k: float | None, d: float | None) -> str:
    if k is None or d is None:
        return "UNKNOWN"
    if k >= d + 1:
        return "UP"
    if k <= d - 1:
        return "DOWN"
    return "FLAT"


def _technical_diagnostics(row: dict[str, Any], frame: pd.DataFrame) -> dict[str, Any]:
    source = frame.copy() if frame is not None else pd.DataFrame()
    if not source.empty:
        source.columns = [str(column).lower() for column in source.columns]
    daily_k, daily_d = _stochastic(source)
    weekly_k, weekly_d = _stochastic(_weekly_frame(source))
    daily_direction, weekly_direction = _direction(daily_k, daily_d), _direction(weekly_k, weekly_d)
    if "UNKNOWN" in {daily_direction, weekly_direction}:
        resonance = "INSUFFICIENT"
        resonance_label = "資料不足"
    elif daily_direction == weekly_direction and daily_direction in {"UP", "DOWN"}:
        resonance = f"ALIGNED_{daily_direction}"
        resonance_label = "日週KD同向向上" if daily_direction == "UP" else "日週KD同向向下"
    else:
        resonance = "DIVERGENT"
        resonance_label = "日週KD背離／整理"

    close = _series(source, "close")
    returns = close.pct_change().dropna().tail(20)
    volatility = float(returns.std(ddof=0) * math.sqrt(252) * 100) if len(returns) >= 10 else None
    adx = _adx(source)
    rsi = _number(row.get("rsi"), 50.0)
    slope = _number(row.get("ma20_slope5_pct"))
    neutral_rsi = 40 <= rsi <= 60
    weak_trend = adx is not None and adx < 25
    flat_slope = abs(slope) <= 2.0
    moderate_volatility = volatility is not None and volatility <= 45
    sideways_votes = sum((neutral_rsi, weak_trend, flat_slope, moderate_volatility))
    if sideways_votes >= 3 and neutral_rsi:
        regime, regime_label = "SIDEWAYS", "RSI中性且趨勢不足，偏盤整"
    elif adx is not None and adx >= 25 and slope > 0:
        regime, regime_label = "TREND_UP", "RSI需配合上升趨勢解讀"
    elif adx is not None and adx >= 25 and slope < 0:
        regime, regime_label = "TREND_DOWN", "RSI需配合下降趨勢解讀"
    else:
        regime, regime_label = "MIXED", "訊號混合，等待更多資料"
    return {
        "daily_k": daily_k,
        "daily_d": daily_d,
        "weekly_k": weekly_k,
        "weekly_d": weekly_d,
        "kd_resonance": resonance,
        "kd_resonance_label": resonance_label,
        "rsi": round(rsi, 2),
        "adx14": adx,
        "annualized_volatility20_pct": None if volatility is None else round(volatility, 2),
        "ma20_slope5_pct": round(slope, 2),
        "rsi_regime": regime,
        "rsi_regime_label": regime_label,
        "sideways_votes": sideways_votes,
    }


def _provisional_direction(row: dict[str, Any]) -> str:
    change = _number(row.get("change_pct"))
    attack = _number(row.get("attack_volume"))
    above_vwap = bool(row.get("tw_above_vwap"))
    breakout = bool(row.get("tw_breakout_opening_15m") or row.get("tw_breakout_opening_30m"))
    if change >= 0.5 and above_vwap and (breakout or attack >= 10):
        return "UP"
    if change <= -0.5 and not above_vwap and attack <= -10:
        return "DOWN"
    return "NEUTRAL"


def _close_direction(row: dict[str, Any]) -> str:
    open_price = _number(row.get("official_open_price"))
    close_price = _number(row.get("official_close_price"))
    if open_price <= 0 or close_price <= 0:
        return "UNKNOWN"
    change = (close_price / open_price - 1) * 100
    if change >= 0.3:
        return "UP"
    if change <= -0.3:
        return "DOWN"
    return "NEUTRAL"


def _load(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        payload = {}
    if payload.get("version") != VERSION:
        payload = {}
    return payload if isinstance(payload, dict) else {}


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def update_tw_signal_confirmation_shadow(
    reports_dir: Path,
    rows: list[dict[str, Any]],
    histories: dict[str, pd.DataFrame],
    *,
    period: str,
    updated_at: str,
    intraday: bool = False,
) -> dict[str, Any]:
    """Update provisional/close outcomes without changing input rows."""
    path = Path(reports_dir) / "tw_signal_confirmation_shadow.json"
    previous = _load(path)
    signals = [item for item in previous.get("signals", []) if isinstance(item, dict)]
    by_key = {(str(item.get("session_date")), str(item.get("symbol"))): item for item in signals}
    diagnostics: list[dict[str, Any]] = []

    for source_row in rows:
        if str(source_row.get("market") or "").upper() != "TW":
            continue
        symbol = str(source_row.get("symbol") or "")
        frame = histories.get(symbol)
        if frame is None or frame.empty:
            continue
        diagnostics.append({"symbol": symbol, **_technical_diagnostics(source_row, frame)})
        provisional_date = str(source_row.get("tw_intraday_session_date") or "")
        official_date = str(source_row.get("official_session_date") or "")
        if (period == "noon" or intraday) and source_row.get("tw_intraday_is_current_session") and provisional_date:
            direction = _provisional_direction(source_row)
            if direction == "NEUTRAL":
                continue
            by_key[(provisional_date, symbol)] = {
                "session_date": provisional_date,
                "symbol": symbol,
                "status": "provisional",
                "intraday_direction": direction,
                "intraday_change_pct": _number(source_row.get("change_pct")),
                "created_at": updated_at,
                "settled_at": None,
                "close_direction": None,
                "confirmed": None,
            }
        elif period == "evening" and official_date:
            item = by_key.get((official_date, symbol))
            if item and item.get("status") == "provisional":
                close_direction = _close_direction(source_row)
                item.update({
                    "status": "confirmed" if item.get("intraday_direction") == close_direction else "rejected",
                    "close_direction": close_direction,
                    "confirmed": item.get("intraday_direction") == close_direction,
                    "settled_at": updated_at,
                })

    signals = sorted(by_key.values(), key=lambda item: (str(item.get("session_date")), str(item.get("symbol"))))
    session_dates = sorted({str(item.get("session_date")) for item in signals if item.get("session_date")})
    keep_dates = set(session_dates[-MAX_SESSIONS:])
    signals = [item for item in signals if item.get("session_date") in keep_dates][-MAX_SIGNALS:]
    settled = [item for item in signals if item.get("confirmed") is not None]
    payload = {
        "version": VERSION,
        "updated_at": updated_at,
        "mode": "tw_signal_confirmation_shadow_only",
        "status": "ok",
        "policy": dict(POLICY),
        "rules": {
            "intraday_to_close": "盤中訊號只標暫定；同一交易日收盤後才確認或否決",
            "kd_resonance": "日KD與週KD分開計算，只記錄同向或背離，不改正式分數",
            "rsi_regime": "RSI中性區須同時參考ADX、20日波動率與MA20斜率",
        },
        "summary": {
            "tracked_signals": len(signals),
            "provisional": sum(item.get("status") == "provisional" for item in signals),
            "confirmed": sum(item.get("status") == "confirmed" for item in signals),
            "rejected": sum(item.get("status") == "rejected" for item in signals),
            "settled": len(settled),
            "confirmation_rate_pct": None if not settled else round(sum(bool(item.get("confirmed")) for item in settled) / len(settled) * 100, 2),
            "diagnostic_rows": len(diagnostics),
            "kd_aligned": sum(str(item.get("kd_resonance", "")).startswith("ALIGNED") for item in diagnostics),
            "kd_divergent": sum(item.get("kd_resonance") == "DIVERGENT" for item in diagnostics),
            "rsi_sideways": sum(item.get("rsi_regime") == "SIDEWAYS" for item in diagnostics),
        },
        "diagnostics": diagnostics,
        "signals": signals,
    }
    _write(path, payload)
    return payload
