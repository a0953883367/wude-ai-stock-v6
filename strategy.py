"""Transparent scoring model for the Wude Taiwan-stock candidate pool."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from config import TAIPEI


NEW_YORK = ZoneInfo("America/New_York")


def market_session_fraction(
    market: str = "TW", now: datetime | None = None
) -> float:
    """Elapsed regular-session fraction for TW or US, including US DST."""
    market = str(market or "TW").upper()
    timezone = TAIPEI if market == "TW" else NEW_YORK
    now = (now or datetime.now(timezone)).astimezone(timezone)
    minute = now.hour * 60 + now.minute
    if market == "TW":
        start, duration = 9 * 60, 270
    else:
        start, duration = 9 * 60 + 30, 390
    return min(1.0, max(0.0, (minute - start) / duration))


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
    frame: pd.DataFrame | None, avg_volume: float, market: str = "TW"
) -> tuple[float, float, float | None, float | None, float | None]:
    if frame is None or frame.empty:
        return 1.0, 0.0, None, None, None
    volume = frame.get("volume", pd.Series(dtype=float)).fillna(0)
    close = frame.get("close", pd.Series(dtype=float))
    total = float(volume.sum())
    bar_time = None
    if isinstance(frame.index, pd.DatetimeIndex) and len(frame.index):
        stamp = pd.Timestamp(frame.index[-1])
        if stamp.tzinfo is None:
            timezone = TAIPEI if str(market).upper() == "TW" else NEW_YORK
            stamp = stamp.tz_localize(timezone)
        bar_time = stamp.to_pydatetime()
    elapsed = market_session_fraction(market, bar_time)
    expected = max(avg_volume * elapsed, avg_volume if elapsed <= 0 else 1.0)
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
    market = str(item.get("market") or ("TW" if str(item.get("symbol", "")).upper().endswith(".TW") else "US")).upper()
    pace, attack, live, attack15, attack30 = _intraday_metrics(intraday, avg20, market)
    if intraday is not None and not intraday.empty and completed_volume is not volume:
        candle["daily_volume_ratio"] = round(pace, 2)
        daily_change = (live or _finite(close.iloc[-1])) - prev
        if pace >= 1.3 and daily_change > 0:
            candle["volume_price_pattern"] = "價漲量增"
        elif pace >= 1.3 and daily_change < 0:
            candle["volume_price_pattern"] = "價跌量增"
        elif pace <= 0.8 and daily_change >= 0:
            candle["volume_price_pattern"] = "價穩量縮"
        elif pace <= 0.8:
            candle["volume_price_pattern"] = "量縮整理"
        else:
            candle["volume_price_pattern"] = "量價中性"
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
        if row.get("us_live_data_available"):
            available_groups += 1
            imbalance = _finite(row.get("us_live_quote_imbalance_pct"))
            vwap_distance = _finite(row.get("us_live_vwap_distance_pct"))
            if imbalance >= 20 and vwap_distance >= 0:
                score += 8
                evidence.append(f"SIP報價委買量差 {imbalance:+.1f}%，且價格位於VWAP上方")
            elif imbalance <= -20 and vwap_distance <= 0:
                score -= 8
                evidence.append(f"SIP報價委買量差 {imbalance:+.1f}%，且價格位於VWAP下方")
            else:
                evidence.append(f"SIP報價委買量差 {imbalance:+.1f}%，未達明顯失衡")
        if row.get("us_option_data_available"):
            available_groups += 1
            option_safety = _finite(row.get("us_option_safety_score"), 50.0)
            score += _clamp((option_safety - 50.0) * .20, -6, 4)
            evidence.append(
                f"OPRA近月價平選擇權IV { _finite(row.get('us_option_iv_pct')):.1f}%"
                f"、風險安全分 {option_safety:.1f}"
            )
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
        if row.get("extended_hours_available"):
            session = str(row.get("extended_session") or "盤前／盤後")
            gap = _finite(row.get("extended_change_pct"))
            flow_text = f"{session}相對正式收盤 {gap:+.2f}%；美股不套用台股三大法人，開盤後再用量價確認"
        else:
            flow_text = "美股不套用台股三大法人；以量價與開盤攻擊量判讀"
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


def _market_outlook(row: dict[str, Any]) -> dict[str, Any]:
    """Estimate a 1-5 session direction from available, market-aware evidence."""
    bias = 0.0
    reasons: list[tuple[float, str]] = []
    available = 0
    total = 5
    price = _finite(row.get("price"))
    ma5 = _finite(row.get("ma5"))
    ma10 = _finite(row.get("ma10"))
    ma20 = _finite(row.get("ma20"))
    ma60 = _finite(row.get("ma60"))
    if all(value > 0 for value in (price, ma5, ma10, ma20)):
        available += 1
        if price >= ma5 >= ma10 >= ma20:
            bias += 28
            reasons.append((28, "現價與5／10／20日線呈多頭排列"))
        elif price >= ma10 >= ma20:
            bias += 18
            reasons.append((18, "現價站上10／20日線，趨勢偏多"))
        elif price < ma5 < ma10 < ma20:
            bias -= 28
            reasons.append((-28, "現價與5／10／20日線呈空頭排列"))
        elif price < ma20:
            bias -= 18
            reasons.append((-18, "現價跌破20日線，趨勢偏弱"))
        else:
            reasons.append((0, "均線交錯，趨勢尚未形成"))
        if ma60 > 0:
            bias += 5 if ma20 >= ma60 else -5

    rsi = _finite(row.get("rsi"), -1)
    if rsi >= 0:
        available += 1
        if 55 <= rsi <= 68:
            bias += 8
            reasons.append((8, f"RSI {rsi:.1f}，動能偏強且未過熱"))
        elif 32 <= rsi < 45:
            bias -= 8
            reasons.append((-8, f"RSI {rsi:.1f}，動能偏弱"))
        elif rsi > 72:
            reasons.append((0, f"RSI {rsi:.1f}，偏熱不追價"))
        elif rsi < 28:
            reasons.append((0, f"RSI {rsi:.1f}，超賣但尚待止穩"))
        if row.get("breakout20"):
            bias += 10
            reasons.append((10, "價格突破20日高點"))
        if row.get("breakdown20"):
            bias -= 12
            reasons.append((-12, "價格跌破20日低點"))

    volume_available = _finite(row.get("avg_volume20")) > 0 or row.get("intraday_available")
    if volume_available:
        available += 1
        volume_ratio = _finite(row.get("daily_volume_ratio"), _finite(row.get("volume_pace"), 1.0))
        change = _finite(row.get("change_pct"))
        attack = _finite(row.get("attack_volume"))
        if volume_ratio >= 1.2 and change > 0:
            bias += 10
            reasons.append((10, f"價漲量增，日量約均量 {volume_ratio:.2f} 倍"))
        elif volume_ratio >= 1.2 and change < 0:
            bias -= 10
            reasons.append((-10, f"價跌量增，賣壓量約均量 {volume_ratio:.2f} 倍"))
        elif volume_ratio < 0.7:
            reasons.append((0, "成交量偏低，方向訊號可信度降低"))
        if attack >= 10:
            bias += 6
        elif attack <= -10:
            bias -= 6

    market = str(row.get("market", "US")).upper()
    is_etf = "ETF" in str(row.get("type", "")).upper()
    if is_etf and row.get("etf_market_flow_score") is not None:
        flow = _finite(row.get("etf_market_flow_score"), 50)
        flow_available = True
        flow_name = "ETF資金流"
    elif market == "US":
        flow = _finite(row.get("market_flow_score"), 50)
        flow_available = bool(row.get("market_flow_available"))
        flow_name = "美股市場資金流"
    else:
        flow = _finite(row.get("institution_score"), 50)
        flow_available = bool(row.get("institution_available") or row.get("credit_available"))
        flow_name = "法人與信用籌碼"
    if flow_available:
        available += 1
        flow_bias = _clamp((flow - 50) * 0.35, -12, 12)
        bias += flow_bias
        reasons.append((flow_bias, f"{flow_name} {flow:.1f} 分"))

    if row.get("news_data_available"):
        available += 1
        penalty = _clamp(_finite(row.get("news_penalty")) * 1.5, 0, 12)
        bias -= penalty
        reasons.append((-penalty, "近期負面消息已納入風險" if penalty else "未發現已確認的重大負面消息"))

    bias = _clamp(bias, -100, 100)
    if available < 3:
        direction = "↔️ 震盪平盤"
    elif bias >= 18:
        direction = "📈 看漲"
    elif bias <= -18:
        direction = "📉 看跌"
    else:
        direction = "↔️ 震盪平盤"
    confidence = _clamp(35 + abs(bias) * 0.7 + available / total * 20, 35, 95)
    if available < 3:
        confidence = min(confidence, 55)
    if direction.endswith("看漲"):
        change_condition = "跌破20日線且放量賣壓轉強，改判震盪／看跌"
    elif direction.endswith("看跌"):
        change_condition = "站回20日線且量價、資金同步轉強，改判震盪／看漲"
    else:
        change_condition = "放量突破第一壓力轉看漲；放量跌破第一支撐轉看跌"
    ordered = sorted(reasons, key=lambda item: abs(item[0]), reverse=True)
    return {
        "outlook_direction": direction,
        "outlook_bias_score": round(bias, 1),
        "outlook_confidence": round(confidence, 1),
        "outlook_horizon": "未來1～5個交易日",
        "outlook_reasons": [text for _, text in ordered[:3]],
        "outlook_change_condition": change_condition,
        "outlook_data_quality": f"{available}/{total}",
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


def _market_flow_score(row: dict[str, Any]) -> tuple[float, bool, str]:
    """Return a disclosure-aware flow score for the candidate's own market."""
    market = str(row.get("market") or "").upper()
    if market == "TW":
        return (
            _finite(row.get("institution_score"), 50.0),
            bool(row.get("institution_available") or row.get("credit_available")),
            "台股法人／信用籌碼",
        )
    score = 50.0
    available = False
    relative_volume = _finite(row.get("market_relative_volume"), 1.0)
    change = _finite(row.get("change_pct"))
    attack = _finite(row.get("opening_attack_30m"), _finite(row.get("attack_volume")))
    if _finite(row.get("avg_volume20")) > 0:
        available = True
        score += _clamp((relative_volume - 1.0) * 18, -12, 12)
        if relative_volume >= 1.15:
            score += 5 if change >= 0 else -6
    if row.get("us_short_volume_available"):
        available = True
        short_ratio = _finite(row.get("us_short_volume_ratio_pct"), 45.0)
        score += _clamp((45.0 - short_ratio) * 0.45, -8, 6)
    if row.get("us_live_data_available"):
        available = True
        imbalance = _finite(row.get("us_live_quote_imbalance_pct"))
        vwap_distance = _finite(row.get("us_live_vwap_distance_pct"))
        score += _clamp(imbalance * .12, -6, 6)
        score += _clamp(vwap_distance * 1.5, -5, 5)
    if row.get("us_option_data_available"):
        available = True
        score += _clamp((_finite(row.get("us_option_safety_score"), 50.0) - 50.0) * .15, -5, 4)
    if row.get("intraday_available"):
        available = True
        score += _clamp(attack * 0.20, -8, 8)
    return round(_clamp(score), 1), available, "美股FINRA交易流／相對量能"


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
        if market == "US":
            profile = "美股ETF"
            first_near, first_far = 0.012, 0.025
            better_near, better_far = 0.032, 0.055
            atr_floor, stop_gap = 0.012, 0.010
        else:
            profile = "台灣ETF"
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
    premium = _finite(row.get("premium_discount_pct"))
    premium_available = row.get("premium_discount_pct") is not None
    premium_limit = max(1.5, _finite(row.get("premium_discount_60d_std_pct")) * 2)
    premium_blocked = bool(is_etf and premium_available and premium > premium_limit)
    if premium_blocked:
        entry_score = min(entry_score, 55.0)

    if premium_blocked:
        note = f"ETF溢價 {premium:.2f}% 高於 {premium_limit:.2f}% 門檻，先等市價貼近淨值、不追價"
    elif missing:
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
        "etf_premium_blocked": premium_blocked,
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


