"""Independent confidence ranking for detected chart patterns.

Only ``chart_pattern_*`` keys are added to already-final rows. No formal V6
score, rank, model weight or order instruction is read back or overwritten.
"""

from __future__ import annotations

import math
from typing import Any


WEIGHTS = {
    "close": 25.0, "volume": 20.0, "retest": 15.0, "kd": 15.0,
    "rsi_macd": 10.0, "market_sector": 10.0, "history": 5.0,
}


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _bounded(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 1)


def _directional(value: float, direction: str) -> float:
    return value if direction == "bullish" else 100.0-value


def _latest_signal(report: dict[str, Any], row: dict[str, Any]) -> dict[str, Any] | None:
    candidates = [
        item for item in report.get("signals", [])
        if item.get("market") == str(row.get("market") or "").upper()
        and str(item.get("symbol") or "").upper() == str(row.get("symbol") or "").upper()
        and item.get("pattern") == row.get("chart_pattern_shadow_name")
    ]
    return max(candidates, key=lambda item: str(item.get("signal_date") or ""), default=None)


def _history_stat(report: dict[str, Any], row: dict[str, Any]) -> tuple[float | None, str]:
    match = next((
        item for item in report.get("pattern_statistics", [])
        if item.get("market") == str(row.get("market") or "").upper()
        and item.get("pattern") == row.get("chart_pattern_shadow_name")
    ), None)
    if not match:
        return None, "尚無歷史結算"
    horizons = match.get("horizons") or {}
    for horizon in ("5", "3", "1"):
        item = horizons.get(horizon) or {}
        samples = int(item.get("sample_count") or 0)
        rate = _number(item.get("hit_rate_pct"))
        if samples >= 5 and rate is not None:
            return rate, f"{horizon}日命中 {rate:.1f}%（{samples}筆）"
    total = max((int((horizons.get(h) or {}).get("sample_count") or 0) for h in ("1", "3", "5")), default=0)
    return None, f"歷史樣本累積中（最多{total}筆，滿5筆計分）"


def _component_scores(row: dict[str, Any], report: dict[str, Any]) -> list[dict[str, Any]]:
    direction = str(row.get("chart_pattern_shadow_direction") or "neutral")
    confirmed = str(row.get("chart_pattern_shadow_status") or "").startswith("收盤確認")
    geometry = _number(row.get("chart_pattern_shadow_confidence")) or 0.0
    close_score = geometry*.55+(100.0 if confirmed else 40.0)*.45

    ratio = _number(row.get("daily_volume_ratio"))
    if row.get("chart_pattern_shadow_volume_confirmed") is True:
        volume_score, volume_text = 100.0, "放量確認"
    elif ratio is None:
        volume_score, volume_text = None, "量能資料不足"
    elif ratio >= 1.3:
        volume_score, volume_text = 85.0, f"量比 {ratio:.2f}x，等待收盤方向"
    elif ratio >= 1.0:
        volume_score, volume_text = 60.0, f"量比 {ratio:.2f}x"
    else:
        volume_score, volume_text = 35.0, f"量比 {ratio:.2f}x，量能不足"

    signal = _latest_signal(report, row)
    next_check = (signal or {}).get("next_session_confirmation") or {}
    retest = next_check.get("retest_status")
    retest_map = {
        "held": (100.0, "隔日回踩／反抽守住"),
        "failed": (0.0, "隔日回踩／反抽失敗"),
        "not_retested": (70.0, "隔日方向延續，尚未回測"),
    }
    retest_score, retest_text = retest_map.get(retest, (None, "等待隔日回踩／反抽"))

    daily_k = _number(row.get("chart_pattern_daily_k"))
    daily_d = _number(row.get("chart_pattern_daily_d"))
    weekly_k = _number(row.get("chart_pattern_weekly_k"))
    weekly_d = _number(row.get("chart_pattern_weekly_d"))
    kd_score = None
    kd_text = "日／週KD資料累積中"
    if None not in (daily_k, daily_d, weekly_k, weekly_d):
        daily_ok = daily_k >= daily_d if direction == "bullish" else daily_k <= daily_d
        weekly_ok = weekly_k >= weekly_d if direction == "bullish" else weekly_k <= weekly_d
        kd_score = 100.0 if daily_ok and weekly_ok else 62.0 if daily_ok or weekly_ok else 20.0
        kd_text = f"日KD{'同向' if daily_ok else '背離'}／週KD{'同向' if weekly_ok else '背離'}"

    rsi = _number(row.get("rsi"))
    histogram = _number(row.get("chart_pattern_macd_histogram"))
    rsi_macd_score = None
    rsi_macd_text = "RSI／MACD資料不足"
    if rsi is not None and histogram is not None:
        rsi_ok = 50 <= rsi <= 70 if direction == "bullish" else 30 <= rsi <= 50
        macd_ok = histogram >= 0 if direction == "bullish" else histogram <= 0
        rsi_macd_score = 100.0 if rsi_ok and macd_ok else 60.0 if rsi_ok or macd_ok else 20.0
        rsi_macd_text = f"RSI {rsi:.1f}／MACD{'同向' if macd_ok else '背離'}"

    context_values = []
    macro = _number(row.get("macro_score"))
    group = _number(row.get("group_score"))
    if macro is not None:
        context_values.append(_directional(macro, direction))
    if group is not None:
        context_values.append(_directional(group, direction))
    context_score = sum(context_values)/len(context_values) if context_values else None
    context_text = "大盤／產業資料不足" if not context_values else "大盤與產業方向核對"

    history_score, history_text = _history_stat(report, row)
    return [
        {"key": "close", "label": "型態／收盤", "score": _bounded(close_score), "status": "收盤已確認" if confirmed else "等待收盤確認"},
        {"key": "volume", "label": "成交量", "score": None if volume_score is None else _bounded(volume_score), "status": volume_text},
        {"key": "retest", "label": "回踩／反抽", "score": retest_score, "status": retest_text},
        {"key": "kd", "label": "日KD×週KD", "score": kd_score, "status": kd_text},
        {"key": "rsi_macd", "label": "RSI＋MACD", "score": rsi_macd_score, "status": rsi_macd_text},
        {"key": "market_sector", "label": "大盤＋產業", "score": None if context_score is None else _bounded(context_score), "status": context_text},
        {"key": "history", "label": "1／3／5日驗證", "score": None if history_score is None else _bounded(history_score), "status": history_text},
    ]


