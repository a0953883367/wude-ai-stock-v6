"""Transparent scoring model for the Wude Taiwan-stock candidate pool."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from config import TAIPEI


def market_session_fraction(now: datetime | None = None) -> float:
    """Elapsed fraction of Taiwan's regular 09:00-13:30 session."""
    now = (now or datetime.now(TAIPEI)).astimezone(TAIPEI)
    minute = now.hour * 60 + now.minute
    return min(1.0, max(15 / 270, (minute - 9 * 60) / 270))


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _rsi(close: pd.Series, window: int = 14) -> float:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(window).mean()
    loss = (-delta.clip(upper=0)).rolling(window).mean()
    rs = gain / loss.replace(0, np.nan)
    value = 100 - (100 / (1 + rs))
    return _finite(value.iloc[-1], 50.0)


def _intraday_metrics(frame: pd.DataFrame | None, avg_volume: float) -> tuple[float, float, float | None]:
    if frame is None or frame.empty:
        return 1.0, 0.0, None
    volume = frame.get("volume", pd.Series(dtype=float)).fillna(0)
    open_ = frame.get("open", pd.Series(dtype=float))
    close = frame.get("close", pd.Series(dtype=float))
    total = float(volume.sum())
    expected = max(avg_volume * market_session_fraction(), 1.0)
    pace = total / expected
    up = float(volume[close > open_].sum()) if len(close) else 0.0
    down = float(volume[close < open_].sum()) if len(close) else 0.0
    attack = (up - down) / max(up + down, 1.0) * 100
    live_price = _finite(close.dropna().iloc[-1], 0.0) if not close.dropna().empty else None
    return pace, attack, live_price


def build_features(
    item: dict[str, Any],
    daily: pd.DataFrame,
    intraday: pd.DataFrame | None,
    institution: dict[str, float] | None,
) -> dict[str, Any] | None:
    if daily.empty or "close" not in daily or len(daily["close"].dropna()) < 20:
        return None
    daily = daily.dropna(subset=["close"]).copy()
    close = daily["close"].astype(float)
    high = daily.get("high", close).astype(float)
    low = daily.get("low", close).astype(float)
    volume = daily.get("volume", pd.Series(0, index=daily.index)).astype(float)
    prev = _finite(close.iloc[-2], _finite(close.iloc[-1])) if len(close) > 1 else _finite(close.iloc[-1])
    # Exclude the still-forming daily bar only when intraday data belongs to
    # the same session. On weekends/holidays, keep the latest completed day.
    completed_volume = volume
    if intraday is not None and not intraday.empty and len(volume) > 1:
        try:
            if pd.Timestamp(daily.index[-1]).date() == pd.Timestamp(intraday.index[-1]).date():
                completed_volume = volume.iloc[:-1]
        except (TypeError, ValueError):
            pass
    avg5 = _finite(completed_volume.tail(5).mean(), 1.0)
    avg10 = _finite(completed_volume.tail(10).mean(), avg5)
    avg20 = _finite(completed_volume.tail(20).mean(), avg10)
    pace, attack, live = _intraday_metrics(intraday, avg20)
    price = live or _finite(close.iloc[-1])
    change = (price / prev - 1) * 100 if prev else 0.0
    ma5 = _finite(close.tail(5).mean(), price)
    ma10 = _finite(close.tail(10).mean(), ma5)
    ma20 = _finite(close.tail(20).mean(), price)
    ma60 = _finite(close.tail(60).mean(), ma20)
    rsi = _rsi(close)
    support1 = _finite(low.tail(5).min(), price)
    support2 = _finite(low.tail(20).min(), support1)
    resistance1 = _finite(high.tail(5).max(), price)
    resistance2 = _finite(high.tail(20).max(), resistance1)
    inst = institution or {"foreign": 0.0, "trust": 0.0, "dealer": 0.0}
    inst_net = sum(_finite(inst.get(k)) for k in ("foreign", "trust", "dealer"))
    return {
        **item,
        "price": round(price, 2),
        "change_pct": round(change, 2),
        "volume_pace": round(pace, 2),
        "attack_volume": round(attack, 1),
        "rsi": round(rsi, 1),
        "ma5": round(ma5, 2),
        "ma10": round(ma10, 2),
        "ma20": round(ma20, 2),
        "ma60": round(ma60, 2),
        "avg_volume5": int(avg5),
        "avg_volume10": int(avg10),
        "avg_volume20": int(avg20),
        "ma20_distance_pct": round((price / ma20 - 1) * 100 if ma20 else 0.0, 2),
        "support1": round(support1, 2),
        "support2": round(support2, 2),
        "resistance1": round(resistance1, 2),
        "resistance2": round(resistance2, 2),
        "foreign_net": int(_finite(inst.get("foreign"))),
        "trust_net": int(_finite(inst.get("trust"))),
        "dealer_net": int(_finite(inst.get("dealer"))),
        "institution_net": int(inst_net),
        "institution_available": bool(_finite(inst.get("available"))),
        "institution_1d": int(_finite(inst.get("institution_1d", inst_net))),
        "institution_3d": int(_finite(inst.get("institution_3d", inst_net))),
        "institution_5d": int(_finite(inst.get("institution_5d", inst_net))),
        "institution_10d": int(_finite(inst.get("institution_10d", inst_net))),
    }


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return min(high, max(low, value))


