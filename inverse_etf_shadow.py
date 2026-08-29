"""Isolated inverse-ETF mapping database and forward-only shadow ledger.

This module never changes the 374-stock universe, formal scores, rankings,
medium/long statistics, or broker state.  Returns are measured from the ETF's
own prices because daily-reset funds cannot be modelled as index return times
the stated leverage across several sessions.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from statistics import median
from typing import Any


VERSION = 1
HORIZONS = (1, 2, 3, 5)
CLOSED_PERIOD = {"TW": "evening", "US": "morning"}


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def load_catalog(path: Path | None = None) -> dict[str, Any]:
    target = path or Path(__file__).with_name("inverse_etf_catalog.json")
    payload = json.loads(target.read_text(encoding="utf-8"))
    products = payload.get("products") if isinstance(payload, dict) else None
    if not isinstance(products, list) or not products:
        raise ValueError("inverse ETF catalog must contain products[]")
    seen: set[str] = set()
    for row in products:
        symbol = str(row.get("symbol") or "").upper()
        target_value = _finite(row.get("daily_target"))
        if not symbol or symbol in seen or target_value not in (-1.0, -2.0, -3.0):
            raise ValueError("invalid inverse ETF catalog row")
        seen.add(symbol)
    return payload


def _text(row: dict[str, Any]) -> str:
    return " ".join(str(row.get(key) or "") for key in ("symbol", "name", "theme", "industry", "etf_category")).lower()


def _price_series(frame: Any):
    if frame is None or getattr(frame, "empty", True):
        return None
    for name in ("close", "Close", "adj close", "Adj Close"):
        try:
            series = frame[name]
        except (KeyError, TypeError):
            continue
        try:
            return series.astype(float).pct_change(fill_method=None).replace([math.inf, -math.inf], float("nan")).dropna()
        except (TypeError, ValueError, AttributeError):
            return None
    return None


def _mapping_quality(stock_frame: Any, inverse_frame: Any, strength: str) -> dict[str, Any]:
    """Measure the realised hedge relationship without treating a proxy as exact."""
    stock_returns, inverse_returns = _price_series(stock_frame), _price_series(inverse_frame)
    structural = {"index": 30.0, "sector": 25.0, "broad": 15.0}.get(strength, 0.0)
    if stock_returns is None or inverse_returns is None:
        return {
            "mapping_quality_score": None,
            "negative_correlation_20d": None,
            "negative_correlation_60d": None,
            "hedge_sensitivity": None,
            "correlation_samples": 0,
            "mapping_data_status": "waiting_for_aligned_history",
        }
    paired = stock_returns.rename("stock").to_frame().join(
        inverse_returns.rename("inverse"), how="inner"
    ).dropna()
    samples = len(paired)
    correlations: dict[int, float | None] = {}
    for window in (20, 60):
        sample = paired.tail(window)
        correlation = _finite(sample["stock"].corr(sample["inverse"])) if len(sample) >= 10 else None
        correlations[window] = round(max(0.0, -correlation) * 100, 1) if correlation is not None else None
    down = paired[paired["stock"] < 0].tail(60)
    sensitivity = None
    if len(down) >= 5 and float(down["stock"].abs().mean()) > 0:
        sensitivity = float(down["inverse"].mean()) / float(down["stock"].abs().mean())
    available_corr = [value for value in correlations.values() if value is not None]
    correlation_score = sum(available_corr) / len(available_corr) if available_corr else 0.0
    coverage = min(samples, 40) / 40 * 15
    quality = min(100.0, structural + correlation_score * 0.55 + coverage)
    return {
        "mapping_quality_score": round(quality, 1),
        "negative_correlation_20d": correlations[20],
        "negative_correlation_60d": correlations[60],
        "hedge_sensitivity": round(sensitivity, 3) if sensitivity is not None else None,
        "correlation_samples": samples,
        "mapping_data_status": "complete" if samples >= 20 else "limited_history",
    }


def classify_mapping(row: dict[str, Any]) -> tuple[str, str, str]:
    """Return product group, mapping strength, and an honest explanation."""
    market, text = str(row.get("market") or "").upper(), _text(row)
    if market == "TW":
        if any(token in text for token in ("nasdaq", "美國科技", "北美科技")):
            return "tw_nasdaq", "index", "NASDAQ 類標的以台灣掛牌 NASDAQ 反1作指數替代"
        return "tw_broad", "broad", "台股目前無反2／反3；僅以台灣50反1作大盤替代"
    if any(token in text for token in ("半導體", "晶片", "晶圓", "記憶體", "eda", "fpga")):
        return "us_semiconductor", "sector", "半導體產業以 SOXS 反3作產業替代"
    if any(token in text for token in ("生技", "biotech", "製藥")):
        return "us_biotech", "sector", "生技產業以 LABD 反3作產業替代"
    if any(token in text for token in ("金融", "銀行", "fintech")):
        return "us_financial", "sector", "金融產業以 FAZ 反3作產業替代"
    if any(token in text for token in ("房地產", "real estate", "reit")):
        return "us_real_estate", "sector", "房地產產業以 DRV 反3作產業替代"
    if any(token in text for token in ("公用事業", "utilities")):
        return "us_utilities", "sector", "公用事業以 SDP 反2作產業替代"
    if any(token in text for token in ("能源", "石油", "天然氣")) and "核" not in text:
        return "us_energy", "sector", "傳統能源以 ERY 反2作產業替代"
    if any(token in text for token in ("nasdaq", "科技etf", "ai etf")):
        return "us_nasdaq", "index", "NASDAQ 類標的以 SQQQ 反3作指數替代"
    if any(token in text for token in ("ai", "雲端", "軟體", "量子", "資安", "網路", "robotaxi", "自駕")):
        return "us_technology", "sector", "科技成長股以 TECS 反3作產業替代"
    return "us_broad", "broad", "無精準反向商品；僅以 SPXS 反3作美股大盤替代"


def build_mapping_database(rows: list[dict[str, Any]], *, updated_at: str = "", catalog: dict[str, Any] | None = None, histories: dict[str, Any] | None = None) -> dict[str, Any]:
    selected = catalog or load_catalog()
    by_group = {item["group"]: item for item in selected["products"]}
    mappings = []
    counts: Counter[str] = Counter()
    for row in rows:
        group, strength, rationale = classify_mapping(row)
        product = by_group[group]
        quality = _mapping_quality(
            (histories or {}).get(str(row.get("symbol") or "")),
            (histories or {}).get(product["symbol"]),
            strength,
        )
        counts[strength] += 1
        mappings.append({
            "symbol": row.get("symbol"), "name": row.get("name"), "market": row.get("market"),
            "type": row.get("type"), "theme": row.get("theme"), "industry": row.get("industry"),
            "mapping_strength": strength, "inverse_symbol": product["symbol"],
            "inverse_name": product["name"], "daily_target": product["daily_target"],
            "benchmark": product["benchmark"], "group": group, "rationale": rationale,
            "direct_single_stock_inverse": False,
            **quality,
        })
    return {
        "version": VERSION, "model_version": 2, "updated_at": updated_at, "universe_count": len(mappings),
        "summary": {"TW": sum(x["market"] == "TW" for x in mappings), "US": sum(x["market"] == "US" for x in mappings), **dict(counts)},
        "policy": {
            "meaning": "可反代表有可交易的指數／產業替代，不代表與單一股票精準反向",
            "taiwan_minus_2_or_3_available": False, "daily_reset": True,
            "formal_ranking_locked": True, "flow_weight_shadow_unchanged": True,
            "medium_45_day_unchanged": True, "long_6_month_unchanged": True,
            "broker_orders": False,
            "mapping_quality_uses_real_aligned_returns": True,
        },
        "products": selected["products"], "mappings": mappings,
    }


def build_product_rows(catalog: dict[str, Any], histories: dict[str, Any], session_dates: dict[str, str]) -> dict[str, dict[str, Any]]:
    output = {}
    for product in catalog["products"]:
        expected = session_dates.get(product["market"], "")
        matched = None
        frame = histories.get(product["symbol"])
        if expected and frame is not None and not getattr(frame, "empty", True):
            for index, row in frame.iterrows():
                if (index.date().isoformat() if hasattr(index, "date") else str(index)[:10]) == expected:
                    matched = row
                    break
        def value(name: str) -> float | None:
            return _finite(matched.get(name)) if matched is not None else None
        open_price, close = value("open"), value("close")
        if open_price is None: open_price = value("Open")
        if close is None: close = value("Close")
        previous_close = None
        volume_ratio = None
        if frame is not None and not getattr(frame, "empty", True) and matched is not None:
            try:
                before = frame.loc[frame.index < matched.name]
                close_column = "close" if "close" in frame.columns else "Close"
                if not before.empty:
                    previous_close = _finite(before.iloc[-1].get(close_column))
                volume_column = "volume" if "volume" in frame.columns else "Volume"
                volumes = frame[volume_column].astype(float).tail(20)
                current_volume = _finite(matched.get(volume_column))
                if current_volume is not None and len(volumes) >= 5 and float(volumes.mean()) > 0:
                    volume_ratio = current_volume / float(volumes.mean())
            except (KeyError, TypeError, ValueError, AttributeError):
                pass
        session_change = (close / open_price - 1) * 100 if open_price and close else None
        gap = (open_price / previous_close - 1) * 100 if open_price and previous_close else None
        chase_threshold = {1.0: 2.5, 2.0: 4.0, 3.0: 6.0}[abs(float(product["daily_target"]))]
        output[product["symbol"]] = {
            **product, "session_date": expected or None, "open": open_price, "close": close,
            "previous_close": previous_close,
            "session_change_pct": round(session_change, 2) if session_change is not None else None,
            "opening_gap_pct": round(gap, 2) if gap is not None else None,
            "volume_ratio_20d": round(volume_ratio, 2) if volume_ratio is not None else None,
            "no_chase": bool(session_change is not None and session_change >= chase_threshold),
            "no_chase_threshold_pct": chase_threshold,
            "data_complete": bool(open_price and close and open_price > 0 and close > 0),
        }
    return output


def _bear_score(rows: list[dict[str, Any]]) -> tuple[float, dict[str, Any]]:
    changes = [x for row in rows if (x := _finite(row.get("change_pct"))) is not None]
    if len(changes) < 3:
        return 0.0, {"coverage": len(changes), "reason": "樣本不足"}
    negative = sum(value < 0 for value in changes) / len(changes)
    med = median(changes)
    breakdown = sum(bool(row.get("breakdown20")) for row in rows) / len(rows)
    score = min(100.0, negative * 55 + max(0.0, -med) * 12 + breakdown * 25)
    return round(score, 1), {"coverage": len(changes), "negative_breadth_pct": round(negative * 100, 1), "median_change_pct": round(med, 2), "breakdown_pct": round(breakdown * 100, 1)}


def empty_state(updated_at: str = "") -> dict[str, Any]:
    return {"version": VERSION, "updated_at": updated_at, "mode": "isolated_inverse_etf_shadow", "policy": {"actual_etf_prices_only": True, "daily_return_times_leverage_forbidden": True, "formal_ranking_locked": True, "flow_weight_shadow_unchanged": True, "broker_orders": False}, "markets": {"TW": {"cohorts": [], "current_candidates": [], "summary": {}}, "US": {"cohorts": [], "current_candidates": [], "summary": {}}}}


def _summary(cohorts: list[dict[str, Any]]) -> dict[str, Any]:
    output = {}
    for horizon in HORIZONS:
        values = [c["outcomes"][str(horizon)]["actual_etf_return_pct"] for c in cohorts if c.get("outcomes", {}).get(str(horizon), {}).get("status") == "valid"]
        output[str(horizon)] = {"samples": len(values), "win_rate_pct": round(sum(v > 0 for v in values) / len(values) * 100, 1) if values else None, "average_return_pct": round(sum(values) / len(values), 3) if values else None}
    return output


def update_inverse_etf_shadow(reports_dir: Path, rows: list[dict[str, Any]], product_rows: dict[str, dict[str, Any]], *, period: str, updated_at: str, intraday: bool = False, catalog: dict[str, Any] | None = None, histories: dict[str, Any] | None = None) -> dict[str, Any]:
    selected = catalog or load_catalog()
    database = build_mapping_database(rows, updated_at=updated_at, catalog=selected, histories=histories)
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "inverse_etf_database.json").write_text(json.dumps(database, ensure_ascii=False, indent=2), encoding="utf-8")
    path = reports_dir / "inverse_etf_shadow.json"
    try: state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError): state = empty_state(updated_at)
    if state.get("version") != VERSION: state = empty_state(updated_at)
    state["updated_at"] = updated_at
    if intraday:
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"); return state
    mapping_by_symbol = {m["symbol"]: m for m in database["mappings"]}
    for market in ("TW", "US"):
        market_rows = [r for r in rows if r.get("market") == market]
        if period != CLOSED_PERIOD[market] or not market_rows: continue
        session = Counter(str(r.get("official_session_date") or "") for r in market_rows).most_common(1)[0][0]
        book = state["markets"][market]
        # Advance existing samples strictly with the inverse ETF's own OHLC.
        for cohort in book["cohorts"]:
            if cohort.get("status") in ("complete", "quarantined") or cohort.get("signal_session_date") == session: continue
            price = product_rows.get(cohort["inverse_symbol"]) or {}
            if price.get("session_date") != session or not price.get("data_complete"): continue
            if cohort.get("entry_open") is None:
                cohort["entry_open"] = price["open"]; cohort["status"] = "tracking"
            cohort.setdefault("observed_sessions", []).append(session)
            day = len(cohort["observed_sessions"])
            if day in HORIZONS:
                actual = (price["close"] / cohort["entry_open"] - 1) * 100
                cohort.setdefault("outcomes", {})[str(day)] = {"status": "valid", "session_date": session, "actual_etf_return_pct": round(actual, 3), "price_source": "inverse_etf_own_ohlc"}
            if day >= max(HORIZONS): cohort["status"] = "complete"
        candidates = []
        groups = sorted({mapping_by_symbol[r.get("symbol")]["group"] for r in market_rows if r.get("symbol") in mapping_by_symbol})
        for group in groups:
            grouped = [r for r in market_rows if mapping_by_symbol.get(r.get("symbol"), {}).get("group") == group]
            score, evidence = _bear_score(grouped)
            product = next(p for p in selected["products"] if p["group"] == group)
            price = product_rows.get(product["symbol"]) or {}
            no_chase = bool(price.get("no_chase"))
            status = "no_chase" if no_chase and score >= 60 else "confirmed" if score >= 75 else "watch" if score >= 60 else "waiting"
            candidates.append({
                "group": group, "inverse_symbol": product["symbol"], "inverse_name": product["name"],
                "daily_target": product["daily_target"], "bear_score": score, "status": status,
                "constituent_count": len(grouped), "evidence": evidence,
                "product_session_change_pct": price.get("session_change_pct"),
                "product_opening_gap_pct": price.get("opening_gap_pct"),
                "product_volume_ratio_20d": price.get("volume_ratio_20d"),
                "no_chase": no_chase,
            })
            cohort_id = f"{market}:{session}:{product['symbol']}"
            existing = next((c for c in book["cohorts"] if c.get("cohort_id") == cohort_id), None)
            if existing and existing.get("status") == "quarantined" and existing.get("quarantine_reason") == "signal_session_inverse_price_missing" and price.get("session_date") == session and price.get("data_complete"):
                # A local/report artifact may be generated before the ETF batch
                # finishes. Repair only the same official session, never fill
                # from a prior or future date.
                existing["signal_close"] = price["close"]
                existing["status"] = "pending_entry"
                existing["quarantine_reason"] = None
            elif score >= 75 and existing is None:
                book["cohorts"].append({"cohort_id": cohort_id, "market": market, "signal_session_date": session, "inverse_symbol": product["symbol"], "inverse_name": product["name"], "daily_target": product["daily_target"], "bear_score": score, "evidence": evidence, "signal_close": price.get("close") if price.get("session_date") == session else None, "entry_open": None, "observed_sessions": [], "outcomes": {}, "status": "pending_entry" if price.get("data_complete") else "quarantined", "quarantine_reason": None if price.get("data_complete") else "signal_session_inverse_price_missing"})
        book["current_candidates"] = sorted(candidates, key=lambda x: x["bear_score"], reverse=True)
        book["summary"] = _summary(book["cohorts"])
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return state


def _recent_alert(alert: dict[str, Any], now: datetime, seconds: int = 900) -> bool:
    value = alert.get("detected_at")
    try:
        observed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    age = (now - observed.astimezone(timezone.utc)).total_seconds()
    return 0 <= age <= seconds


def build_live_overlay(
    database: dict[str, Any],
    shadow_state: dict[str, Any],
    capital_flow: dict[str, Any],
    alerts: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Read other ledgers and return an isolated, non-mutating live view."""
    current = now or datetime.now(timezone.utc)
    mappings = database.get("mappings") or []
    mapping_by_symbol = {str(row.get("symbol") or "").upper(): row for row in mappings}
    products = {row.get("group"): row for row in database.get("products") or []}
    recent_sells = [
        row for row in alerts
        if row.get("alert_side") == "sell" and _recent_alert(row, current)
    ]
    output = {
        "version": 1,
        "mode": "isolated_inverse_etf_live_overlay",
        "updated_at": current.isoformat(timespec="seconds"),
        "policy": {
            "reads_capital_flow_only": True,
            "formal_ranking_locked": True,
            "flow_weight_shadow_unchanged": True,
            "medium_45_day_unchanged": True,
            "long_6_month_unchanged": True,
            "broker_orders": False,
        },
        "markets": {},
    }
    for market in ("TW", "US"):
        market_flow = (((capital_flow.get("markets") or {}).get(market) or {}).get("windows") or {}).get("15m") or {}
        static_candidates = {
            row.get("group"): row
            for row in ((((shadow_state.get("markets") or {}).get(market) or {}).get("current_candidates")) or [])
        }
        groups = sorted({row.get("group") for row in mappings if row.get("market") == market and row.get("group")})
        cards = []
        for group in groups:
            group_maps = [row for row in mappings if row.get("market") == market and row.get("group") == group]
            quality_values = [_finite(row.get("mapping_quality_score")) for row in group_maps]
            quality_values = [value for value in quality_values if value is not None]
            mapping_quality = sum(quality_values) / len(quality_values) if quality_values else None
            static = static_candidates.get(group) or {}
            breadth_score = min(100.0, float(static.get("bear_score") or 0))
            outflow_rows = [
                row for row in (market_flow.get("top_outflows") or [])
                if (mapping_by_symbol.get(str(row.get("symbol") or "").upper()) or {}).get("group") == group
            ]
            sell_ratios = [100 - float(row.get("buy_ratio_pct") or 0) for row in outflow_rows]
            flow_strength = (sum(sell_ratios) / len(sell_ratios)) if sell_ratios else 0.0
            group_alerts = [
                row for row in recent_sells
                if row.get("market") == market
                and (mapping_by_symbol.get(str(row.get("symbol") or "").upper()) or {}).get("group") == group
            ]
            alert_strength = min(100.0, len(group_alerts) / 3 * 100)
            mapping_points = (mapping_quality or 0) * 0.30
            breadth_points = breadth_score * 0.25
            sell_points = min(100.0, flow_strength * 0.65 + alert_strength * 0.35) * 0.20
            # Derivatives and current inverse-ETF quote are deliberately not
            # inferred from stock prints. They remain missing until official
            # sources are wired and verified.
            derivative_points = 0.0
            product_points = 0.0
            score = round(mapping_points + breadth_points + sell_points + derivative_points + product_points, 1)
            missing = []
            if mapping_quality is None:
                missing.append("歷史配對仍在累積")
            missing.extend(["期貨／選擇權尚未接入", "反向ETF即時價差尚未接入"])
            no_chase = bool(static.get("no_chase"))
            status = "no_chase" if no_chase else "confirmed" if score >= 80 else "small_shadow" if score >= 70 else "watch" if score >= 60 else "waiting"
            cards.append({
                "group": group,
                "inverse_symbol": (products.get(group) or {}).get("symbol"),
                "inverse_name": (products.get(group) or {}).get("name"),
                "daily_target": (products.get(group) or {}).get("daily_target"),
                "live_score": score,
                "status": status,
                "no_chase": no_chase,
                "components": {
                    "mapping_quality": round(mapping_quality, 1) if mapping_quality is not None else None,
                    "bearish_breadth": round(breadth_score, 1),
                    "sell_flow": round(flow_strength, 1),
                    "large_sell_alerts_15m": len(group_alerts),
                    "derivatives": None,
                    "product_trading_quality": None,
                },
                "data_missing": missing,
            })
        output["markets"][market] = {
            "market_buy_ratio_pct": market_flow.get("buy_ratio_pct"),
            "market_positive_breadth_pct": market_flow.get("positive_breadth_pct"),
            "cards": sorted(cards, key=lambda row: row["live_score"], reverse=True),
        }
    return output
