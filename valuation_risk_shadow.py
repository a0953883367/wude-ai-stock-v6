"""Independent valuation-pressure radar and forward validation ledger.

This module is intentionally downstream of the production ranking.  It reads
the already-scored universe, writes only its own report and never mutates a
row supplied by the caller.  Its output describes valuation pressure rather
than declaring that any part of a market value is certainly a bubble.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import json
import math
from pathlib import Path
from statistics import median
from typing import Any, Iterable


SCHEMA_VERSION = 2
MODEL_VERSION = "VALUATION-RISK-SHADOW-V2"
MARKETS = ("TW", "US")
CLOSED_PERIOD = {"TW": "evening", "US": "morning"}
MAX_SNAPSHOTS = 30
MAX_OUTCOMES = 8_000


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _positive(value: Any) -> float | None:
    number = _finite(value)
    return number if number is not None and number > 0 else None


def _is_etf(row: dict[str, Any]) -> bool:
    return "ETF" in str(row.get("type") or "").upper()


def _is_equity(row: dict[str, Any]) -> bool:
    return str(row.get("market") or "").upper() in MARKETS and not _is_etf(row)


def _industry(row: dict[str, Any]) -> str:
    return str(row.get("industry") or row.get("theme") or "其他")[:80]


def _valuation_profile(industry: str) -> str:
    return (
        "FINANCIAL"
        if any(token in industry for token in ("銀行", "金融", "保險", "Bank", "Insurance"))
        else "OPERATING_COMPANY"
    )


def _annualize_ytd(value: float | None, report_date: str) -> float | None:
    if value is None:
        return None
    try:
        month = int(report_date[5:7])
    except (TypeError, ValueError, IndexError):
        return value
    elapsed_quarters = {3: 1, 6: 2, 9: 3, 12: 4}.get(month)
    return value * 4 / elapsed_quarters if elapsed_quarters else value


def _company_values(row: dict[str, Any]) -> dict[str, Any]:
    market = str(row.get("market") or "").upper()
    price = _positive(row.get("official_close_price")) or _positive(row.get("price"))
    report_date = str(row.get("financial_report_date") or "")
    statement_unit = str(row.get("financial_statement_unit") or "")
    statement_multiplier = 1000.0 if statement_unit == "TWD_thousands_as_reported" else 1.0

    revenue = _positive(row.get("total_revenue_ttm"))
    revenue_source = "TTM"
    if revenue is None:
        statement_revenue = _positive(row.get("statement_revenue_ytd"))
        if statement_revenue is not None:
            statement_revenue *= statement_multiplier
        revenue = _annualize_ytd(statement_revenue, report_date)
        revenue_source = "annualized_ytd" if revenue is not None else "unavailable"

    net_income = _finite(row.get("net_income_ttm"))
    net_income_source = "TTM"
    if net_income is None:
        statement_net_income = _finite(row.get("statement_net_income_ytd"))
        if statement_net_income is not None:
            statement_net_income *= statement_multiplier
        net_income = _annualize_ytd(statement_net_income, report_date)
        net_income_source = "annualized_ytd" if net_income is not None else "unavailable"

    shares = _positive(row.get("shares_outstanding"))
    if shares is None:
        statement_income = _finite(row.get("statement_net_income_ytd"))
        eps = _finite(row.get("eps"))
        if statement_income is not None and eps not in (None, 0) and statement_income * eps > 0:
            shares = abs(statement_income * statement_multiplier / eps)

    market_cap = _positive(row.get("market_cap"))
    market_cap_source = "reported"
    if market_cap is None and shares is not None and price is not None:
        market_cap = shares * price
        market_cap_source = "price_times_inferred_shares"
    if market_cap is None:
        market_cap_source = "unavailable"

    total_cash = _finite(row.get("total_cash"))
    total_debt = _finite(row.get("total_debt"))
    if total_debt is not None and statement_multiplier != 1.0:
        total_debt *= statement_multiplier
    enterprise_value = _positive(row.get("enterprise_value"))
    enterprise_value_source = "reported"
    if enterprise_value is None and market_cap is not None and total_debt is not None and total_cash is not None:
        enterprise_value = market_cap + total_debt - total_cash
        enterprise_value_source = "derived"
    if enterprise_value is None:
        enterprise_value_source = "unavailable"

    free_cash_flow = _finite(row.get("free_cash_flow"))
    operating_cash_flow = _finite(row.get("operating_cash_flow"))
    per = _finite(row.get("per"))
    per_source = "reported"
    if per is None and price is not None:
        annualized_eps = _annualize_ytd(_finite(row.get("eps")), report_date)
        if annualized_eps is not None and annualized_eps > 0:
            per = price / annualized_eps
            per_source = "price_divided_by_official_annualized_ytd_eps"
    if per is None:
        per_source = "unavailable"
    earnings_years = per if per is not None and per > 0 else None
    pbr = _positive(row.get("pbr"))
    pbr_source = "reported"
    if pbr is None and price is not None:
        book_value = _positive(row.get("book_value_per_share"))
        if book_value is not None:
            pbr = price / book_value
            pbr_source = "price_divided_by_official_book_value_per_share"
    if pbr is None:
        pbr_source = "unavailable"
    sales_years = market_cap / revenue if market_cap is not None and revenue else None
    fcf_years = (
        market_cap / free_cash_flow
        if market_cap is not None and free_cash_flow is not None and free_cash_flow > 0
        else None
    )
    ev_sales = enterprise_value / revenue if enterprise_value is not None and revenue else None

    industry = _industry(row)
    values = {
        "market": market,
        "symbol": str(row.get("symbol") or ""),
        "name": str(row.get("name") or row.get("symbol") or ""),
        "asset_type": "ETF" if _is_etf(row) else "STOCK",
        "industry": industry,
        "valuation_profile": _valuation_profile(industry),
        "session_date": str(row.get("official_session_date") or ""),
        "price": price,
        "market_cap": market_cap,
        "market_cap_source": market_cap_source,
        "financial_statement_unit": "TWD" if statement_multiplier != 1.0 else statement_unit or None,
        "enterprise_value": enterprise_value,
        "enterprise_value_source": enterprise_value_source,
        "revenue_annualized": revenue,
        "revenue_source": revenue_source,
        "net_income_annualized": net_income,
        "net_income_source": net_income_source,
        "free_cash_flow": free_cash_flow,
        "operating_cash_flow": operating_cash_flow,
        "sales_years": sales_years,
        "earnings_years": earnings_years,
        "free_cash_flow_years": fcf_years,
        "ev_sales": ev_sales,
        "per_source": per_source,
        "pbr": pbr,
        "pbr_source": pbr_source,
        "revenue_yoy_pct": _finite(row.get("revenue_yoy_pct")),
        "operating_margin_pct": _finite(row.get("operating_margin_pct")),
        "roe_pct": _finite(row.get("roe_pct")),
        "debt_ratio_pct": _finite(row.get("debt_ratio_pct")),
        "financial_report_date": report_date,
        "official_rank": row.get("overall_rank"),
        "official_ranking_score": row.get("overall_ranking_score"),
        "valuation_affects_official_score": False,
    }
    available = sum(
        values[key] is not None
        for key in ("market_cap", "revenue_annualized", "earnings_years", "free_cash_flow")
    )
    values["core_data_available"] = available
    values["core_data_total"] = 4
    return values


def _absolute_pressure(metric: str, value: float) -> float:
    thresholds = {
        "sales_years": ((2, 15), (5, 35), (10, 60), (20, 80)),
        "earnings_years": ((15, 15), (25, 35), (40, 60), (60, 80)),
        "free_cash_flow_years": ((20, 15), (35, 40), (60, 70), (90, 85)),
        "pbr": ((1, 15), (1.5, 35), (2.5, 60), (4, 80)),
    }
    for limit, score in thresholds[metric]:
        if value <= limit:
            return float(score)
    return 95.0


def _percentile(value: float, peers: list[float]) -> float | None:
    if len(peers) < 5:
        return None
    below = sum(item < value for item in peers)
    equal = sum(item == value for item in peers)
    return (below + equal * 0.5) / len(peers) * 100


def _quantile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    low = math.floor(index)
    high = math.ceil(index)
    if low == high:
        return ordered[low]
    return ordered[low] * (high - index) + ordered[high] * (index - low)


def _pressure_label(score: float | None, sufficient: bool) -> str:
    if score is None or not sufficient:
        return "⚪ 資料不足"
    if score < 30:
        return "🟢 估值壓力低"
    if score < 50:
        return "🟡 估值偏貴"
    if score < 70:
        return "🟠 高成長期待"
    return "🔴 極高估值壓力"


def _score_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    peer_values: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for item in rows:
        if item["asset_type"] != "STOCK":
            continue
        metrics = (
            ("pbr", "earnings_years")
            if item.get("valuation_profile") == "FINANCIAL"
            else ("sales_years", "earnings_years", "free_cash_flow_years", "pbr")
        )
        for metric in metrics:
            value = _positive(item.get(metric))
            if value is not None:
                peer_values[(item["market"], item["industry"], metric)].append(value)

    output = []
    for source in rows:
        item = dict(source)
        if item["asset_type"] == "ETF":
            item.update({
                "status": "not_applicable",
                "valuation_pressure_score": None,
                "valuation_pressure_label": "⚪ ETF改看持股加權估值／淨值折溢價",
                "reason": "ETF不是營運公司，不套用公司營收與現金流倍數",
                "estimated_excess_market_value": None,
                "estimated_excess_pct_of_market_cap": None,
            })
            output.append(item)
            continue

        component_scores = []
        peer_percentiles = {}
        peer_medians = {}
        fair_value_candidates = []
        metric_weights = (
            (("pbr", 0.55), ("earnings_years", 0.45))
            if item.get("valuation_profile") == "FINANCIAL"
            else (
                ("sales_years", 0.30), ("earnings_years", 0.30),
                ("free_cash_flow_years", 0.25), ("pbr", 0.15),
            )
        )
        for metric, base_weight in metric_weights:
            value = _positive(item.get(metric))
            peers = peer_values[(item["market"], item["industry"], metric)]
            if value is None:
                continue
            percentile = _percentile(value, peers)
            peer_median = median(peers) if len(peers) >= 5 else None
            pressure = _absolute_pressure(metric, value)
            if percentile is not None:
                pressure = pressure * 0.65 + percentile * 0.35
                peer_percentiles[metric] = round(percentile, 2)
            if peer_median is not None:
                peer_medians[metric] = round(peer_median, 4)
                if metric == "sales_years" and item.get("revenue_annualized"):
                    fair_value_candidates.append(item["revenue_annualized"] * peer_median)
                elif metric == "earnings_years" and _positive(item.get("net_income_annualized")) is not None:
                    fair_value_candidates.append(item["net_income_annualized"] * peer_median)
                elif metric == "free_cash_flow_years" and _positive(item.get("free_cash_flow")) is not None:
                    fair_value_candidates.append(item["free_cash_flow"] * peer_median)
            component_scores.append((pressure, base_weight))

        if component_scores:
            score = sum(value * weight for value, weight in component_scores) / sum(
                weight for _, weight in component_scores
            )
            if item.get("net_income_annualized") is not None and item["net_income_annualized"] <= 0:
                score += 8
            if item.get("free_cash_flow") is not None and item["free_cash_flow"] <= 0:
                score += 8
            if item.get("operating_margin_pct") is not None and item["operating_margin_pct"] < 0:
                score += 5
            if item.get("debt_ratio_pct") is not None and item["debt_ratio_pct"] >= 70:
                score += 5
            score = round(max(0.0, min(100.0, score)), 1)
        else:
            score = None

        metric_count = len(component_scores)
        # Official PER/PBR are real valuation evidence even when a provider
        # does not expose shares outstanding.  They can support the pressure
        # classification; market-cap-dependent fair-value gaps remain absent.
        sufficient = metric_count >= 2
        fair_value = median(fair_value_candidates) if fair_value_candidates else None
        market_cap = item.get("market_cap")
        excess_value = (
            max(0.0, market_cap - fair_value)
            if market_cap is not None and fair_value is not None
            else None
        )
        excess_pct = (
            excess_value / market_cap * 100
            if excess_value is not None and market_cap
            else None
        )
        sales_years = _positive(item.get("sales_years"))
        sales_peer = peer_medians.get("sales_years")
        implied_cagr = None
        if sales_years and sales_peer and sales_years > sales_peer:
            implied_cagr = ((sales_years / sales_peer) ** (1 / 5) - 1) * 100

        item.update({
            "status": "ready" if sufficient else "data_insufficient",
            "valuation_pressure_score": score,
            "valuation_pressure_label": _pressure_label(score, sufficient),
            "valuation_metric_count": metric_count,
            "market_value_gap_available": market_cap is not None and fair_value is not None,
            "peer_percentiles": peer_percentiles,
            "peer_medians": peer_medians,
            "peer_based_fair_market_value": None if fair_value is None else round(fair_value, 2),
            "estimated_excess_market_value": None if excess_value is None else round(excess_value, 2),
            "estimated_excess_pct_of_market_cap": None if excess_pct is None else round(excess_pct, 2),
            "implied_revenue_cagr_to_peer_multiple_5y_pct": (
                None if implied_cagr is None else round(implied_cagr, 2)
            ),
            "reason": (
                "同業倍數與至少兩項估值資料可用"
                if sufficient and market_cap is not None
                else "官方PER／PBR等至少兩項估值資料可用；市值差額仍待股份資料補齊"
                if sufficient
                else "至少需要PER、PBR、營收倍數或自由現金流倍數中的兩項"
            ),
        })
        output.append(item)
    return output


def _completed_checkpoint(market: str, period: str, updated_at: str, intraday: bool) -> bool:
    if intraday or period != CLOSED_PERIOD[market]:
        return False
    try:
        local_time = datetime.fromisoformat(updated_at.replace("/", "-"))
    except (TypeError, ValueError):
        return False
    return local_time.hour >= 14 if market == "TW" else 5 <= local_time.hour < 12


def _session_date(rows: Iterable[dict[str, Any]], market: str) -> str:
    dates = [
        str(row.get("session_date") or "")
        for row in rows
        if row.get("market") == market and row.get("asset_type") == "STOCK" and row.get("session_date")
    ]
    return max(dates) if dates else ""


def _evaluate_outcomes(
    snapshots: list[dict[str, Any]], outcomes: list[dict[str, Any]],
    current_rows: list[dict[str, Any]], market: str,
) -> list[dict[str, Any]]:
    current = {
        str(row.get("symbol")): row for row in current_rows
        if row.get("market") == market and row.get("asset_type") == "STOCK"
    }
    existing = {(item.get("signal_session_date"), item.get("symbol")) for item in outcomes}
    for snapshot in snapshots:
        signal_date = str(snapshot.get("session_date") or "")
        for frozen in snapshot.get("stocks", []):
            symbol = str(frozen.get("symbol") or "")
            key = (signal_date, symbol)
            now = current.get(symbol)
            if key in existing or not now:
                continue
            outcome_date = str(now.get("session_date") or "")
            start = _positive(frozen.get("price"))
            end = _positive(now.get("price"))
            if not signal_date or not outcome_date or outcome_date <= signal_date or not start or not end:
                continue
            outcomes.append({
                "market": market,
                "symbol": symbol,
                "signal_session_date": signal_date,
                "outcome_session_date": outcome_date,
                "valuation_pressure_score": frozen.get("valuation_pressure_score"),
                "valuation_pressure_label": frozen.get("valuation_pressure_label"),
                "return_pct": round((end / start - 1) * 100, 4),
                "valid": True,
            })
            existing.add(key)
    return outcomes[-MAX_OUTCOMES:]


def _validation_summary(outcomes: list[dict[str, Any]], market: str) -> dict[str, Any]:
    rows = [item for item in outcomes if item.get("market") == market and item.get("valid")]
    buckets: dict[str, list[float]] = defaultdict(list)
    for item in rows:
        label = str(item.get("valuation_pressure_label") or "資料不足")
        value = _finite(item.get("return_pct"))
        if value is not None:
            buckets[label].append(value)
    return {
        "effective_samples": len(rows),
        "minimum_samples_before_weight_review": 100,
        "weight_review_ready": len(rows) >= 100 and len({item.get("signal_session_date") for item in rows}) >= 10,
        "effective_sessions": len({item.get("signal_session_date") for item in rows}),
        "buckets": {
            label: {
                "samples": len(values),
                "average_next_session_return_pct": round(sum(values) / len(values), 4),
                "win_rate_pct": round(sum(value > 0 for value in values) / len(values) * 100, 2),
            }
            for label, values in sorted(buckets.items())
        },
    }


def update_valuation_risk_shadow(
    reports_dir: Path,
    rows: list[dict[str, Any]],
    *,
    period: str,
    updated_at: str,
    intraday: bool,
) -> dict[str, Any]:
    """Update the isolated report without mutating official ranking rows."""
    path = reports_dir / "valuation_risk_shadow.json"
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
        previous = {}

    valued = _score_rows([_company_values(dict(row)) for row in rows])
    same_model = previous.get("model_version") == MODEL_VERSION
    snapshots = (
        previous.get("snapshots")
        if same_model and isinstance(previous.get("snapshots"), dict)
        else {}
    )
    outcomes = (
        previous.get("outcomes")
        if same_model and isinstance(previous.get("outcomes"), list)
        else []
    )
    market_snapshots: dict[str, list[dict[str, Any]]] = {}
    for market in MARKETS:
        history = list(snapshots.get(market) or [])
        outcomes = _evaluate_outcomes(history, outcomes, valued, market)
        session_date = _session_date(valued, market)
        if _completed_checkpoint(market, period, updated_at, intraday) and session_date:
            if not any(item.get("session_date") == session_date for item in history):
                frozen = [
                    {
                        "symbol": item["symbol"],
                        "price": item["price"],
                        "valuation_pressure_score": item["valuation_pressure_score"],
                        "valuation_pressure_label": item["valuation_pressure_label"],
                        "status": item["status"],
                    }
                    for item in valued
                    if item.get("market") == market and item.get("asset_type") == "STOCK"
                    and item.get("price") is not None
                ]
                history.append({
                    "session_date": session_date,
                    "frozen_at": updated_at,
                    "model_version": MODEL_VERSION,
                    "stocks": frozen,
                })
        market_snapshots[market] = history[-MAX_SNAPSHOTS:]

    coverage = {}
    for market in MARKETS:
        stock_rows = [item for item in valued if item["market"] == market and item["asset_type"] == "STOCK"]
        etf_rows = [item for item in valued if item["market"] == market and item["asset_type"] == "ETF"]
        ready = [item for item in stock_rows if item.get("status") == "ready"]
        coverage[market] = {
            "stocks_total": len(stock_rows),
            "stocks_ready": len(ready),
            "stocks_data_insufficient": len(stock_rows) - len(ready),
            "stock_coverage_pct": round(len(ready) / len(stock_rows) * 100, 2) if stock_rows else 0.0,
            "etfs_total": len(etf_rows),
            "session_date": _session_date(valued, market),
        }

    payload = {
        "schema_version": SCHEMA_VERSION,
        "model_version": MODEL_VERSION,
        "updated_at": updated_at,
        "period": period,
        "run_mode": "intraday_refresh" if intraday else "scheduled_report",
        "status": "shadow_collecting",
        "official_ranking_affected": False,
        "official_weights_affected": False,
        "places_orders": False,
        "method": {
            "purpose": "估值壓力與同業相對風險；不宣稱確定泡沫",
            "company_metrics": ["市值／營收", "市值／獲利", "市值／自由現金流", "股價淨值比", "同業百分位"],
            "financial_company_metrics": ["股價淨值比", "市值／獲利", "ROE", "同業百分位"],
            "etf_method": "ETF不套公司倍數，待持股加權估值與NAV折溢價資料",
            "estimated_excess_value": "僅在同產業至少5筆有效倍數時，以同業中位數估算；不是確定泡沫",
            "weight_gate": "至少10個交易日且100筆有效向前樣本，通過後才可另行審查最高5%中長期風險權重",
        },
        "coverage": coverage,
        "validation": {market: _validation_summary(outcomes, market) for market in MARKETS},
        "snapshots": market_snapshots,
        "outcomes": outcomes,
        "data": valued,
    }
    reports_dir.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    tmp.replace(path)
    return payload
