"""Independent Treasury stress monitor for equity-flight confirmation.

This module is deliberately shadow-only.  It reports evidence but cannot
change V6 scores, rankings, weights or orders.
"""

from __future__ import annotations

from typing import Any


TREASURY_SIP_SYMBOLS = {
    "美國長債 TLT": "TLT",
    "美國中期債 IEF": "IEF",
    "美國短債 SHY": "SHY",
}


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def attach_treasury_market(
    market: dict[str, dict[str, Any]],
    sip_rows: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Add Treasury ETF observations without placing them in the stock pool."""
    output = dict(market or {})
    for name, symbol in TREASURY_SIP_SYMBOLS.items():
        row = sip_rows.get(symbol) or {}
        price = _number(row.get("us_live_price"))
        change = _number(row.get("us_live_change_pct"))
        if price is None:
            continue
        output[name] = {
            "symbol": symbol,
            "price": price,
            "change_pct": change,
            "previous_close": _number(row.get("us_live_previous_close")),
            "source": row.get("us_live_source") or "Alpaca SIP / Railway relay",
            "fetched_at": row.get("us_live_fetched_at"),
            "shadow_only": True,
        }
    return output


def _change(market: dict[str, dict[str, Any]], name: str) -> float | None:
    return _number((market.get(name) or {}).get("change_pct"))


def _price(market: dict[str, dict[str, Any]], name: str) -> float | None:
    return _number((market.get(name) or {}).get("price"))


def _yield_bps_change(market: dict[str, dict[str, Any]]) -> float | None:
    row = market.get("美國10年期公債殖利率") or {}
    current = _number(row.get("price"))
    change_pct = _number(row.get("change_pct"))
    if current is None or change_pct is None or 1 + change_pct / 100 <= 0:
        return None
    previous = current / (1 + change_pct / 100)
    return round((current - previous) * 100, 1)


def evaluate_treasury_stress(market: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Classify bond/equity stress conservatively using corroborating signals."""
    tlt = _change(market, "美國長債 TLT")
    ief = _change(market, "美國中期債 IEF")
    shy = _change(market, "美國短債 SHY")
    ten_year = _price(market, "美國10年期公債殖利率")
    yield_bps = _yield_bps_change(market)
    nasdaq = _change(market, "Nasdaq")
    sp500 = _change(market, "S&P 500")
    vix = _price(market, "VIX")
    vix_change = _change(market, "VIX")

    available = sum(value is not None for value in (tlt, ief, shy, yield_bps))
    selloff = (
        (tlt is not None and tlt <= -1.0)
        or (ief is not None and ief <= -0.5)
        or (yield_bps is not None and yield_bps >= 8.0)
    )
    severe = (
        (tlt is not None and tlt <= -2.0)
        or (ief is not None and ief <= -1.0)
        or (yield_bps is not None and yield_bps >= 15.0)
    )
    equity_confirmation = (
        (nasdaq is not None and nasdaq <= -1.0)
        or (sp500 is not None and sp500 <= -0.8)
    )
    volatility_confirmation = (
        (vix is not None and vix >= 25.0)
        or (vix_change is not None and vix_change >= 10.0)
    )
    safe_haven_rally = (
        equity_confirmation
        and ((tlt is not None and tlt >= 0.5) or (ief is not None and ief >= 0.25))
    )

    reasons: list[str] = []
    if selloff:
        reasons.append("美債價格下跌或10年債殖利率明顯上升")
    if equity_confirmation:
        reasons.append("美股指數同步轉弱")
    if volatility_confirmation:
        reasons.append("VIX同步升高")
    if safe_haven_rally:
        reasons.append("股跌債漲較像避險資金流入美債，不是債券拋售帶動")

    if available == 0:
        level, light, label = "waiting", "⚪", "等待美債可靠行情"
        summary = "尚無足夠的 TLT／IEF／SHY 或殖利率變動資料，不做推論。"
    elif selloff and equity_confirmation and (severe or volatility_confirmation):
        level, light, label = "critical", "🔴", "股票資金逃離風險升高"
        summary = "債券、股票與波動訊號共同轉差；降低追價並加強部位風險檢查。"
    elif selloff:
        level, light, label = "watch", "🟡", "美債承壓，尚未確認股票逃離"
        summary = "單一債券訊號不足以判定股票逃離，等待股指或 VIX 共同確認。"
    elif safe_haven_rally:
        level, light, label = "watch", "🟡", "股票轉弱，美債呈避險上漲"
        summary = "目前較像資金流向美債避險，仍需監控股市壓力。"
    else:
        level, light, label = "normal", "🟢", "美債波動未形成股票逃離訊號"
        summary = "未出現債券拋售與股市／VIX共同惡化。"

    return {
        "mode": "shadow_only",
        "level": level,
        "light": light,
        "label": label,
        "summary": summary,
        "reasons": reasons,
        "data_available_count": available,
        "tlt_change_pct": tlt,
        "ief_change_pct": ief,
        "shy_change_pct": shy,
        "us10y_yield_pct": ten_year,
        "us10y_yield_change_bps": yield_bps,
        "equity_confirmation": equity_confirmation,
        "volatility_confirmation": volatility_confirmation,
        "safe_haven_rally": safe_haven_rally,
        "affects_formal_v6": False,
        "affects_ranking": False,
        "affects_weights": False,
        "can_place_orders": False,
    }
