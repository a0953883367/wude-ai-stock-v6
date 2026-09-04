"""Point-in-time industry life-cycle evidence for the existing 60-day shadow.

This module does not create a model or fetch data.  It only normalizes fields
already present in a completed report and rejects evidence dated after the
signal-session close.
"""

from __future__ import annotations

from typing import Any


STAGES = {"萌芽期", "成長期", "成熟期", "再創新期", "資料不足"}


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _date(value: Any) -> str:
    text = str(value or "")[:10]
    return text if len(text) == 10 and text[4:5] == "-" and text[7:8] == "-" else ""


def _available_on(date: str, signal_date: str) -> bool:
    return bool(date and signal_date and date <= signal_date)


def _first(row: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _metric(
    row: dict[str, Any],
    *,
    kind: str,
    label: str,
    value_keys: tuple[str, ...],
    date_keys: tuple[str, ...],
    source_keys: tuple[str, ...],
    signal_date: str,
) -> dict[str, Any] | None:
    value = _number(_first(row, value_keys))
    evidence_date = _date(_first(row, date_keys))
    source = str(_first(row, source_keys) or "").strip()
    if value is None or not source or not _available_on(evidence_date, signal_date):
        return None
    return {
        "kind": kind,
        "label": label,
        "value_pct": round(value, 4),
        "date": evidence_date,
        "source": source[:160],
    }


def _evidence_items(row: dict[str, Any], signal_date: str) -> list[dict[str, Any]]:
    specs = (
        ("revenue_yoy", "營收年增", ("revenue_yoy_pct",),
         ("revenue_date", "financial_report_date"),
         ("revenue_source", "us_company_data_source", "financial_quality_source")),
        ("revenue_mom", "營收月增", ("revenue_mom_pct",),
         ("revenue_date", "financial_report_date"),
         ("revenue_source", "us_company_data_source", "financial_quality_source")),
        ("orders", "訂單／在手訂單成長", ("order_growth_pct", "orders_yoy_pct", "order_backlog_growth_pct"),
         ("order_date", "orders_date"), ("order_source", "orders_source")),
        ("capex", "資本支出成長", ("capex_growth_pct", "capital_expenditure_growth_pct"),
         ("capex_date", "financial_report_date"), ("capex_source", "financial_quality_source", "us_company_data_source")),
        ("capacity", "產能利用率變化", ("capacity_utilization_change_pct",),
         ("capacity_utilization_date", "capacity_date"), ("capacity_utilization_source", "capacity_source")),
        ("demand", "產業需求成長", ("industry_demand_growth_pct", "demand_growth_pct"),
         ("industry_demand_date", "demand_date"), ("industry_demand_source", "demand_source")),
    )
    result = []
    for kind, label, values, dates, sources in specs:
        item = _metric(
            row, kind=kind, label=label, value_keys=values,
            date_keys=dates, source_keys=sources, signal_date=signal_date,
        )
        if item:
            result.append(item)
    return result


def _stage(row: dict[str, Any], signal_date: str, items: list[dict[str, Any]]) -> tuple[str, str]:
    explicit = str(row.get("industry_lifecycle_stage") or "").strip()
    explicit_date = _date(row.get("industry_lifecycle_date"))
    explicit_source = str(row.get("industry_lifecycle_source") or "").strip()
    if (
        explicit in STAGES - {"資料不足"}
        and explicit_source
        and _available_on(explicit_date, signal_date)
    ):
        return explicit, "已取得具日期與來源的產業生命週期判斷"

    values = {item["kind"]: float(item["value_pct"]) for item in items}
    yoy = values.get("revenue_yoy")
    mom = values.get("revenue_mom")
    leading = [values[key] for key in ("orders", "capex", "capacity", "demand") if key in values]

    if yoy is not None and yoy >= 20:
        return "成長期", "營收年增達20%以上"
    if yoy is not None and yoy <= 5 and mom is not None and mom >= 10 and any(value >= 10 for value in leading):
        return "再創新期", "營收回升並有訂單、資本支出、產能或需求證據交叉支持"
    if yoy is None and any(value >= 25 for value in leading):
        return "萌芽期", "領先需求／訂單證據已增長，但尚無可對齊營收年增資料"
    if yoy is not None and -5 <= yoy < 20:
        return "成熟期", "營收年增介於-5%至20%"
    if len([value for value in leading if value >= 15]) >= 2:
        return "成長期", "至少兩項領先產業證據同步增長"
    return "資料不足", "現有可對齊證據不足以判斷產業生命週期"


def _risk(row: dict[str, Any], signal_date: str, items: list[dict[str, Any]]) -> str:
    risks: list[str] = []
    valuation_date = _date(_first(row, ("valuation_date", "financial_report_date")))
    valuation_source = str(_first(row, ("valuation_source", "us_company_data_source", "financial_quality_source")) or "")
    valuation_score = _number(row.get("valuation_score"))
    per = _number(row.get("per"))
    if valuation_source and _available_on(valuation_date, signal_date) and (
        (valuation_score is not None and valuation_score <= 40) or (per is not None and per >= 50)
    ):
        risks.append("估值偏高")

    values = {item["kind"]: float(item["value_pct"]) for item in items}
    if values.get("revenue_yoy") is not None and values["revenue_yoy"] < 10:
        risks.append("成長放慢")
    elif values.get("revenue_mom") is not None and values["revenue_mom"] <= -5:
        risks.append("成長放慢")

    explicit = (
        ("single_customer_risk", "單一客戶"),
        ("single_supplier_risk", "單一供應商"),
        ("competition_increasing", "競爭增加"),
    )
    risk_date = _date(row.get("industry_risk_date"))
    risk_source = str(row.get("industry_risk_source") or "").strip()
    if risk_source and _available_on(risk_date, signal_date):
        risks.extend(label for key, label in explicit if row.get(key) is True)
    return "、".join(dict.fromkeys(risks)) or "資料不足"


def analyze_industry_lifecycle(row: dict[str, Any], signal_date: str) -> dict[str, Any]:
    """Return a frozen, auditable life-cycle snapshot for one stock."""
    position = str(
        row.get("industry_position") or row.get("industry") or row.get("theme") or "資料不足"
    ).strip()[:100] or "資料不足"
    items = _evidence_items(row, signal_date)
    stage, stage_reason = _stage(row, signal_date, items)
    sources = []
    for item in items:
        source = {"date": item["date"], "source": item["source"]}
        if source not in sources:
            sources.append(source)
    explicit_source = str(row.get("industry_lifecycle_source") or "").strip()
    explicit_date = _date(row.get("industry_lifecycle_date"))
    if explicit_source and _available_on(explicit_date, signal_date):
        source = {"date": explicit_date, "source": explicit_source[:160]}
        if source not in sources:
            sources.append(source)

    valuation_source = str(_first(row, ("valuation_source", "us_company_data_source", "financial_quality_source")) or "").strip()
    valuation_date = _date(_first(row, ("valuation_date", "financial_report_date")))
    if valuation_source and _available_on(valuation_date, signal_date):
        source = {"date": valuation_date, "source": valuation_source[:160]}
        if source not in sources:
            sources.append(source)
    risk_source = str(row.get("industry_risk_source") or "").strip()
    risk_date = _date(row.get("industry_risk_date"))
    if risk_source and _available_on(risk_date, signal_date):
        source = {"date": risk_date, "source": risk_source[:160]}
        if source not in sources:
            sources.append(source)

    explicit_evidence = str(row.get("industry_lifecycle_evidence") or "").strip()
    evidence_labels = [explicit_evidence[:200]] if explicit_evidence and explicit_source and _available_on(explicit_date, signal_date) else []
    for item in items:
        value = float(item["value_pct"])
        evidence_labels.append(f'{item["label"]} {value:+.1f}%')
    evidence = "、".join(evidence_labels[:3]) or "資料不足"
    score = {
        "萌芽期": 62.0,
        "成長期": 75.0,
        "成熟期": 52.0,
        "再創新期": 66.0,
        "資料不足": 50.0,
    }[stage]
    return {
        "industry_position": position,
        "stage": stage,
        "evidence": evidence,
        "evidence_items": items,
        "stage_reason": stage_reason,
        "risk": _risk(row, signal_date, items),
        "judged_at": signal_date,
        "sources": sources,
        "status": "point_in_time_frozen" if stage != "資料不足" else "data_insufficient",
        "shadow_score": score,
        "affects_horizon": "UP_60D",
        "formal_v6_unchanged": True,
    }
