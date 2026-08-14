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


def _attack_volume(frame: pd.DataFrame) -> float:
    """Signed up-bar volume share; positive means buyers controlled the window."""
    if frame.empty:
        return 0.0
    volume = frame.get("volume", pd.Series(0, index=frame.index)).fillna(0)
    open_ = frame.get("open", pd.Series(dtype=float))
    close = frame.get("close", pd.Series(dtype=float))
    up = float(volume[close > open_].sum()) if len(close) else 0.0
    down = float(volume[close < open_].sum()) if len(close) else 0.0
    return (up - down) / max(up + down, 1.0) * 100


def _intraday_metrics(
    frame: pd.DataFrame | None, avg_volume: float
) -> tuple[float, float, float | None, float | None, float | None]:
    if frame is None or frame.empty:
        return 1.0, 0.0, None, None, None
    volume = frame.get("volume", pd.Series(dtype=float)).fillna(0)
    close = frame.get("close", pd.Series(dtype=float))
    total = float(volume.sum())
    expected = max(avg_volume * market_session_fraction(), 1.0)
    pace = total / expected
    attack = _attack_volume(frame)
    # The feed uses 5-minute bars. Three/six bars are the opening 15/30 minutes.
    attack15 = _attack_volume(frame.iloc[:3]) if len(frame) >= 3 else None
    attack30 = _attack_volume(frame.iloc[:6]) if len(frame) >= 6 else None
    live_price = _finite(close.dropna().iloc[-1], 0.0) if not close.dropna().empty else None
    return pace, attack, live_price, attack15, attack30


def _candlestick_features(
    open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series,
    volume: pd.Series, avg_volume20: float,
) -> dict[str, Any]:
    """Return explainable daily candle, volume-price and 20-day breakout signals."""
    current_open = _finite(open_.iloc[-1], _finite(close.iloc[-1]))
    current_close = _finite(close.iloc[-1])
    current_high = _finite(high.iloc[-1], max(current_open, current_close))
    current_low = _finite(low.iloc[-1], min(current_open, current_close))
    previous_open = _finite(open_.iloc[-2], _finite(close.iloc[-2])) if len(close) > 1 else current_open
    previous_close = _finite(close.iloc[-2], current_close) if len(close) > 1 else current_close
    candle_range = max(current_high - current_low, abs(current_close) * 0.001, 0.01)
    body = current_close - current_open
    body_abs = abs(body)
    upper_wick = current_high - max(current_open, current_close)
    lower_wick = min(current_open, current_close) - current_low
    body_ratio = body_abs / candle_range
    labels: list[str] = []
    score_delta = 0.0

    bullish_engulfing = (
        previous_close < previous_open and body > 0
        and current_open <= previous_close and current_close >= previous_open
    )
    bearish_engulfing = (
        previous_close > previous_open and body < 0
        and current_open >= previous_close and current_close <= previous_open
    )
    if bullish_engulfing:
        labels.append("多方吞噬")
        score_delta += 8
    elif bearish_engulfing:
        labels.append("空方吞噬")
        score_delta -= 8
    elif body_ratio <= 0.12:
        labels.append("十字線")
    elif lower_wick >= max(body_abs * 2, candle_range * 0.45) and upper_wick <= candle_range * 0.25:
        labels.append("錘子線")
        score_delta += 5
    elif upper_wick >= max(body_abs * 2, candle_range * 0.45) and lower_wick <= candle_range * 0.25:
        labels.append("上影壓力")
        score_delta -= 5
    elif body_ratio >= 0.65 and body > 0:
        labels.append("長紅K")
        score_delta += 5
    elif body_ratio >= 0.65 and body < 0:
        labels.append("長黑K")
        score_delta -= 6
    else:
        labels.append("小紅K" if body > 0 else "小黑K")

    prior_high20 = _finite(high.iloc[:-1].tail(20).max(), current_high)
    prior_low20 = _finite(low.iloc[:-1].tail(20).min(), current_low)
    breakout20 = current_close > prior_high20
    breakdown20 = current_close < prior_low20
    if breakout20:
        labels.append("突破20日高")
        score_delta += 8
    elif breakdown20:
        labels.append("跌破20日低")
        score_delta -= 10

    daily_volume_ratio = _finite(volume.iloc[-1], 0.0) / max(avg_volume20, 1.0)
    daily_change = current_close - previous_close
    if daily_volume_ratio >= 1.3 and daily_change > 0:
        volume_price = "價漲量增"
        score_delta += 5
    elif daily_volume_ratio >= 1.3 and daily_change < 0:
        volume_price = "價跌量增"
        score_delta -= 7
    elif daily_volume_ratio <= 0.8 and daily_change >= 0:
        volume_price = "價穩量縮"
        score_delta += 2
    elif daily_volume_ratio <= 0.8 and daily_change < 0:
        volume_price = "量縮整理"
    else:
        volume_price = "量價中性"

    return {
        "kline_pattern": "／".join(labels),
        "kline_score": round(_clamp(50 + score_delta), 1),
        "daily_volume_ratio": round(daily_volume_ratio, 2),
        "volume_price_pattern": volume_price,
        "breakout20": breakout20,
        "breakdown20": breakdown20,
    }


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
    open_ = daily.get("open", close).astype(float)
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
    candle = _candlestick_features(open_, high, low, close, volume, avg20)
    pace, attack, live, attack15, attack30 = _intraday_metrics(intraday, avg20)
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
        "opening_attack_15m": None if attack15 is None else round(attack15, 1),
        "opening_attack_30m": None if attack30 is None else round(attack30, 1),
        "intraday_available": attack15 is not None,
        "rsi": round(rsi, 1),
        "ma5": round(ma5, 2),
        "ma10": round(ma10, 2),
        "ma20": round(ma20, 2),
        "ma60": round(ma60, 2),
        "avg_volume5": int(avg5),
        "avg_volume10": int(avg10),
        "avg_volume20": int(avg20),
        "ma20_distance_pct": round((price / ma20 - 1) * 100 if ma20 else 0.0, 2),
        **candle,
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