def _complete_price_plan(row: dict[str, Any]) -> dict[str, Any]:
    """Return a complete, sanity-checked price plan and its ranking quality.

    Display values may be derived from the current price and ATR when exchange
    history is incomplete, but derived plans are explicitly demoted.  This
    avoids both blank fields and fabricated high rankings.
    """
    price = max(_finite(row.get("price")), 0.01)
    atr = _finite(row.get("atr14"))
    tick = _price_tick(row, price)
    is_etf = _is_etf(row)
    market = str(row.get("market") or "").upper()
    us_high_vol = market == "US" and not is_etf and (atr / price if price else 0) >= 0.04
    fallback_floor = (
        0.016 if market == "US" and is_etf else
        0.035 if us_high_vol else
        0.022 if market == "US" else
        0.012 if is_etf else 0.02
    )
    fallback_atr = max(atr, price * fallback_floor, tick)

    buy_low = _finite(row.get("buy_zone_low"))
    buy_high = _finite(row.get("buy_zone_high"))
    better_low = _finite(row.get("better_buy_low"))
    better_high = _finite(row.get("better_buy_high"))
    stop = _finite(row.get("stop_price"))
    support1 = _finite(row.get("support1"))
    support2 = _finite(row.get("support2"))
    resistance1 = _finite(row.get("resistance1"))
    resistance2 = _finite(row.get("resistance2"))

    derived: list[str] = []
    max_first_pullback = (
        0.10 if market == "US" and is_etf else
        0.20 if us_high_vol else
        0.16 if market == "US" else
        0.08 if is_etf else 0.14
    )
    zones_ok = (
        0 < better_low <= better_high < buy_low <= buy_high < price
        and buy_low >= price * (1 - max_first_pullback)
    )
    if not zones_ok:
        derived.append("進場區")
        first_near = 0.015 if market == "US" and is_etf else 0.025 if us_high_vol else 0.018 if market == "US" else 0.010 if is_etf else 0.018
        base_first_far = 0.030 if market == "US" and is_etf else 0.055 if us_high_vol else 0.040 if market == "US" else 0.020 if is_etf else 0.035
        first_far = max(base_first_far, fallback_atr / price * 1.05)
        first_far = min(first_far, max_first_pullback)
        better_floor = 0.045 if market == "US" and is_etf else 0.075 if market == "US" else 0.030 if is_etf else 0.050
        better_near = min(max(first_far + 0.008, better_floor), 0.22)
        better_far = min(max(better_near + 0.015, fallback_atr / price * 2.0), 0.20)
        buy_low = _market_price(row, price * (1 - first_far))
        buy_high = _market_price(row, price * (1 - first_near))
        better_low = _market_price(row, price * (1 - better_far))
        better_high = _market_price(row, price * (1 - better_near))

    if not (0 < support1 <= price * 1.03):
        support1 = buy_low
        derived.append("第一支撐")
    if not (0 < support2 <= support1):
        support2 = min(better_low, support1 - tick)
        derived.append("第二支撐")
    if not (resistance1 > price):
        resistance1 = _market_price(row, price + fallback_atr)
        derived.append("第一壓力")
    if not (resistance2 > resistance1):
        resistance2 = _market_price(row, resistance1 + fallback_atr)
        derived.append("第二壓力")
    if not (0 < stop < better_low):
        stop = _market_price(row, better_low - max(fallback_atr * 0.75, tick))
        derived.append("風控價")

    required_inputs = {
        "20日線": _finite(row.get("ma20")) > 0,
        "60日線": _finite(row.get("ma60")) > 0,
        "ATR": atr > 0,
        "20日均量": _finite(row.get("avg_volume20")) > 0,
    }
    verified_count = sum(required_inputs.values())
    if not derived and verified_count == len(required_inputs):
        quality, factor = "完整", 1.0
    elif verified_count >= 3:
        quality, factor = "部分推估", 0.88
    else:
        quality, factor = "資料不足", 0.68

    return {
        "buy_zone_low": buy_low,
        "buy_zone_high": buy_high,
        "better_buy_low": better_low,
        "better_buy_high": better_high,
        "buy_price": _market_price(row, (buy_low + buy_high) / 2),
        "support1": _market_price(row, support1),
        "support2": _market_price(row, support2),
        "resistance1": _market_price(row, resistance1),
        "resistance2": _market_price(row, resistance2),
        "stop_price": stop,
        "price_plan_complete": True,
        "price_plan_quality": quality,
        "price_plan_rank_factor": factor,
        "price_plan_derived_fields": derived,
        "price_plan_note": (
            "價格資料完整，使用均線、ATR與近期支撐壓力計算"
            if quality == "完整" else
            f"{quality}：{('、'.join(derived) or '部分指標')}採保守推估，排名已下調"
        ),
        "price_plan_market_model": (
            "美股ETF模型" if market == "US" and is_etf else
            "美股高波動模型" if us_high_vol else
            "美股一般模型" if market == "US" else
            "台灣ETF模型" if is_etf else "台股個股模型"
        ),
    }



