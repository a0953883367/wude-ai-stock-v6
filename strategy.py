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
    atr14 = _finite((high - low).tail(14).mean(), price * 0.02)
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
        "atr14": round(atr14, 2),
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



def _positioning_radar(row: dict[str, Any]) -> dict[str, Any]:
    """Estimate observable buying/short pressure without claiming hidden intent.

    The radar is explanatory only and deliberately does not alter the AI score.
    Taiwan and US markets use different evidence because their disclosures differ.
    """
    market = str(row.get("market", "US")).upper()
    score = 50.0
    evidence: list[str] = []
    available_groups = 0
    change = _finite(row.get("change_pct"))
    volume_ratio = _finite(row.get("daily_volume_ratio"), 1.0)
    attack15 = _finite(row.get("opening_attack_15m"))
    attack30 = _finite(row.get("opening_attack_30m"))

    if market == "TW":
        if row.get("institution_available"):
            available_groups += 1
            one = _finite(row.get("institution_1d"))
            five = _finite(row.get("institution_5d"))
            if one > 0 and five > 0:
                score += 16
                evidence.append(f"三大法人1日與5日同步買超（{one:+,.0f}／{five:+,.0f}股）")
            elif one < 0 and five < 0:
                score -= 16
                evidence.append(f"三大法人1日與5日同步賣超（{one:+,.0f}／{five:+,.0f}股）")
            else:
                score += 4 if five > 0 else -4 if five < 0 else 0
                evidence.append(f"三大法人5日累計 {five:+,.0f} 股，短線方向未完全一致")
        else:
            evidence.append("法人資料未取得，不推定主力方向")

        if row.get("credit_available"):
            available_groups += 1
            short5 = _finite(row.get("short_5d_change"))
            sbl5 = _finite(row.get("sbl_5d_change"))
            margin5 = _finite(row.get("margin_5d_change"))
            short_pressure = short5 + sbl5
            base = max(_finite(row.get("avg_volume20")) * 5, 1)
            pressure_pct = short_pressure / base * 100
            if pressure_pct >= 2:
                score -= 12
                evidence.append(f"融券＋借券賣出5日增加 {short_pressure:+,.0f} 股，空方壓力升高")
            elif pressure_pct <= -2:
                score += 9
                evidence.append(f"融券＋借券賣出5日減少 {short_pressure:+,.0f} 股，空單回補")
            elif margin5 > base * 0.03 and change < 0:
                score -= 7
                evidence.append("股價偏弱但融資增加，籌碼擁擠需留意")
            else:
                evidence.append("融資融券與借券變化未達明顯門檻")

        if row.get("broker_available"):
            available_groups += 1
            buy_net = sum(max(_finite(item.get("net")), 0) for item in row.get("top_brokers_buy", []))
            sell_net = abs(sum(min(_finite(item.get("net")), 0) for item in row.get("top_brokers_sell", [])))
            if buy_net > sell_net * 1.25:
                score += 7
                evidence.append("主要買超分點金額／張數集中度高於賣超分點")
            elif sell_net > buy_net * 1.25:
                score -= 7
                evidence.append("主要賣超分點集中度高於買超分點")
    else:
        if row.get("us_short_volume_available"):
            available_groups += 1
            ratio = _finite(row.get("us_short_volume_ratio_pct"))
            report_date = str(row.get("us_short_volume_date", ""))
            if ratio >= 55:
                score -= 11
                evidence.append(f"FINRA {report_date} 每日放空成交占比 {ratio:.1f}%，偏高")
            elif ratio <= 35:
                score += 6
                evidence.append(f"FINRA {report_date} 每日放空成交占比 {ratio:.1f}%，偏低")
            else:
                evidence.append(f"FINRA {report_date} 每日放空成交占比 {ratio:.1f}%，中性")
        else:
            evidence.append("FINRA每日放空成交資料未取得，不推定空單部位")

    if row.get("intraday_available"):
        available_groups += 1
        attack = attack30 if attack30 else attack15
        if attack >= 12 and change >= 0:
            score += 10
            evidence.append(f"開盤攻擊量 {attack:+.1f}%，買盤轉強")
        elif attack <= -12 and change <= 0:
            score -= 10
            evidence.append(f"開盤攻擊量 {attack:+.1f}%，賣壓增強")

    if volume_ratio >= 1.5 and change >= 2:
        score += 7
        evidence.append(f"價漲量增，日量為20日均量 {volume_ratio:.2f} 倍")
    elif volume_ratio >= 1.5 and change <= -2:
        score -= 9
        evidence.append(f"價跌量增，日量為20日均量 {volume_ratio:.2f} 倍")
    elif not evidence:
        evidence.append("目前量價訊號中性")

    score = round(_clamp(score), 1)
    if score >= 68:
        signal = "🔥 多方押注"
    elif score >= 58:
        signal = "🟢 買盤／回補轉強"
    elif score <= 32:
        signal = "🔴 放空／賣壓增強"
    elif score <= 42:
        signal = "⚠️ 籌碼偏空"
    else:
        signal = "⚪ 多空不明"

    needed = 3 if market == "TW" else 2
    result = {
        "positioning_market": market,
        "positioning_score": score,
        "positioning_signal": signal,
        "positioning_evidence": evidence[:4],
        "positioning_data_quality": "完整" if available_groups >= needed else "部分資料",
        "positioning_affects_ai_score": False,
    }
    if market == "US":
        result["positioning_disclaimer"] = "FINRA每日放空成交量≠未回補空單；本雷達不把它當成即時主力持倉"
    return result