def _next_day_scenario(row: dict[str, Any]) -> dict[str, Any]:
    """Build factual conditional scenarios without inventing a win probability."""
    price = max(_finite(row.get("price")), 0.01)
    support1 = min(_finite(row.get("support1"), price), price)
    support2 = min(_finite(row.get("support2"), support1), support1)
    resistance1 = max(_finite(row.get("resistance1"), price), price)
    resistance2 = max(_finite(row.get("resistance2"), resistance1), resistance1)
    defense_low = support1
    defense_high = min(price, support1 * 1.015)
    if defense_high < defense_low:
        defense_high = defense_low
    no_chase_low = resistance1
    no_chase_high = max(resistance1 * 1.03, min(resistance2, resistance1 * 1.05))

    if row.get("institution_available"):
        foreign = int(_finite(row.get("foreign_net")))
        institution = int(_finite(row.get("institution_1d")))
        institution_text = (
            f"外資 {foreign:+,} 股、三大法人 {institution:+,} 股"
        )
    else:
        institution_text = "法人資料未取得，不推定主力方向"

    volume_ratio = _finite(row.get("daily_volume_ratio"), 1.0)
    volume_text = f"日量為20日均量 {volume_ratio:.2f} 倍"
    intraday_text = (
        "開盤15～30分鐘攻擊量轉正且相對量能達1.3倍"
        if row.get("intraday_available")
        else "待開盤15～30分鐘資料出現後再確認量價"
    )
    quality = "完整" if row.get("institution_available") and row.get("intraday_available") else "部分資料"

    return {
        "scenario_defense_low": round(defense_low, 2),
        "scenario_defense_high": round(defense_high, 2),
        "scenario_breakdown": round(support2, 2),
        "scenario_breakout": round(resistance1, 2),
        "scenario_no_chase_low": round(no_chase_low, 2),
        "scenario_no_chase_high": round(no_chase_high, 2),
        "scenario_basis": f"{institution_text}；{volume_text}",
        "scenario_continuation": (
            f"守住 {defense_low:.2f}～{defense_high:.2f}，且{intraday_text}，續攻條件轉強"
        ),
        "scenario_no_chase": (
            f"若直接跳到 {no_chase_low:.2f}～{no_chase_high:.2f}，爆量卻無法站穩 "
            f"{resistance1:.2f}，不追，提防高檔換手"
        ),
        "scenario_breakdown_text": (
            f"跌破 {support1:.2f} 且30分鐘無法收回先減碼；跌破 {support2:.2f} 視為轉弱"
        ),
        "scenario_data_quality": quality,
    }