def _is_etf(row: dict[str, Any]) -> bool:
    return "ETF" in str(row.get("type") or "").upper()


def _etf_kind(row: dict[str, Any]) -> str:
    """Classify ETF rules without treating leveraged products as long-term funds."""
    name = str(row.get("name") or "").upper()
    symbol = str(row.get("symbol") or "").upper().split(".")[0]
    combined = f"{name} {str(row.get('type') or '').upper()}"
    leveraged_words = ("槓桿", "反向", "正2", "反1", "2X", "3X", "ULTRA", "INVERSE", "BEAR")
    if any(word in combined for word in leveraged_words):
        return "槓桿／反向ETF"
    if "主動" in combined or (
        str(row.get("market") or "").upper() == "TW"
        and symbol.endswith("A") and symbol[:-1].isdigit()
    ):
        return "主動ETF"
    return "被動ETF"


def _etf_score_bundle(
    row: dict[str, Any],
    technical: float,
    volume_score: float,
    news_score: float,
) -> dict[str, Any]:
    """Build verified ETF dimensions; unavailable data never receives a neutral 50."""
    returns = [
        _finite(row.get(key))
        for key in ("etf_return_3y_pct", "etf_return_5y_pct", "benchmark_excess_return_pct")
        if row.get(key) is not None
    ]
    portfolio_available = bool(returns or row.get("etf_portfolio_score") is not None)
    portfolio_score = (
        _finite(row.get("etf_portfolio_score"))
        if row.get("etf_portfolio_score") is not None
        else float(np.mean([_clamp(50 + value * 0.45) for value in returns]))
        if returns else 0.0
    )

    tracking_error = row.get("tracking_error_pct")
    tracking_difference = row.get("tracking_difference_pct")
    tracking_available = tracking_error is not None or tracking_difference is not None
    tracking_score = _clamp(
        100
        - abs(_finite(tracking_error)) * 18
        - abs(_finite(tracking_difference)) * 10
    ) if tracking_available else 0.0

    spread = row.get("bid_ask_spread_pct")
    aum = _finite(row.get("aum"))
    liquidity_parts = [volume_score] if _finite(row.get("avg_volume20")) > 0 else []
    if spread is not None:
        liquidity_parts.append(_clamp(100 - abs(_finite(spread)) * 45))
    if aum > 0:
        liquidity_parts.append(_clamp(35 + math.log10(max(aum, 1)) * 6))
    liquidity_score = float(np.mean(liquidity_parts)) if liquidity_parts else 0.0
    liquidity_available = bool(liquidity_parts)

    premium = row.get("premium_discount_pct")
    premium_available = premium is not None
    premium_score = _clamp(100 - abs(_finite(premium)) * 22) if premium_available else 0.0

    beta = row.get("beta_3y")
    drawdown = row.get("max_drawdown_pct")
    risk_parts: list[float] = []
    if beta is not None:
        risk_parts.append(_clamp(90 - abs(_finite(beta) - 1.0) * 35))
    if drawdown is not None:
        risk_parts.append(_clamp(100 - abs(_finite(drawdown)) * 2.0))
    risk_score = float(np.mean(risk_parts)) if risk_parts else 0.0
    risk_available = bool(risk_parts)

    expense = row.get("expense_ratio_pct")
    cost_parts: list[float] = []
    if expense is not None:
        cost_parts.append(_clamp(100 - max(_finite(expense), 0.0) * 45))
    if aum > 0:
        cost_parts.append(_clamp(35 + math.log10(max(aum, 1)) * 6))
    cost_score = float(np.mean(cost_parts)) if cost_parts else 0.0
    cost_available = bool(cost_parts)

    concentration = row.get("top10_concentration_pct")
    concentration_available = concentration is not None
    concentration_score = _clamp(100 - max(_finite(concentration) - 35, 0) * 1.3) if concentration_available else 0.0

    market = str(row.get("market") or "").upper()
    breadth = row.get("holdings_breadth_score")
    breadth_available = breadth is not None
    flow = row.get("fund_flow_score")
    flow_available = flow is not None
    flow_label = "基金資金流"
    if market == "TW" and row.get("institution_available"):
        participation = _finite(row.get("institution_5d")) / max(_finite(row.get("avg_volume20")) * 5, 1) * 100
        continuity = 0.0
        if all(_finite(row.get(key)) > 0 for key in ("institution_1d", "institution_3d", "institution_5d")):
            continuity = 8.0
        elif all(_finite(row.get(key)) < 0 for key in ("institution_1d", "institution_3d", "institution_5d")):
            continuity = -8.0
        institutional_flow = _clamp(50 + participation * 2 + continuity)
        flow = float(np.mean([_finite(flow), institutional_flow])) if flow_available else institutional_flow
        flow_available = True
        flow_label = "法人參與／基金資金流" if row.get("fund_flow_score") is not None else "法人參與"
    elif market == "US" and not flow_available and row.get("market_flow_available"):
        flow = _finite(row.get("market_flow_score"))
        flow_available = True
        flow_label = "美股市場相對資金流"

    fx_risk = row.get("fx_risk_score")
    event_score = (
        float(np.mean([news_score, _finite(fx_risk)]))
        if fx_risk is not None else news_score
    )
    event_available = bool(row.get("news_data_available") or fx_risk is not None)

    return {
        "etf_kind": _etf_kind(row),
        "market": market,
        "portfolio_score": round(_clamp(portfolio_score), 1),
        "portfolio_available": portfolio_available,
        "tracking_score": round(_clamp(tracking_score), 1),
        "tracking_available": tracking_available,
        "liquidity_score": round(_clamp(liquidity_score), 1),
        "liquidity_available": liquidity_available,
        "premium_score": round(_clamp(premium_score), 1),
        "premium_available": premium_available,
        "risk_score": round(_clamp(risk_score), 1),
        "risk_available": risk_available,
        "cost_score": round(_clamp(cost_score), 1),
        "cost_available": cost_available,
        "concentration_score": round(_clamp(concentration_score), 1),
        "concentration_available": concentration_available,
        "breadth_score": round(_clamp(_finite(breadth)), 1),
        "breadth_available": breadth_available,
        "flow_score": round(_clamp(_finite(flow)), 1),
        "flow_available": flow_available,
        "flow_label": flow_label,
        "event_score": round(_clamp(event_score), 1),
        "event_available": event_available,
        "technical_score": round(_clamp(technical), 1),
        "volume_score": round(_clamp(volume_score), 1),
    }