def _next_day_scenario(row: dict[str, Any]) -> dict[str, Any]:
    """Build market-specific conditional scenarios without inventing probability."""
    market = str(row.get("market", "US")).upper()
    is_tw = market == "TW"
    price = max(_finite(row.get("price")), 0.01)
    support1 = min(_finite(row.get("support1"), price), price)
    support2 = min(_finite(row.get("support2"), support1), support1)
    resistance1 = max(_finite(row.get("resistance1"), price), price)
    resistance2 = max(_finite(row.get("resistance2"), resistance1), resistance1)
    defense_low = support1
    defense_high = max(defense_low, min(price, support1 * 1.015))
    no_chase_low = resistance1
    no_chase_high = max(resistance1 * 1.03, min(resistance2, resistance1 * 1.05))

    if is_tw:
        market_label = "🇹🇼 台股明日劇本"
        if row.get("institution_available"):
            foreign = int(_finite(row.get("foreign_net")))
            institution = int(_finite(row.get("institution_1d")))
            flow_text = f"外資 {foreign:+,} 股、三大法人 {institution:+,} 股"
        else:
            flow_text = "台股法人資料未取得，不推定主力方向"
        opening_text = "台股09:00開盤後15～30分鐘"
        complete = row.get("institution_available") and row.get("intraday_available")
    else:
        market_label = "🇺🇸 美股下個交易日劇本"
        # US stocks must not be judged with Taiwan foreign/trust/dealer fields.
        flow_text = "美股不套用台股三大法人；以盤前跳空、量價與開盤攻擊量判讀"
        opening_text = "美股正式開盤後15～30分鐘"
        complete = row.get("intraday_available")

    volume_ratio = _finite(row.get("daily_volume_ratio"), 1.0)
    volume_text = f"日量為20日均量 {volume_ratio:.2f} 倍"
    intraday_text = (
        f"{opening_text}攻擊量轉正且相對量能達1.3倍"
        if row.get("intraday_available")
        else f"待{opening_text}資料出現後再確認量價"
    )

    return {
        "scenario_market": market,
        "scenario_title": market_label,
        "scenario_defense_low": round(defense_low, 2),
        "scenario_defense_high": round(defense_high, 2),
        "scenario_breakdown": round(support2, 2),
        "scenario_breakout": round(resistance1, 2),
        "scenario_no_chase_low": round(no_chase_low, 2),
        "scenario_no_chase_high": round(no_chase_high, 2),
        "scenario_basis": f"{flow_text}；{volume_text}",
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
        "scenario_data_quality": "完整" if complete else "部分資料",
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


def _price_tick(row: dict[str, Any], value: float) -> float:
    """Return a valid market price increment for display and limit orders."""
    if str(row.get("market", "")).upper() != "TW":
        return 0.01
    if "ETF" in str(row.get("type", "")).upper():
        return 0.01
    if value < 10:
        return 0.01
    if value < 50:
        return 0.05
    if value < 100:
        return 0.10
    if value < 500:
        return 0.50
    if value < 1000:
        return 1.00
    return 5.00


def _market_price(row: dict[str, Any], value: float) -> float:
    value = max(value, 0.01)
    tick = _price_tick(row, value)
    rounded = round(value / tick) * tick
    decimals = 2 if tick < 0.1 else 1 if tick < 1 else 0
    return round(max(rounded, tick), decimals)


def _entry_coverage(row: dict[str, Any], is_etf: bool) -> tuple[int, int]:
    """Count only data that was really obtained; neutral fallbacks are not coverage."""
    technical = bool(row.get("price")) and bool(row.get("ma20")) and row.get("rsi") is not None
    volume = _finite(row.get("avg_volume20")) > 0 and row.get("volume_pace") is not None
    positioning = bool(
        row.get("institution_available")
        or row.get("us_short_volume_available")
        or row.get("broker_available")
    )
    news = bool(row.get("news_data_available"))
    if is_etf:
        flags = (technical, volume, positioning, news)
    else:
        flags = (
            technical,
            volume,
            positioning,
            bool(row.get("fundamental_available")),
            bool(row.get("financial_quality_available")),
            news,
        )
    return sum(flags), len(flags)


def _entry_plan(
    row: dict[str, Any],
    total: float,
    technical: float,
    volume_score: float,
    institution_score: float,
    group_score: float,
    position_score: float,
) -> dict[str, Any]:
    """Build market-specific, ATR-aware and executable entry zones."""
    price = max(_finite(row.get("price")), 0.01)
    market = str(row.get("market", "")).upper()
    is_etf = "ETF" in str(row.get("type", "")).upper()
    raw_atr = _finite(row.get("atr14"))
    atr_pct = raw_atr / price if raw_atr > 0 else 0.0

    if is_etf:
        profile = "ETF"
        first_near, first_far = 0.008, 0.018
        better_near, better_far = 0.022, 0.038
        atr_floor, stop_gap = 0.008, 0.007
    elif market == "TW":
        profile = "台股個股"
        first_near, first_far = 0.010, 0.020
        better_near, better_far = 0.025, 0.040
        atr_floor, stop_gap = 0.012, 0.010
    else:
        high_volatility = atr_pct >= 0.04
        if high_volatility:
            profile = "美股高波動"
            first_near, first_far = 0.025, 0.050
            better_near, better_far = 0.060, 0.100
            atr_floor, stop_gap = 0.030, 0.020
        else:
            profile = "美股一般"
            first_near, first_far = 0.015, 0.030
            better_near, better_far = 0.035, 0.060
            atr_floor, stop_gap = 0.018, 0.015

    # ATR can widen a zone, but can never shrink it into a meaningless
    # near-current-price interval.
    effective_atr_pct = max(atr_pct, atr_floor)
    first_near = max(first_near, min(effective_atr_pct * 0.45, first_far * 0.85))
    first_far = max(first_far, min(effective_atr_pct * 1.05, better_near * 0.92))
    better_near = max(better_near, first_far + max(0.004, effective_atr_pct * 0.20))
    better_far = max(better_far, min(effective_atr_pct * 2.0, 0.12))
    if better_far <= better_near:
        better_far = better_near + max(0.01, effective_atr_pct * 0.5)

    buy_low = _market_price(row, price * (1 - first_far))
    buy_high = _market_price(row, price * (1 - first_near))
    better_low = _market_price(row, price * (1 - better_far))
    better_high = _market_price(row, price * (1 - better_near))

    # Enforce ordering after market tick rounding.
    tick = _price_tick(row, price)
    if buy_high >= price:
        buy_high = _market_price(row, price - tick)
    if buy_low >= buy_high:
        buy_low = _market_price(row, buy_high - max(tick, price * 0.005))
    if better_high >= buy_low:
        better_high = _market_price(row, buy_low - max(tick, price * 0.005))
    if better_low >= better_high:
        better_low = _market_price(row, better_high - max(tick, price * 0.01))

    timing = (
        _clamp(technical) * 0.30
        + _clamp(volume_score) * 0.25
        + _clamp(institution_score) * 0.15
        + _clamp(position_score) * 0.20
        + _clamp(group_score) * 0.10
    )
    entry_score = timing if is_etf else total * 0.55 + timing * 0.45
    rsi = _finite(row.get("rsi"), 50)
    distance = _finite(row.get("ma20_distance_pct"))
    if rsi >= 70:
        entry_score -= min(12.0, (rsi - 70) * 1.5)
    if distance >= 8:
        entry_score -= min(15.0, (distance - 8) * 1.5 + 4)
    resistance1 = _finite(row.get("resistance1"), price)
    if resistance1 > 0 and price >= resistance1 * 0.985:
        entry_score -= 8
    if _finite(row.get("volume_pace"), 1) < 0.75:
        entry_score -= 4

    coverage, coverage_total = _entry_coverage(row, is_etf)
    missing = coverage_total - coverage
    if is_etf:
        cap = 75.0 if coverage <= 2 else 86.0 if coverage == 3 else 100.0
    else:
        cap = 75.0 if coverage <= 3 else 82.0 if coverage == 4 else 90.0 if coverage == 5 else 100.0
    entry_score = min(entry_score, cap)

    if missing:
        note = f"資料涵蓋 {coverage}/{coverage_total}，僅列觀察；等價格回測且補齊資料"
    elif price > buy_high:
        note = f"{profile}區間：現價高於第一買進區，等待回測、不追價"
    elif buy_low <= price <= buy_high:
        note = f"{profile}區間：進入第一買進區，可小量分批"
    else:
        note = f"{profile}區間：跌破原買進區，先確認止跌與量價"

    stop_distance = max(price * stop_gap, raw_atr * 0.75)
    stop_price = _market_price(row, better_low - stop_distance)
    return {
        "entry_score": round(_clamp(entry_score), 1),
        "entry_score_cap": cap,
        "entry_data_coverage": coverage,
        "entry_data_total": coverage_total,
        "entry_profile": profile,
        "buy_zone_low": buy_low,
        "buy_zone_high": buy_high,
        "better_buy_low": better_low,
        "better_buy_high": better_high,
        "buy_price": _market_price(row, (buy_low + buy_high) / 2),
        "stop_price": stop_price,
        "entry_note": note,
    }


def _available_weighted_score(
    components: list[tuple[float, float, bool]],
) -> tuple[float, int, int, float]:
    """Score only verified dimensions and reduce confidence when data is missing."""
    available = [(score, weight) for score, weight, ok in components if ok]
    total_weight = sum(weight for _, weight, _ in components)
    available_weight = sum(weight for _, weight in available)
    count = len(available)
    total = len(components)
    if not available or total_weight <= 0:
        return 0.0, count, total, 0.0
    raw = sum(_clamp(score) * weight for score, weight in available) / available_weight
    coverage = available_weight / total_weight
    # Missing dimensions never receive a neutral 50. They lower confidence and rank.
    adjusted = raw * (0.55 + 0.45 * coverage)
    return round(_clamp(adjusted), 1), count, total, round(coverage * 100, 1)


def _short_term_plan(row: dict[str, Any]) -> dict[str, Any]:
    """Conservative trigger-based plan for a 1-5 trading day trade."""
    market = str(row.get("market") or "").upper()
    is_etf = "ETF" in str(row.get("type") or "").upper()
    price, ma5, ma10, ma20 = map(lambda k: _finite(row.get(k)), ("price", "ma5", "ma10", "ma20"))
    atr = _finite(row.get("atr14"))
    rsi = _finite(row.get("rsi"), 50.0)
    volume_pace = _finite(row.get("volume_pace"), 1.0)
    attack = _finite(row.get("attack_volume"))
    entry_score = _finite(row.get("entry_score"), _finite(row.get("score")))
    technical = _finite(row.get("technical_score"), 50.0)
    volume_score = _finite(row.get("volume_score"), 50.0)
    positioning = _finite(row.get("positioning_score"), 50.0)
    news_penalty = _finite(row.get("news_penalty"))
    coverage_total = int(_finite(row.get("entry_data_coverage")))
    coverage_expected = int(_finite(row.get("entry_data_total"), 4 if is_etf else 6))
    entry_low = _finite(row.get("buy_zone_low"), _finite(row.get("better_buy_low")))
    entry_high = _finite(row.get("buy_zone_high"), _finite(row.get("better_buy_high")))
    support1 = _finite(row.get("support1"), entry_low)
    resistance1 = _finite(row.get("resistance1"))
    resistance2 = _finite(row.get("resistance2"))

    coverage_ok = coverage_total >= min(3 if is_etf else 4, max(coverage_expected, 1))
    technical_ok = all(v > 0 for v in (price, ma10, ma20, atr, entry_low, entry_high))
    if ma5 > 0 and price >= ma5 >= ma10 >= ma20:
        trend_score, setup = 88.0, "多頭回測"
    elif price >= ma10 >= ma20:
        trend_score, setup = 78.0, "短均線續強"
    elif price >= ma20 * 0.99:
        trend_score, setup = 62.0, "月線防守"
    else:
        trend_score, setup = 30.0, "趨勢未確認"

    attack_score = _clamp(50 + attack * 0.8)
    structure_score = _clamp((trend_score + _finite(row.get("position_score"), 50.0)) / 2)
    news_score = _clamp(100 - max(news_penalty, 0.0) * 6)
    score, short_available, short_total, short_confidence = _available_weighted_score([
        (volume_score, 25, _finite(row.get("avg_volume20")) > 0),
        (technical, 20, technical_ok),
        (positioning, 20, bool(row.get("institution_available") or row.get("credit_available"))),
        (attack_score, 15, row.get("attack_volume") is not None),
        (structure_score, 15, technical_ok),
        (news_score, 5, bool(row.get("news_data_available"))),
    ])
    if rsi >= 72:
        score -= min(12.0, (rsi-72)*1.5+4)
    if ma20 > 0 and price/ma20-1 >= .10:
        score -= 8
    if row.get("breakdown20"):
        score -= 15
    score = round(_clamp(score, 0.0, 100.0), 1)

    max_loss = (.035 if is_etf else .045) if market == "TW" else (.040 if is_etf else .055)
    if str(row.get("entry_profile") or "") == "美股高波動":
        max_loss = .070
    tick = _tick_size(row, max(price, 1.0))
    anchors = [v for v in (entry_low, support1) if v > 0]
    anchor = min(anchors) if anchors else 0.0
    structural = anchor-atr*.35 if anchor and atr else price*(1-max_loss)
    loss_floor = entry_high*(1-max_loss) if entry_high else price*(1-max_loss)
    stop = _market_price(row, max(structural, loss_floor))
    if entry_low and stop >= entry_low:
        stop = _market_price(row, entry_low-max(atr*.35, tick))
    risk = max(entry_high-stop, tick) if entry_high else tick
    target1 = _market_price(row, max(resistance1, entry_high+risk*1.5))
    target2 = _market_price(row, max(resistance2, entry_high+risk*2.3, target1+tick))
    rr = round((target1-entry_high)/risk, 2) if risk > 0 and entry_high else 0.0

    market_relative_volume = _finite(row.get("market_relative_volume"), 1.0)
    volume_ok = (
        volume_pace >= (.70 if is_etf else .75)
        or market_relative_volume >= 0.90
        or attack > 0
    )
    eligible = bool(
        technical_ok and coverage_ok and entry_score >= 58 and score >= 60
        and price >= ma20*.99 and 40 <= rsi <= 72 and volume_ok
        and not row.get("breakdown20") and news_penalty < 8
        and price <= entry_high*(1.08 if is_etf else 1.10) and rr >= 1.5
    )
    if eligible:
        status = "🟢 等待觸發"
        trigger = ("回測進場區止穩，且前15～30分鐘攻擊量轉正才分批進場"
                   if price > entry_high else
                   "進場區不破支撐，出現量增紅K或突破前15分鐘高點才分批進場")
        reason = f"{setup}｜量能合格｜風報比 {rr:.2f}"
    elif not coverage_ok or not technical_ok:
        status, trigger = "⚪ 資料不足", "等待均線、ATR、量能與資料完整度補齊，不先進場"
        reason = f"資料涵蓋 {coverage_total}/{coverage_expected}，暫不列入短線首選"
    elif rr < 1.5:
        status, trigger = "🟡 等待更佳風報比", "現價不追，等待回測使第一停利風報比至少達1.5"
        reason = f"目前風報比 {rr:.2f}，未達短線門檻"
    else:
        status, trigger = "🔴 不列入短線", "等待趨勢、量能、RSI或新聞風險重新符合條件"
        reason = f"{setup}｜短線分數 {score:.1f}｜條件尚未齊全"
    return {
        "short_term_eligible": eligible, "short_term_score": score,
        "short_term_status": status, "short_term_setup": setup,
        "short_term_entry_low": _market_price(row, entry_low) if entry_low else None,
        "short_term_entry_high": _market_price(row, entry_high) if entry_high else None,
        "short_term_trigger": trigger, "short_term_stop": stop if stop > 0 else None,
        "short_term_target1": target1 if target1 > 0 else None,
        "short_term_target2": target2 if target2 > 0 else None,
        "short_term_rr": rr, "short_term_holding_period": "1～5個交易日",
        "short_term_exit_rule": "跌破停損價立即退出；第一停利減碼一半並移動停損至成本，第二停利或跌破5日線退出",
        "short_term_reason": reason,
        "short_term_data_quality": f"{short_available}/{short_total}",
        "short_term_confidence": short_confidence,
    }



def _mid_long_term_plan(row: dict[str, Any]) -> dict[str, Any]:
    """Build a conservative 3-12 month accumulation plan."""
    market = str(row.get("market") or "").upper()
    is_etf = str(row.get("type") or "").upper() == "ETF"
    price = _finite(row.get("price"))
    ma20 = _finite(row.get("ma20"))
    ma60 = _finite(row.get("ma60"), ma20)
    atr = _finite(row.get("atr14"))
    ai_score = _finite(row.get("score"))
    technical = _finite(row.get("technical_score"), 50.0)
    fundamental = _finite(row.get("fundamental_score"), 50.0)
    quality = _finite(row.get("financial_quality_score"), 50.0)
    growth = _finite(row.get("growth_score"), 50.0)
    valuation = _finite(row.get("valuation_score"), 50.0)
    news_penalty = _finite(row.get("news_penalty"))
    better_low = _finite(row.get("better_buy_low"), _finite(row.get("buy_zone_low")))
    better_high = _finite(row.get("better_buy_high"), _finite(row.get("buy_zone_high")))
    support1 = _finite(row.get("support1"), better_low)
    support2 = _finite(row.get("support2"), ma60)
    resistance1 = _finite(row.get("resistance1"))
    resistance2 = _finite(row.get("resistance2"))

    fundamental_available = bool(row.get("fundamental_available"))
    quality_available = bool(row.get("financial_quality_available"))
    growth_available = row.get("revenue_yoy_pct") is not None or row.get("revenue_mom_pct") is not None
    valuation_available = bool(_finite(row.get("per")) or _finite(row.get("pbr")) or _finite(row.get("dividend_yield")))
    institution_available = bool(row.get("institution_available") or row.get("credit_available"))
    news_available = bool(row.get("news_data_available"))
    technical_available = all(v > 0 for v in (price, ma20, ma60, atr))
    news_score = _clamp(100 - max(news_penalty, 0.0) * 6)
    long_score, available, long_total, long_confidence = _available_weighted_score([
        (quality, 25, quality_available),
        (growth, 25, growth_available),
        (valuation, 15, valuation_available),
        (technical, 15, technical_available),
        (_finite(row.get("positioning_score"), 50.0), 10, institution_available),
        (news_score, 10, news_available),
    ])
    required = 3 if is_etf else 4
    data_ok = available >= required
    if price > 0 and ma60 > 0:
        long_score += 4 if price >= ma20 >= ma60 else (-8 if price < ma60*.90 else 0)
    long_score = round(_clamp(long_score, 0.0, 100.0), 1)

    # Three batches: fair pullback, deeper support, and long-term trend defense.
    batch1_low, batch1_high = better_low, better_high
    batch2 = min(v for v in (support1, support2, ma60) if v > 0) if any(v > 0 for v in (support1, support2, ma60)) else 0.0
    batch2_low = batch2-atr*.40 if batch2 > 0 else 0.0
    batch2_high = batch2+atr*.20 if batch2 > 0 else 0.0
    max_loss = .10 if is_etf else (.12 if market == "TW" else .15)
    structural_stop = min(v for v in (support2, ma60) if v > 0)-atr if any(v > 0 for v in (support2, ma60)) else price*(1-max_loss)
    stop_floor = batch1_high*(1-max_loss) if batch1_high > 0 else price*(1-max_loss)
    stop = _market_price(row, max(structural_stop, stop_floor))
    target1 = _market_price(row, max(resistance2, price*1.15, batch1_high*1.18))
    target2 = _market_price(row, max(target1+_tick_size(row, max(target1, 1.0)), price*1.28, batch1_high*1.32))

    trend_ok = price >= ma60*.90 if ma60 > 0 else False
    eligible = bool(
        data_ok and technical_available and ai_score >= 62 and long_score >= 62
        and trend_ok and news_penalty < 10 and batch1_low > 0 and batch1_high > 0
    )
    if eligible:
        status = "🟢 可分批布局" if price <= batch1_high*1.03 else "🟡 等待回測布局"
        reason = f"中長線分數 {long_score:.1f}｜可用資料 {available}/{long_total}｜採三段資金管理"
    elif not data_ok or not technical_available:
        status = "⚪ 資料不足"
        reason = f"可用資料 {available}/{long_total}，個股至少需 {required} 面向才列入"
    elif news_penalty >= 10:
        status = "🔴 重大風險觀察"
        reason = f"負面新聞風險扣分 {news_penalty:.1f}，暫停新增部位"
    else:
        status = "🟡 暫不列入首選"
        reason = f"中長線分數 {long_score:.1f} 或長期趨勢尚未達門檻"

    return {
        "mid_long_eligible": eligible,
        "mid_long_score": long_score,
        "mid_long_status": status,
        "mid_long_period": "3～12個月",
        "mid_long_batch1_low": _market_price(row, batch1_low) if batch1_low > 0 else None,
        "mid_long_batch1_high": _market_price(row, batch1_high) if batch1_high > 0 else None,
        "mid_long_batch2_low": _market_price(row, batch2_low) if batch2_low > 0 else None,
        "mid_long_batch2_high": _market_price(row, batch2_high) if batch2_high > 0 else None,
        "mid_long_stop": stop if stop > 0 else None,
        "mid_long_target1": target1 if target1 > 0 else None,
        "mid_long_target2": target2 if target2 > 0 else None,
        "mid_long_allocation": "第一批40%｜第二批30%｜趨勢確認後30%",
        "mid_long_exit_rule": "跌破中長線風控價且兩日未收復則減碼；基本面轉差、重大負面事件或月線轉空時重新評估",
        "mid_long_reason": reason,
        "mid_long_data_quality": f"{available}/{long_total}",
        "mid_long_confidence": long_confidence,
    }


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

    # Compare each symbol with its own market on the same session.  Absolute
    # volume alone makes an entire low-participation US session look weak.
    market_volume_medians: dict[str, float] = {}
    for market_name in {str(r.get("market") or "").upper() for r in rows}:
        paces = [
            _finite(r.get("volume_pace"))
            for r in rows
            if str(r.get("market") or "").upper() == market_name
            and _finite(r.get("volume_pace")) > 0
        ]
        market_volume_medians[market_name] = float(np.median(paces)) if paces else 1.0

    for row in rows:
        market_name = str(row.get("market") or "").upper()
        market_volume_median = max(market_volume_medians.get(market_name, 1.0), 0.10)
        market_relative_volume = _finite(row.get("volume_pace"), 1.0) / market_volume_median
        row["market_volume_median"] = round(market_volume_median, 2)
        row["market_relative_volume"] = round(market_relative_volume, 2)

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

        # Blend absolute pace, same-market relative participation and attack
        # volume.  A broad market-wide quiet session is no longer treated as
        # symbol-specific volume failure.
        absolute_volume = _clamp(50 + (row["volume_pace"] - 0.75) * 30)
        relative_volume = _clamp(50 + (market_relative_volume - 1.0) * 35)
        volume_score = _clamp(
            absolute_volume * 0.40
            + relative_volume * 0.40
            + _clamp(50 + row["attack_volume"] * 0.8) * 0.20
        )
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

        news_score = _clamp(100 - max(_finite(row.get("news_penalty")), 0.0) * 6)
        base_total, overall_available, overall_total, overall_confidence = _available_weighted_score([
            (_clamp(technical), 20, all(_finite(row.get(k)) > 0 for k in ("price", "ma5", "ma10", "ma20"))),
            (volume_score, 15, _finite(row.get("avg_volume20")) > 0),
            (institution_score, 15, bool(row.get("institution_available") or row.get("credit_available"))),
            (quality_score, 15, bool(row.get("financial_quality_available"))),
            (growth_score, 15, bool(growth_parts)),
            (valuation_score, 10, bool(valuation_parts)),
            (news_score, 10, bool(row.get("news_data_available"))),
        ])
        base_total += macro_adjustment
        preliminary_signal = "🟢" if base_total >= 70 else "🟡" if base_total >= 58 else "🔴"
        performance_adjustment, performance_samples, performance_horizon = (
            _performance_adjustment(performance, preliminary_signal)
        )
        # Only verified, recent negative news can reduce the score. A failed
        # news request, a single-source report, or an unverified rumor is neutral.
        news_penalty = _clamp(_finite(row.get("news_penalty")), 0.0, 15.0)
        total = _clamp(base_total + performance_adjustment - news_penalty)
        row.update({
            "news_penalty": round(news_penalty, 1),
            "technical_score": round(_clamp(technical), 1),
            "volume_score": round(volume_score, 1),
            "institution_score": round(institution_score, 1),
            "credit_score": round(credit_score, 1),
            "valuation_score": round(valuation_score, 1),
            "growth_score": round(growth_score, 1),
            "fundamental_score": round(fundamental_score, 1),
            "financial_quality_score": round(quality_score, 1),
            "group_score": round(group_score, 1),
            "position_score": round(_clamp(position_score), 1),
            "overall_data_quality": f"{overall_available}/{overall_total}",
            "overall_confidence": overall_confidence,
            "macro_score": round(macro_score, 1),
            "macro_adjustment": round(macro_adjustment, 1),
            "macro_affects_score": macro_active,
            "performance_adjustment": round(performance_adjustment, 2),
            "performance_samples": performance_samples,
            "performance_horizon": performance_horizon,
            "score": round(total, 1),
            "theme_change_pct": round(theme_change[str(row["theme"])], 2),
        })
        row.update(_entry_plan(
            row,
            total,
            technical,
            volume_score,
            institution_score,
            group_score,
            position_score,
        ))
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
        elif (
            row["volume_pace"] < 0.65
            and market_relative_volume < 0.80
            and row["attack_volume"] <= 0
        ):
            row["risk"] = "量能明顯弱於同市場"
        else:
            row["risk"] = "一般波動"

        trend_ready = row["price"] >= row["ma10"] >= row["ma20"]
        not_extended = row["ma20_distance_pct"] < 10
        kline_ready = _finite(row.get("kline_score"), 50) >= 45 and not row.get("breakdown20")
        fundamental_ready = not row.get("fundamental_available") or fundamental_score >= 40
        quality_ready = not row.get("financial_quality_available") or quality_score >= 38
        if row["entry_score"] >= 70 and row["attack_volume"] > 0 and trend_ready and not_extended and kline_ready and fundamental_ready and quality_ready:
            row["action"] = "🟢 可分批，等回測買進區"
        elif row["entry_score"] >= 58:
            row["action"] = "🟡 觀察，等價格進入買進區"
        else:
            row["action"] = "🔴 暫不買"

        row.update(_positioning_radar(row))
        row.update(_next_day_scenario(row))
        row.update(_short_term_plan(row))
        row.update(_mid_long_term_plan(row))

    ranked = sorted(
        rows,
        key=lambda r: (r["entry_score"], r["score"], r["volume_pace"], r["change_pct"]),
        reverse=True,
    )
    for rank, row in enumerate(ranked, 1):
        row["rank"] = rank
        row["overall_rank"] = rank
    for rank, row in enumerate(sorted(rows, key=lambda r: r.get("short_term_score", 0), reverse=True), 1):
        row["short_term_rank"] = rank
    for rank, row in enumerate(sorted(rows, key=lambda r: r.get("mid_long_score", 0), reverse=True), 1):
        row["mid_long_rank"] = rank
    return ranked