def _performance_adjustment(
    performance: dict[str, Any], signal: str
) -> tuple[float, int, str | None]:
    """Return a strongly shrunk adjustment from verified forward outcomes."""
    if not performance.get("calibration", {}).get("affects_ai_score"):
        return 0.0, 0, None
    metrics = performance.get("signals", {}).get(signal, {})
    for horizon in ("5", "1"):
        metric = metrics.get(horizon, {})
        samples = int(metric.get("samples") or 0)
        if not samples:
            continue
        win_rate = _finite(metric.get("win_rate_pct"), 50.0)
        avg_return = _finite(metric.get("avg_return_pct"), 0.0)
        direction_return = -avg_return if signal == "🔴" else avg_return
        raw_edge = (win_rate - 50) * 0.04 + direction_return * 0.15
        confidence = min(0.5, samples / (samples + 100))
        return _clamp(raw_edge * confidence, -2.0, 2.0), samples, horizon
    return 0.0, 0, None


def score_candidates(
    rows: list[dict[str, Any]],
    macro_regime: dict[str, Any] | None = None,
    performance: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not rows:
        return []
    macro_regime = macro_regime or {}
    performance = performance or {}
    macro_score = _finite(macro_regime.get("score"), 50.0)
    macro_active = bool(
        macro_regime.get("calibration", {}).get("affects_ai_score")
    )
    # Keep the original model untouched during calibration. After 16 valid
    # trading days, macro risk can adjust the total by at most +/-4 points.
    macro_adjustment = _clamp((macro_score - 50) * 0.08, -4.0, 4.0) if macro_active else 0.0
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
        technical += (_finite(row.get("kline_score"), 50) - 50) * 0.35

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

        valuation_parts: list[float] = []
        per = _finite(row.get("per"), 0)
        if per:
            valuation_parts.append(75 if 0 < per <= 12 else 68 if per <= 20 else 58 if per <= 30 else 45 if per <= 45 else 35)
        pbr = _finite(row.get("pbr"), 0)
        if pbr:
            valuation_parts.append(68 if pbr <= 1.5 else 58 if pbr <= 3 else 50 if pbr <= 6 else 38)
        dividend_yield = _finite(row.get("dividend_yield"), 0)
        if dividend_yield:
            valuation_parts.append(_clamp(50 + dividend_yield * 4, 50, 75))
        valuation_score = float(np.mean(valuation_parts)) if valuation_parts else 50.0
        growth_parts: list[float] = []
        if row.get("revenue_yoy_pct") is not None:
            growth_parts.append(_clamp(50 + _finite(row.get("revenue_yoy_pct")) * 0.8))
        if row.get("revenue_mom_pct") is not None:
            growth_parts.append(_clamp(50 + _finite(row.get("revenue_mom_pct")) * 0.35))
        growth_score = float(np.mean(growth_parts)) if growth_parts else 50.0
        quality_parts: list[float] = []
        if row.get("eps_yoy_pct") is not None:
            quality_parts.append(_clamp(50 + _finite(row.get("eps_yoy_pct")) * 0.5))
        if row.get("gross_margin_pct") is not None:
            quality_parts.append(_clamp(35 + _finite(row.get("gross_margin_pct"))))
        if row.get("operating_margin_pct") is not None:
            quality_parts.append(_clamp(45 + _finite(row.get("operating_margin_pct")) * 1.2))
        if row.get("roe_pct") is not None:
            quality_parts.append(_clamp(45 + _finite(row.get("roe_pct")) * 2))
        if row.get("debt_ratio_pct") is not None:
            quality_parts.append(_clamp(80 - _finite(row.get("debt_ratio_pct")) * 0.6))
        if row.get("operating_cash_flow_positive") is not None:
            quality_parts.append(65 if row.get("operating_cash_flow_positive") else 35)
        quality_score = float(np.mean(quality_parts)) if quality_parts else 50.0
        if row.get("financial_quality_available"):
            fundamental_score = valuation_score * 0.35 + growth_score * 0.25 + quality_score * 0.40
        else:
            fundamental_score = valuation_score * 0.55 + growth_score * 0.45

        base_total = (
            _clamp(technical) * 0.28
            + volume_score * 0.23
            + institution_score * 0.17
            + fundamental_score * 0.10
            + group_score * 0.14
            + _clamp(position_score) * 0.08
            + macro_adjustment
        )
        preliminary_signal = "🟢" if base_total >= 70 else "🟡" if base_total >= 58 else "🔴"
        performance_adjustment, performance_samples, performance_horizon = (
            _performance_adjustment(performance, preliminary_signal)
        )
        total = _clamp(base_total + performance_adjustment)
        row.update({
            "technical_score": round(_clamp(technical), 1),
            "volume_score": round(volume_score, 1),
            "institution_score": round(institution_score, 1),
            "credit_score": round(credit_score, 1),
            "valuation_score": round(valuation_score, 1),
            "growth_score": round(growth_score, 1),
            "fundamental_score": round(fundamental_score, 1),
            "financial_quality_score": round(quality_score, 1),
            "group_score": round(group_score, 1),
            "macro_score": round(macro_score, 1),
            "macro_adjustment": round(macro_adjustment, 1),
            "macro_affects_score": macro_active,
            "performance_adjustment": round(performance_adjustment, 2),
            "performance_samples": performance_samples,
            "performance_horizon": performance_horizon,
            "score": round(total, 1),
            "theme_change_pct": round(theme_change[str(row["theme"])], 2),
        })
        row["buy_price"] = round(min(row["price"], row["support1"] * 1.01), 2)
        row["stop_price"] = round(row["support2"] * 0.98, 2)
        sbl_pressure = _finite(row.get("sbl_5d_change")) / max(row["avg_volume20"] * 5, 1) * 100
        if row.get("breakdown20"):
            row["risk"] = "跌破20日低點，先避開"
        elif row.get("credit_available") and sbl_pressure >= 5 and row["price"] < row["ma20"]:
            row["risk"] = "借券賣壓增加且跌破月線"
        elif row.get("fundamental_available") and _finite(row.get("revenue_yoy_pct")) < -15 and per > 40:
            row["risk"] = "估值偏高且營收衰退"
        elif row.get("financial_quality_available") and not row.get("operating_cash_flow_positive") and _finite(row.get("debt_ratio_pct")) >= 65:
            row["risk"] = "營業現金流轉負且負債偏高"
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
        kline_ready = _finite(row.get("kline_score"), 50) >= 45 and not row.get("breakdown20")
        fundamental_ready = not row.get("fundamental_available") or fundamental_score >= 40
        quality_ready = not row.get("financial_quality_available") or quality_score >= 38
        if total >= 70 and row["attack_volume"] > 0 and trend_ready and not_extended and kline_ready and fundamental_ready and quality_ready:
            row["action"] = "🟢 可分批，等回測買點"
        elif total >= 58:
            row["action"] = "🟡 觀察，不追高"
        else:
            row["action"] = "🔴 暫不買"

        row.update(_next_day_scenario(row))

    ranked = sorted(rows, key=lambda r: (r["score"], r["volume_pace"], r["change_pct"]), reverse=True)
    for rank, row in enumerate(ranked, 1):
        row["rank"] = rank
    return ranked