def _etf_overall_components(bundle: dict[str, Any]) -> list[tuple[float, float, bool]]:
    if bundle["market"] == "TW":
        return [
            (bundle["portfolio_score"], 20, bundle["portfolio_available"]),
            (bundle["technical_score"], 20, True),
            (bundle["liquidity_score"], 15, bundle["liquidity_available"]),
            (bundle["premium_score"], 15, bundle["premium_available"]),
            (bundle["tracking_score"], 10, bundle["tracking_available"]),
            (bundle["flow_score"], 10, bundle["flow_available"]),
            (bundle["breadth_score"], 5, bundle["breadth_available"]),
            (bundle["event_score"], 5, bundle["event_available"]),
        ]
    return [
        (bundle["portfolio_score"], 25, bundle["portfolio_available"]),
        (bundle["technical_score"], 15, True),
        (bundle["tracking_score"], 15, bundle["tracking_available"]),
        (bundle["liquidity_score"], 15, bundle["liquidity_available"]),
        (bundle["premium_score"], 5, bundle["premium_available"]),
        (float(np.mean([
            score for score, ok in (
                (bundle["risk_score"], bundle["risk_available"]),
                (bundle["concentration_score"], bundle["concentration_available"]),
            ) if ok
        ])) if bundle["risk_available"] or bundle["concentration_available"] else 0.0,
         10, bundle["risk_available"] or bundle["concentration_available"]),
        (bundle["cost_score"], 10, bundle["cost_available"]),
        (bundle["event_score"], 5, bundle["event_available"]),
    ]