def score_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    theme_change: dict[str, float] = {}
    for theme in {str(r["theme"]) for r in rows}:
        changes = [r["change_pct"] for r in rows if str(r["theme"]) == theme]
        theme_change[theme] = float(np.median(changes)) if changes else 0.0

    for row in rows:
        technical = 50.0
        technical += _clamp(row["change_pct"] * 4, -20, 20)
        technical += 7 if row["price"] >= row["ma5"] else -6
        technical += 7 if row["ma5"] >= row["ma10"] else -6
        technical += 7 if row["ma10"] >= row["ma20"] else -6
        technical += 5 if row["ma20"] >= row["ma60"] else -5
        if row["rsi"] >= 78:
            technical -= 12
        elif 52 <= row["rsi"] <= 70:
            technical += 6

        volume_score = _clamp(35 + (row["volume_pace"] - 1) * 35 + row["attack_volume"] * 0.25)
        if row.get("institution_available"):
            participation = row["institution_5d"] / max(row["avg_volume20"] * 5, 1) * 100
            continuity = 0
            if row["institution_1d"] > 0 and row["institution_3d"] > 0 and row["institution_5d"] > 0:
                continuity = 8
            elif row["institution_1d"] < 0 and row["institution_3d"] < 0 and row["institution_5d"] < 0:
                continuity = -8
            institution_score = _clamp(50 + participation * 2 + continuity)
        else:
            institution_score = 50.0
        credit_score = 50.0
        if row.get("credit_available"):
            five_day_volume = max(row["avg_volume20"] * 5, 1)
            margin_pressure = _finite(row.get("margin_5d_change")) / five_day_volume * 100
            short_pressure = (
                _finite(row.get("short_5d_change")) + _finite(row.get("sbl_5d_change"))
            ) / five_day_volume * 100
            # Rising margin is crowded retail leverage; rising short/SBL is sell pressure.
            credit_score = _clamp(50 - margin_pressure * 1.2 - short_pressure * 1.5)
            institution_score = institution_score * 0.72 + credit_score * 0.28
        group_score = _clamp(50 + theme_change[str(row["theme"])] * 10)
        position_score = 65 if row["price"] <= row["support1"] * 1.03 else 52
        if row["price"] >= row["resistance2"] * 0.995:
            position_score -= 12
        if row["ma20_distance_pct"] >= 10:
            position_score -= 15

        total = (
            _clamp(technical) * 0.30
            + volume_score * 0.25
            + institution_score * 0.18
            + group_score * 0.17
            + _clamp(position_score) * 0.10
        )
        row.update({
            "technical_score": round(_clamp(technical), 1),
            "volume_score": round(volume_score, 1),
            "institution_score": round(institution_score, 1),
            "credit_score": round(credit_score, 1),
            "group_score": round(group_score, 1),
            "score": round(total, 1),
            "theme_change_pct": round(theme_change[str(row["theme"])], 2),
        })
        row["buy_price"] = round(min(row["price"], row["support1"] * 1.01), 2)
        row["stop_price"] = round(row["support2"] * 0.98, 2)
        sbl_pressure = _finite(row.get("sbl_5d_change")) / max(row["avg_volume20"] * 5, 1) * 100
        if row.get("credit_available") and sbl_pressure >= 5 and row["price"] < row["ma20"]:
            row["risk"] = "借券賣壓增加且跌破月線"
        elif row["ma20_distance_pct"] >= 12:
            row["risk"] = "乖離月線過大，勿追高"
        elif row["rsi"] >= 78 or row["volume_pace"] >= 3.5:
            row["risk"] = "過熱，勿追高"
        elif row["price"] < row["ma20"]:
            row["risk"] = "跌破月線，等待止穩"
        elif row["volume_pace"] < 0.65:
            row["risk"] = "量能不足"
        else:
            row["risk"] = "一般波動"

        trend_ready = row["price"] >= row["ma10"] >= row["ma20"]
        not_extended = row["ma20_distance_pct"] < 10
        if total >= 70 and row["attack_volume"] > 0 and trend_ready and not_extended:
            row["action"] = "🟢 可分批，等回測買點"
        elif total >= 58:
            row["action"] = "🟡 觀察，不追高"
        else:
            row["action"] = "🔴 暫不買"

    ranked = sorted(rows, key=lambda r: (r["score"], r["volume_pace"], r["change_pct"]), reverse=True)
    for rank, row in enumerate(ranked, 1):
        row["rank"] = rank
    return ranked
