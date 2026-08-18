"""Market-specific data contracts and transparent coverage scoring."""

from __future__ import annotations

from typing import Any


TW_ONLY_PREFIXES = (
    "foreign_", "trust_", "dealer_", "institution_", "margin_", "sbl_",
    "short_", "broker_", "top_brokers_",
)
US_ONLY_PREFIXES = ("us_short_", "us_live_", "us_option_", "us_market_", "extended_")


def enforce_market_contract(row: dict[str, Any]) -> dict[str, Any]:
    """Remove fields that belong to the other market before scoring.

    The function mutates and returns ``row`` so it can be used cheaply for the
    full universe. Availability flags are also reset to prevent stale cached
    values from silently entering a different market model.
    """
    market = str(row.get("market") or "").upper()
    if market not in {"TW", "US"}:
        raise ValueError(f"unsupported market: {market or 'missing'}")
    forbidden = US_ONLY_PREFIXES if market == "TW" else TW_ONLY_PREFIXES
    removed = [key for key in list(row) if key.startswith(forbidden)]
    for key in removed:
        row.pop(key, None)
    if market == "TW":
        row["us_short_volume_available"] = False
        row["extended_hours_available"] = False
        row["market_model_version"] = "TW-V3"
    else:
        row["institution_available"] = False
        row["credit_available"] = False
        row["broker_available"] = False
        row["market_model_version"] = "US-V3"
    row["market_contract_removed"] = sorted(removed)
    row["market_contract_valid"] = True
    return row


def assess_market_data_quality(row: dict[str, Any]) -> dict[str, Any]:
    """Return a market-aware coverage score; missing data never becomes 50."""
    market = str(row.get("market") or "").upper()
    common = [
        ("日線價量", bool(row.get("price") and row.get("avg_volume20")), 25),
        ("盤中量價", bool(row.get("intraday_available")), 15),
        ("新聞", bool(row.get("news_data_available")), 10),
    ]
    if market == "TW":
        checks = common + [
            ("三大法人", bool(row.get("institution_available")), 15),
            ("融資融券／借券", bool(row.get("credit_available")), 10),
            ("估值／營收", bool(row.get("fundamental_available")), 10),
            ("財務品質", bool(row.get("financial_quality_available")), 10),
            ("券商分點", bool(row.get("broker_available")), 5),
        ]
    elif market == "US":
        is_etf = "ETF" in str(row.get("type") or "").upper()
        checks = [
            ("日線價量", bool(row.get("price") and row.get("avg_volume20")), 20),
            ("盤中量價", bool(row.get("intraday_available")), 10),
            ("新聞", bool(row.get("news_data_available")), 10),
            ("SIP全市場行情", bool(row.get("us_live_data_available")), 15),
            ("盤前／盤後", bool(row.get("extended_hours_available")), 5),
            ("FINRA每日放空成交", bool(row.get("us_short_volume_available")), 10),
            ("ETF資料" if is_etf else "公司財務", bool(row.get("etf_metadata_available") if is_etf else row.get("us_company_data_available")), 15),
            ("OPRA選擇權", bool(row.get("us_option_data_available")), 5),
            ("美股總經環境", bool(row.get("macro_data_available", True)), 10),
        ]
    else:
        raise ValueError(f"unsupported market: {market or 'missing'}")
    score = sum(weight for _, available, weight in checks if available)
    available = [name for name, present, _ in checks if present]
    missing = [name for name, present, _ in checks if not present]
    label = "完整" if score >= 85 else "良好" if score >= 70 else "部分資料" if score >= 50 else "資料不足"
    return {
        "market_data_quality_score": score,
        "market_data_quality": label,
        "market_data_available": available,
        "market_data_missing": missing,
    }