def _etf_short_components(
    bundle: dict[str, Any], structure_score: float
) -> list[tuple[float, float, bool]]:
    if bundle["market"] == "TW":
        return [
            (bundle["technical_score"], 25, True),
            (bundle["volume_score"], 25, bundle["liquidity_available"]),
            (bundle["premium_score"], 15, bundle["premium_available"]),
            (bundle["flow_score"], 15, bundle["flow_available"]),
            (structure_score, 15, True),
            (bundle["event_score"], 5, bundle["event_available"]),
        ]
    return [
        (bundle["technical_score"], 25, True),
        (bundle["volume_score"], 20, bundle["liquidity_available"]),
        (bundle["breadth_score"], 10, bundle["breadth_available"]),
        (bundle["premium_score"], 10, bundle["premium_available"]),
        (bundle["flow_score"], 15, bundle["flow_available"]),
        (structure_score, 15, True),
        (bundle["event_score"], 5, bundle["event_available"]),
    ]


def _etf_long_components(bundle: dict[str, Any], row: dict[str, Any]) -> list[tuple[float, float, bool]]:
    returns = [
        _finite(row.get(key))
        for key in ("etf_return_3y_pct", "etf_return_5y_pct")
        if row.get(key) is not None
    ]
    return_score = float(np.mean([_clamp(50 + value * 0.45) for value in returns])) if returns else 0.0
    tracking_cost_scores = [
        score for score, ok in (
            (bundle["tracking_score"], bundle["tracking_available"]),
            (bundle["cost_score"], bundle["cost_available"]),
        ) if ok
    ]
    tracking_cost = float(np.mean(tracking_cost_scores)) if tracking_cost_scores else 0.0
    if bundle["market"] == "TW":
        return [
            (bundle["portfolio_score"], 25, bundle["portfolio_available"]),
            (return_score, 15, bool(returns)),
            (tracking_cost, 10, bool(tracking_cost_scores)),
            (bundle["flow_score"], 15, bundle["flow_available"]),
            (bundle["risk_score"], 10, bundle["risk_available"]),
            (bundle["concentration_score"], 10, bundle["concentration_available"]),
            (bundle["liquidity_score"], 10, bundle["liquidity_available"]),
            (bundle["event_score"], 5, bundle["event_available"]),
        ]
    return [
        (bundle["portfolio_score"], 25, bundle["portfolio_available"]),
        (return_score, 20, bool(returns)),
        (bundle["risk_score"], 15, bundle["risk_available"]),
        (tracking_cost, 20, bool(tracking_cost_scores)),
        (bundle["concentration_score"], 10, bundle["concentration_available"]),
        (bundle["liquidity_score"], 5, bundle["liquidity_available"]),
        (bundle["event_score"], 5, bundle["event_available"]),
    ]


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
    if is_etf:
        etf_bundle = _etf_score_bundle(row, technical, volume_score, news_score)
        score, short_available, short_total, short_confidence = _available_weighted_score(
            _etf_short_components(etf_bundle, structure_score)
        )
    else:
        score, short_available, short_total, short_confidence = _available_weighted_score([
            (volume_score, 25, _finite(row.get("avg_volume20")) > 0),
            (technical, 20, technical_ok),
            (positioning, 20, bool(row.get("institution_available") or row.get("credit_available") or row.get("us_short_volume_available") or row.get("intraday_available"))),
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
    tick = _price_tick(row, max(price, 1.0))
    anchors = [v for v in (entry_low, support1) if v > 0]
    anchor = min(anchors) if anchors else 0.0
    structural = anchor-atr*.35 if anchor and atr else price*(1-max_loss)
    loss_floor = entry_high*(1-max_loss) if entry_high else price*(1-max_loss)
    stop = _market_price(row, max(structural, loss_floor))
    if entry_low and stop >= entry_low:
        stop = _market_price(row, entry_low-max(atr*.35, tick))
    risk = max(entry_high-stop, tick) if entry_high else tick
    target1 = _market_price(row, max(resistance1, entry_high+risk*1.5))
    # Exchange tick rounding must not turn a nominal 1.5 reward/risk plan into
    # 1.49. Move the first target up by valid ticks until the displayed plan
    # still meets its advertised minimum.
    if entry_high and risk > 0:
        while (target1-entry_high)/risk < 1.5:
            target1 = _market_price(row, target1+tick)
    target2 = _market_price(row, max(resistance2, entry_high+risk*2.3, target1+tick))
    rr = round((target1-entry_high)/risk, 2) if risk > 0 and entry_high else 0.0

    market_relative_volume = _finite(row.get("market_relative_volume"), 1.0)
    if is_etf and market == "TW":
        volume_ok = volume_pace >= .70 or market_relative_volume >= .90 or attack > 0
    elif is_etf and market == "US":
        volume_ok = market_relative_volume >= .90 or volume_pace >= .80 or attack > 0
    else:
        volume_ok = volume_pace >= .75 or market_relative_volume >= .90 or attack > 0
    premium = _finite(row.get("premium_discount_pct"))
    premium_available = row.get("premium_discount_pct") is not None
    premium_limit = max(1.5, _finite(row.get("premium_discount_60d_std_pct")) * 2)
    premium_ok = not is_etf or not premium_available or premium <= premium_limit
    eligible = bool(
        technical_ok and coverage_ok and entry_score >= 58 and score >= 60
        and price >= ma20*.99 and 40 <= rsi <= 72 and volume_ok and premium_ok
        and not row.get("breakdown20") and news_penalty < 8
        and price <= entry_high*(1.08 if is_etf else 1.10) and rr >= 1.5
    )
    if eligible:
        status = "🟢 等待觸發"
        trigger = ("回測進場區止穩，且前15～30分鐘攻擊量轉正才分批進場"
                   if price > entry_high else
                   "進場區不破支撐，出現量增紅K或突破前15分鐘高點才分批進場")
        reason = f"{setup}｜量能合格｜風報比 {rr:.2f}"
    elif not premium_ok:
        status, trigger = "🔴 淨值溢價過高", f"目前溢價 {premium:.2f}% 高於 {premium_limit:.2f}% 門檻，不追價"
        reason = "市價明顯高於基金淨值，先等溢價收斂"
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
    institution_available = bool(row.get("institution_available") or row.get("credit_available") or row.get("us_short_volume_available") or row.get("intraday_available"))
    news_available = bool(row.get("news_data_available"))
    technical_available = all(v > 0 for v in (price, ma20, ma60, atr))
    news_score = _clamp(100 - max(news_penalty, 0.0) * 6)
    if is_etf:
        etf_bundle = _etf_score_bundle(row, technical, _finite(row.get("volume_score")), news_score)
        long_score, available, long_total, long_confidence = _available_weighted_score(
            _etf_long_components(etf_bundle, row)
        )
    else:
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
    target2 = _market_price(row, max(target1+_price_tick(row, max(target1, 1.0)), price*1.28, batch1_high*1.32))

    trend_ok = price >= ma60*.90 if ma60 > 0 else False
    leveraged_etf = is_etf and _etf_kind(row) == "槓桿／反向ETF"
    eligible = bool(
        not leveraged_etf and data_ok and technical_available and ai_score >= 62 and long_score >= 62
        and trend_ok and news_penalty < 10 and batch1_low > 0 and batch1_high > 0
    )
    if leveraged_etf:
        status = "🔴 不列入一般中長線"
        reason = "槓桿／反向ETF有每日再平衡與路徑風險，不使用3～12個月一般布局模型"
    elif eligible:
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
        row["institution_score"] = institution_score
        market_flow_score, market_flow_available, market_flow_model = _market_flow_score(row)
        if market_name == "US":
            institution_score = market_flow_score
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
        if _is_etf(row):
            etf_bundle = _etf_score_bundle(row, technical, volume_score, news_score)
            row.update({
                "etf_kind": etf_bundle["etf_kind"],
                "etf_portfolio_score": etf_bundle["portfolio_score"] if etf_bundle["portfolio_available"] else None,
                "etf_tracking_score": etf_bundle["tracking_score"] if etf_bundle["tracking_available"] else None,
                "etf_liquidity_score": etf_bundle["liquidity_score"] if etf_bundle["liquidity_available"] else None,
                "etf_premium_score": etf_bundle["premium_score"] if etf_bundle["premium_available"] else None,
                "etf_risk_score": etf_bundle["risk_score"] if etf_bundle["risk_available"] else None,
                "etf_cost_score": etf_bundle["cost_score"] if etf_bundle["cost_available"] else None,
                "etf_long_term_eligible": etf_bundle["etf_kind"] != "槓桿／反向ETF",
                "score_model": "美股ETF獨立模型" if market_name == "US" else "台灣ETF獨立模型",
                "etf_market_flow_score": etf_bundle["flow_score"] if etf_bundle["flow_available"] else None,
                "etf_market_flow_label": etf_bundle["flow_label"],
            })
            base_total, overall_available, overall_total, overall_confidence = _available_weighted_score(
                _etf_overall_components(etf_bundle)
            )
        else:
            base_total, overall_available, overall_total, overall_confidence = _available_weighted_score([
                (_clamp(technical), 20, all(_finite(row.get(k)) > 0 for k in ("price", "ma5", "ma10", "ma20"))),
                (volume_score, 15, _finite(row.get("avg_volume20")) > 0),
                (institution_score, 15, market_flow_available if market_name == "US" else bool(row.get("institution_available") or row.get("credit_available"))),
                (quality_score, 15, bool(row.get("financial_quality_available"))),
                (growth_score, 15, bool(growth_parts)),
                (valuation_score, 10, bool(valuation_parts)),
                (news_score, 10, bool(row.get("news_data_available"))),
            ])
            row["score_model"] = "美股個股獨立模型" if market_name == "US" else "台股個股獨立模型"
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
            "market_flow_score": round(market_flow_score, 1),
            "market_flow_available": market_flow_available,
            "market_flow_model": market_flow_model,
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
        # Every displayed candidate receives a complete price plan.  When any
        # level must be derived because source data is incomplete or
        # inconsistent, its ranking confidence is reduced below.
        row.update(_complete_price_plan(row))
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
        row.update(_market_outlook(row))
        row.update(_short_term_plan(row))
        row.update(_mid_long_term_plan(row))

        plan_factor = _finite(row.get("price_plan_rank_factor"), 0.68)
        coverage_factor = 0.75 + 0.25 * (_finite(row.get("overall_confidence")) / 100)
        row["overall_ranking_score"] = round(row["score"] * plan_factor * coverage_factor, 1)
        row["short_term_ranking_score"] = round(
            _finite(row.get("short_term_score"))
            * plan_factor
            * (1.0 if row.get("short_term_eligible") else 0.82),
            1,
        )
        row["mid_long_ranking_score"] = round(
            _finite(row.get("mid_long_score"))
            * plan_factor
            * (1.0 if row.get("mid_long_eligible") else 0.82),
            1,
        )

    ranked = sorted(
        rows,
        # Overall TOP20 represents the sum of verified dimensions.  Entry
        # timing is only a tie-breaker and no longer controls the main list.
        key=lambda r: (r["overall_ranking_score"], r["score"], r["entry_score"]),
        reverse=True,
    )
    for rank, row in enumerate(ranked, 1):
        row["rank"] = rank
        row["overall_rank"] = rank
    for rank, row in enumerate(sorted(rows, key=lambda r: r.get("short_term_ranking_score", 0), reverse=True), 1):
        row["short_term_rank"] = rank
    for rank, row in enumerate(sorted(rows, key=lambda r: r.get("mid_long_ranking_score", 0), reverse=True), 1):
        row["mid_long_rank"] = rank
    return ranked