def _score(components: list[dict[str, Any]]) -> tuple[float, float]:
    available = [item for item in components if _number(item.get("score")) is not None]
    available_weight = sum(WEIGHTS[item["key"]] for item in available)
    if not available or available_weight <= 0:
        return 0.0, 0.0
    raw = sum(float(item["score"])*WEIGHTS[item["key"]] for item in available)/available_weight
    coverage = available_weight/sum(WEIGHTS.values())
    return _bounded(raw*(.70+.30*coverage)), _bounded(coverage*100)


def _holding_score(row: dict[str, Any]) -> tuple[float | None, list[dict[str, Any]]]:
    direction = str(row.get("chart_pattern_shadow_direction") or "neutral")
    candidates = [
        ("法人／資金", row.get("market_flow_score") if row.get("market_flow_available") else row.get("institution_score"), 30.0),
        ("營收／基本面", row.get("fundamental_score") if row.get("fundamental_available") else None, 40.0),
        ("產業方向", row.get("group_score"), 30.0),
    ]
    parts = []
    for label, value, weight in candidates:
        number = _number(value)
        if number is None:
            continue
        parts.append({"label": label, "score": _bounded(_directional(number, direction)), "weight": weight})
    if not parts:
        return None, []
    total = sum(item["weight"] for item in parts)
    return _bounded(sum(item["score"]*item["weight"] for item in parts)/total), parts


def attach_chart_pattern_confidence(rows: list[dict[str, Any]], report: dict[str, Any]) -> None:
    """Add isolated pattern ranks in place; callers retain final formal order."""
    eligible = []
    for row in rows:
        pattern = str(row.get("chart_pattern_shadow_name") or "未偵測")
        direction = str(row.get("chart_pattern_shadow_direction") or "neutral")
        if pattern == "未偵測" or direction not in {"bullish", "bearish"}:
            continue
        components = _component_scores(row, report)
        score, coverage = _score(components)
        holding_score, holding_parts = _holding_score(row)
        row.update({
            "chart_pattern_rank_score": score,
            "chart_pattern_rank_coverage_pct": coverage,
            "chart_pattern_rank_components": components,
            "chart_pattern_rank_label": "高可信" if score >= 80 else "中可信" if score >= 65 else "低可信／等待",
            "chart_pattern_holding_1m_score": holding_score,
            "chart_pattern_holding_1m_components": holding_parts,
            "chart_pattern_rank_affects_formal": False,
        })
        eligible.append(row)

    def assign(items: list[dict[str, Any]], key: str) -> None:
        ordered = sorted(items, key=lambda item: (
            -float(item.get("chart_pattern_rank_score") or 0),
            str(item.get("symbol") or ""),
        ))
        for rank, item in enumerate(ordered, 1):
            item[key] = rank

    assign(eligible, "chart_pattern_rank_all")
    for market in {str(row.get("market") or "").upper() for row in eligible}:
        assign([row for row in eligible if str(row.get("market") or "").upper() == market], "chart_pattern_rank_market")
    for direction in ("bullish", "bearish"):
        assign([row for row in eligible if row.get("chart_pattern_shadow_direction") == direction], "chart_pattern_rank_direction")
