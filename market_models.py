"""Market-specific data contracts and transparent coverage scoring."""

from __future__ import annotations

from datetime import date
from typing import Any


TW_ONLY_PREFIXES = (
    "foreign_", "trust_", "dealer_", "institution_", "margin_", "sbl_",
    "short_", "broker_", "top_brokers_",
)
US_ONLY_PREFIXES = ("us_short_", "us_live_", "us_option_", "us_market_", "extended_")


def _date_value(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value or ""))
    except ValueError:
        return None


def validate_taiwan_data(row: dict[str, Any]) -> dict[str, Any]:
    """Validate Taiwan source dates and units before any score is calculated.

    Invalid data remains visible for diagnosis, but its availability flag is
    disabled so it cannot silently improve or damage a ranking.
    """
    if str(row.get("market") or "").upper() != "TW":
        return row

    issues: list[str] = []
    session = _date_value(row.get("tw_official_session_date"))
    price_unit = str(row.get("tw_price_unit") or "")
    official_price = bool(row.get("tw_official_price_available"))
    if official_price and price_unit != "TWD/shares":
        row["tw_official_price_available"] = False
        official_price = False
        issues.append("官方價量單位不符")
    reference_session = session if official_price else None

    institution_date = _date_value(row.get("institution_date"))
    if row.get("institution_available"):
        if not reference_session:
            row["institution_available"] = False
            issues.append("法人資料缺官方交易日基準")
        elif str(row.get("institution_unit") or "") != "shares":
            row["institution_available"] = False
            issues.append("法人單位不是股")
        elif not institution_date:
            row["institution_available"] = False
            issues.append("法人資料缺日期")
        elif institution_date != reference_session:
            row["institution_available"] = False
            issues.append("法人資料交易日不一致")

    credit_date = _date_value(row.get("credit_date"))
    if row.get("credit_available"):
        if not reference_session:
            row["credit_available"] = False
            issues.append("融資券資料缺官方交易日基準")
        elif str(row.get("credit_unit") or "") != "lots":
            row["credit_available"] = False
            issues.append("融資券單位不是張")
        elif not credit_date:
            row["credit_available"] = False
            issues.append("融資券資料缺日期")
        elif credit_date != reference_session:
            row["credit_available"] = False
            issues.append("融資券資料交易日不一致")

    row["tw_data_validation_issues"] = issues
    row["tw_data_valid"] = not issues
    row["tw_official_session_available"] = bool(reference_session)
    return row


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
        row["market_model_version"] = (
            "TW-ETF-V4" if "ETF" in str(row.get("type") or "").upper()
            else "TW-STOCK-V4"
        )
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
    if market == "TW":
        checks = [
            ("日線價量", bool(row.get("price") and row.get("avg_volume20")), 20),
            ("官方收盤", bool(row.get("tw_official_session_available")), 10),
            ("盤中量價", bool(row.get("intraday_available")), 10),
            ("新聞", bool(row.get("news_data_available")), 10),
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
