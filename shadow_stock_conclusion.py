"""Fail-closed, read-only conclusions over existing hub and trade-plan outputs.

No model votes, probabilities, weights, calendar fetches, or broker calls here.
An eligible conclusion means only that a shadow plan passed these data gates.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import math
from typing import Any

from market_calendar import MARKET_ZONES, OfficialMarketCalendar

VERSION = "SHADOW-STOCK-CONCLUSION-V1"
LABELS = {"eligible": "符合影子計畫條件", "wait": "等待", "avoid": "避開", "insufficient": "資料不足"}
# Existing derived models share underlying inputs. They are context, not extra votes.
CORRELATED_GROUPS = {
    "formal_v6": "existing_price_and_model", "short_plan": "existing_price_and_model",
    "medium_45d": "existing_price_and_model", "long_6m": "existing_price_and_model",
    "tw_official_institution": "tw_institution", "institution": "tw_institution",
}


def _positive(value: Any) -> bool:
    try:
        return not isinstance(value, bool) and math.isfinite(float(value)) and float(value) > 0
    except (TypeError, ValueError):
        return False


def source_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    """Carry provenance through the hub without filling unknown source metadata.

    A generic `official_*` price field does not establish provider identity.
    Daily US provenance must be explicit; SIP intraday flags are not a substitute.
    """
    market = str(row.get("market") or "").upper()
    return {
        "market_contract_valid": row.get("market_contract_valid") is True,
        "session_date": row.get("official_session_date"),
        "close": row.get("official_close_price"),
        "attestation_status": ("pending" if "source_daily_ohlcv_complete" not in row else
                               "verified" if row.get("source_daily_ohlcv_complete") is True else "invalid"),
        "news_cache_stale": row.get("news_cache_stale") is True,
        "daily_proof": {k:v for k,v in (row.get("shadow_daily_ohlcv_proof") or {}).items()
                        if k in {"version", "shadow_only", "status", "reason", "source", "source_url",
                                 "source_session_date", "fetched_at", "raw_record_sha256",
                                 "source_payload_sha256", "formal_fields_unchanged"}},
        "ohlcv_complete": row.get("source_daily_ohlcv_complete") is True
                          and bool(row.get("official_session_date"))
                          and row.get("source_daily_ohlcv_session_date") == row.get("official_session_date"),
        "source": row.get("tw_price_source") if market == "TW" else row.get("us_daily_source"),
        "source_session_date": row.get("tw_official_session_date") if market == "TW" else row.get("us_daily_session_date"),
        "source_available": row.get("tw_official_price_available") is True if market == "TW" else row.get("us_daily_price_available") is True,
        "unit": row.get("tw_price_unit") if market == "TW" else row.get("us_daily_price_unit"),
    }


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value or ""))
    except ValueError:
        return None


def observed_by(value: Any, now: datetime) -> bool:
    """Repository report timestamps without offsets are Asia/Taipei wall time."""
    try:
        observed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=MARKET_ZONES["TW"])
        return observed <= now
    except (TypeError, ValueError):
        return False


def _evidence_date_valid(value: Any, session: Any, market: str, now: datetime) -> bool:
    text = str(value or "")
    if len(text) == 10:
        return _date(text) is not None and text == session
    if market not in MARKET_ZONES or not observed_by(text, now):
        return False
    try:
        observed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=MARKET_ZONES["TW"])
        return observed.astimezone(MARKET_ZONES[market]).date().isoformat() == session
    except (TypeError, ValueError):
        return False


def _verified(status: dict[str, Any], market: str) -> bool:
    allowed = {"verified_alpaca"} if market == "US" else {
        "verified_twse_tpex", "verified_conservative_union", "verified_twse_only", "verified_tpex_only",
    }
    return status.get("available") is True and status.get("status") in allowed


def _close_at(calendar: OfficialMarketCalendar, market: str, session: str) -> datetime | None:
    status = calendar.session_status(market, session)
    if not _verified(status, market) or status.get("is_session") is not True:
        return None
    close = "13:30" if market == "TW" else status.get("close")
    try:
        hour, minute = str(close).split(":")[:2]
        parsed = date.fromisoformat(session)
        return datetime(parsed.year, parsed.month, parsed.day, int(hour), int(minute), tzinfo=MARKET_ZONES[market])
    except (TypeError, ValueError, KeyError):
        return None


def _timing(calendar: OfficialMarketCalendar, market: str, session: Any,
            window: int, now: datetime) -> dict[str, Any]:
    result = {"calendar_verified": False, "completed": False, "fresh": False,
              "expires_at": None, "valid_through_session": None}
    parsed = _date(session)
    if market not in MARKET_ZONES or parsed is None:
        return result
    closed = _close_at(calendar, market, parsed.isoformat())
    if closed is None:
        return result
    today = now.astimezone(MARKET_ZONES[market]).date()
    if parsed > today or (today - parsed).days > 370:
        return result
    result["calendar_verified"] = True
    result["completed"] = closed <= now
    # Inspect every intervening official date. Missing cross-year coverage blocks.
    future = []
    fresh = result["completed"]
    cursor = parsed + timedelta(days=1)
    for _ in range(370):
        status = calendar.session_status(market, cursor.isoformat())
        if not _verified(status, market):
            result["calendar_verified"] = False
            return result
        if status.get("is_session"):
            close = _close_at(calendar, market, cursor.isoformat())
            if close is None:
                result["calendar_verified"] = False
                return result
            future.append((cursor.isoformat(), close))
            if close <= now:
                fresh = False
        if cursor >= today and len(future) >= max(1, window):
            break
        cursor += timedelta(days=1)
    result["fresh"] = fresh
    if len(future) >= max(1, window):
        result["valid_through_session"] = future[max(1, window) - 1][0] if window > 0 else None
        # A newer completed close invalidates the plan before a longer buy window.
        result["expires_at"] = future[0][1].isoformat()
    return result


def _news_observation_valid(value: Any, now: datetime) -> bool:
    """News scans follow observation time, including non-trading days (18h cache policy)."""
    text = str(value or "")
    if len(text) <= 10:
        return False
    try:
        observed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=MARKET_ZONES["TW"])
        return timedelta(0) <= now - observed < timedelta(hours=18)
    except (TypeError, ValueError):
        return False


def _evidence_audit(row: dict[str, Any], horizon: str, now: datetime) -> dict[str, Any]:
    seen, groups, sources = set(), set(), set()
    duplicates = 0
    invalid = []
    news_expiries = []
    for item in row.get("evidence") or []:
        if not isinstance(item, dict) or item.get("horizon") not in {horizon, "all", "market", "risk"}:
            continue
        if item.get("affects_decision") is not True:
            continue
        source = str(item.get("source_id") or "")
        if (not source or not item.get("provenance")
                or item.get("market") != row.get("market")
                or item.get("symbol") != row.get("symbol")
                or not (_news_observation_valid(item.get("as_of"), now)
                        and not (row.get("source_snapshot") or {}).get("news_cache_stale")
                        if source == "verified_news" else
                        _evidence_date_valid(item.get("as_of"), row.get("session_date"),
                                             str(row.get("market") or ""), now))
                or item.get("status") in {"stale", "expired", "missing", "unverified", "invalid", "data_blocked"}
                or item.get("direction") == "missing"):
            invalid.append(source or "unknown")
        if source == "verified_news" and _news_observation_valid(item.get("as_of"), now):
            scan = datetime.fromisoformat(str(item["as_of"]).replace("Z", "+00:00"))
            if scan.tzinfo is None:
                scan = scan.replace(tzinfo=MARKET_ZONES["TW"])
            news_expiries.append(scan + timedelta(hours=18))
        key = (source, item.get("horizon"), item.get("as_of"))
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        sources.add(source)
        groups.add(CORRELATED_GROUPS.get(source, source))
    return {"used_source_ids": sorted(sources), "correlated_groups": sorted(groups),
            "duplicate_count": duplicates, "invalid_source_ids": sorted(set(invalid)),
            "additional_weight": 0, "policy": "existing_models_once_no_extra_votes",
            "news_expires_at": min(news_expiries).isoformat() if news_expiries else None}


def build_conclusion(row: dict[str, Any], plan: dict[str, Any], *,
                     calendar: OfficialMarketCalendar, now: datetime,
                     source_coherent: bool = True) -> dict[str, Any]:
    """One auditable result per stock/horizon. Input dictionaries stay unchanged."""
    if now.tzinfo is None:
        raise ValueError("now must include timezone")
    market, session = str(row.get("market") or ""), row.get("session_date")
    snapshot = row.get("source_snapshot") or {}
    horizon = str(plan.get("horizon") or "")
    timing = _timing(calendar, market, session, int(plan.get("buy_window_sessions") or 0), now)
    audit = _evidence_audit(row, horizon, now)
    if audit.get("news_expires_at") and timing["expires_at"]:
        timing["expires_at"] = min(datetime.fromisoformat(timing["expires_at"]),
                                   datetime.fromisoformat(audit["news_expires_at"])).isoformat()
    etf = "ETF" in str(row.get("asset_type") or "").upper()
    sources = {"TWSE OpenAPI", "TPEx OpenAPI"} if market == "TW" else {"Alpaca SIP daily bars"}
    expected_unit = "TWD/shares" if market == "TW" else "USD/shares"
    gates = []
    def gate(code: str, passed: bool, reason: str) -> None:
        gates.append({"code": code, "passed": bool(passed), "reason": reason})
    gate("market", market in {"TW", "US"} and snapshot.get("market_contract_valid") is True,
         "市場資料契約未確認")
    gate("source", snapshot.get("source_available") is True and snapshot.get("source") in sources
         and snapshot.get("unit") == expected_unit
         and (not snapshot.get("daily_proof") or
              observed_by(snapshot["daily_proof"].get("fetched_at"), now)),
         "既有 Yahoo 日線僅供來源診斷，不是本結論允許的美股資料源"
         if market == "US" and snapshot.get("source") == "Yahoo Finance daily bars"
         else "收盤來源／授權資料標示或單位未確認")
    gate("source_date", bool(_date(session)) and snapshot.get("session_date") == session
         and snapshot.get("source_session_date") == session, "來源日期與股票交易日不一致")
    gate("ohlcv", snapshot.get("ohlcv_complete") is True,
         "來源完整性尚未驗證；既有 OHLC 不等於缺資料，需核對原始成交量與來源"
         if snapshot.get("attestation_status") == "pending" else
         "未確認真實完整 OHLCV，缺值代入的 K 線不能作進場依據")
    gate("calendar", timing["calendar_verified"], "官方交易日曆缺漏，禁止猜測交易日")
    gate("completed", timing["completed"], "尚無已完成的正式收盤")
    gate("freshness", timing["fresh"], "收盤快照已落後，等待最新完成交易日")
    price, close = row.get("price"), snapshot.get("close")
    gate("price", _positive(price) and _positive(close)
         and abs(float(price) - float(close)) <= max(0.01, abs(float(close)) * 0.00001)
         if _positive(price) and _positive(close) else False,
         "缺少可驗證收盤價，或現價與收盤基準不一致")
    gate("snapshot", source_coherent, "中央報告與分檔不是同一批次，等待重算")
    gate("core_data", not row.get("core_data_missing"), "關鍵資料缺漏")
    required_source = {"short": "short_plan", "medium": "medium_45d", "long": "long_6m"}.get(horizon)
    gate("evidence", not audit["invalid_source_ids"] and required_source in audit["used_source_ids"],
         "採用證據來源／市場／日期未通過核對")
    required = ["entry_low", "entry_high", "stop", "target1"]
    gate("levels", all(_positive(plan.get(k)) for k in required), "買進區／停損／目標關鍵價位不足")
    gate("expiry", timing["expires_at"] is not None, "無法以官方日曆確認失效時間")
    failures = [item["reason"] for item in gates if not item["passed"]]
    reasons = list(dict.fromkeys(failures + [str(v) for v in row.get("risk_blocks") or []]))
    code = "insufficient" if failures or plan.get("recommendation") == "data_insufficient" else "wait"
    if not failures:
        if row.get("risk_blocks") or plan.get("recommendation") == "avoid":
            code = "avoid"
        elif (plan.get("recommendation") == "can_scale"
              and (plan.get("plan_quality") or {}).get("entry_eligible") is True
              and not plan.get("no_buy_reason") and not row.get("unresolved_conflict_count")
              and float(plan["entry_low"]) <= float(price) <= float(plan["entry_high"])
              and float(plan["stop"]) < float(plan["entry_low"]) <= float(plan["entry_high"]) < float(plan["target1"])):
            code = "eligible"
    if plan.get("no_buy_reason"):
        reasons.append(str(plan["no_buy_reason"]))
    if row.get("unresolved_conflict_count"):
        reasons.append("仍有未解除的模型衝突")
    if not reasons:
        reasons.append({"eligible": "既有影子計畫與資料閘門通過，仍非實單建議", "wait": "等待買進區、既有計畫品質或確認條件", "avoid": "既有模型風險條件不合格", "insufficient": "資料不足，先不建立買進訊號"}[code])
    failed = {item["code"] for item in gates if not item["passed"]}
    status_code, status_label = "ready", "影子資料核對通過"
    if failed:
        if "completed" in failed and timing["calendar_verified"]:
            status_code, status_label = "market_not_closed", "等待正式收盤"
        elif "snapshot" in failed or "freshness" in failed:
            status_code, status_label = "stale_snapshot", "資料需更新"
        elif snapshot.get("attestation_status") == "pending" or "source" in failed:
            status_code, status_label = "source_attestation_pending", "來源完整性待驗證"
        elif "price" in failed and _positive(price) and _positive(close):
            status_code, status_label = "price_basis_mismatch", "價格基準待核對"
        elif "evidence" in failed:
            status_code, status_label = "evidence_review_pending", "證據時間待核對"
        else:
            status_code, status_label = "data_missing", "關鍵資料待補齊"
    return {
        "data_status": {"code": status_code, "label": status_label,
                        "detail": "；".join(failures)},
        "price_basis": {"reported_quote": price, "completed_close": close,
                        "aligned": "price" not in failed,
                        "note": "報告參考價與正式收盤分開列示；不以替換價格讓既有計畫通過"},
        "version": VERSION, "code": code, "label": LABELS[code], "horizon": horizon,
        "reasons": list(dict.fromkeys(reasons)), "as_of": session,
        "evaluated_at": now.astimezone(timezone.utc).isoformat(),
        "expires_at": timing["expires_at"], "valid_through_session": timing["valid_through_session"],
        "gates": gates, "evidence": audit,
        "applicability": {"market": market, "asset_type": "ETF" if etf else "STOCK",
                          "company_financials": "not_applicable" if etf else "existing_model_only",
                          "tw_institution": "existing_model_only" if market == "TW" and not etf else "not_applicable"},
        "validation": {"status": "pending", "formal_adoption_ready": False,
                       "label": "影子研究；尚需分市場／資產／週期的樣本外、前向成本與回撤驗證"},
        "shadow_only": True, "automatic_orders": False, "probability_pct": None,
    }


def input_evidence_categories(row: dict[str, Any]) -> list[dict[str, Any]]:
    """Expose existing inputs, not new factors or independently weighted votes."""
    market = str(row.get("market") or "")
    etf = "ETF" in str(row.get("type") or "").upper()
    snapshot = source_snapshot(row)
    session = row.get("official_session_date")
    def value(key):
        item = row.get(key)
        if isinstance(item, bool):
            return "是" if item else "否"
        if isinstance(item, (int, float)):
            return item if math.isfinite(item) else None
        return item if isinstance(item, str) and item.strip() else None
    def category(key, label, fields, *, applicable=True, available=True, as_of=None,
                 source=None, note=""):
        items = [{"key": field, "label": name, "value": value(field) if applicable and available else None}
                 for field, name in fields]
        return {"id": key, "label": label, "applicable": applicable, "as_of": as_of,
                "source": source, "note": note, "items": items, "additional_weight": 0,
                "status": "not_applicable" if not applicable else
                          "reference" if any(item["value"] is not None for item in items) else "missing"}
    categories = [
        category("daily_candle", "日 K 線與成交量", [
            ("official_open_price", "開盤"), ("official_high_price", "最高"),
            ("official_low_price", "最低"), ("official_close_price", "收盤"),
            ("official_volume", "成交股數"), ("kline_pattern", "K 線型態"),
            ("daily_volume_ratio", "日量比"), ("volume_price_pattern", "量價型態"),
        ], available=True, as_of=session, source=snapshot["source"],
                 note="原報告 OHLC 參考值；來源與真實成交量完成核對前不作合格進場依據。並非完整歷史圖表。"),
        category("price_indicators", "日線趨勢指標", [
            ("rsi", "日 RSI"), ("ma5", "MA5"), ("ma10", "MA10"),
            ("ma20", "MA20"), ("ma60", "MA60"), ("atr14", "ATR14"),
        ], as_of=session, source=snapshot["source"],
                 note="與 K 線共用價量輸入，僅解釋既有模型，不作多張獨立選票。有效性仍受主結論閘門限制。"),
        category("momentum_audit", "日／週 KD 與 MACD（隔離研究）", [
            ("chart_pattern_daily_k", "日 K"), ("chart_pattern_daily_d", "日 D"),
            ("chart_pattern_weekly_k", "週 K"), ("chart_pattern_weekly_d", "週 D"),
            ("chart_pattern_macd", "MACD"), ("chart_pattern_macd_signal", "訊號線"),
            ("chart_pattern_macd_histogram", "柱狀值"),
        ], as_of=session, source=snapshot["source"],
                 note="既有隔離型態研究，未獨立加入 V6 或本結論權重；週線可能尚未完成。週 RSI／週 MA 未接入，不補造。"),
        category("fundamentals", "公司財務與估值", [
            ("per", "本益比"), ("pbr", "股價淨值比"), ("revenue_yoy_pct", "營收年增％"),
            ("eps_yoy_pct", "EPS 年增％"), ("gross_margin_pct", "毛利率％"),
            ("operating_margin_pct", "營業利益率％"), ("roe_pct", "ROE％"),
            ("debt_ratio_pct", "負債比％"), ("financial_report_date", "財報期別日期"),
            ("revenue_date", "營收資料日期"),
        ], applicable=not etf, as_of=row.get("financial_report_date"),
                 source="／".join(dict.fromkeys(str(row[k]) for k in
                      ("financial_quality_source", "us_company_data_source", "us_sec_data_source") if row.get(k))) or None,
                 note="ETF 公司損益表不適用。個股各欄位可能混合原來源與 SEC 補缺；財報期別非當日驗證，快取時間未提供時不宣稱最新。"),
        category("sector_market", "產業分類與市場環境", [
            ("industry", "產業分類"), ("theme", "主題"),
            ("tw_sector_context_score", "台股族群背景分"),
            ("tw_market_context_score", "台股大盤背景分"), ("group_score", "既有群組分"),
            ("macro_score", "既有總體環境分"),
        ], as_of=row.get("tw_market_context_session_date") if market == "TW" else None,
                 source=row.get("tw_market_context_source") if market == "TW" else None,
                 note="既有分類／市場背景參考；群組分不等於完整產業基本面研究，不在此重複加權。"),
        category("liquidity", "既有流動性參考", [
            ("avg_volume20", "20日均量"), ("us_live_spread_pct", "美股買賣價差％"),
            ("us_live_quote_imbalance_pct", "美股報價量失衡％"),
            ("etf_liquidity_score", "ETF既有流動性分"),
        ], as_of=row.get("us_live_fetched_at") if market == "US" else session,
                 source=row.get("us_live_source") if market == "US" else snapshot["source"],
                 note="沿用既有資料，不另建指標或重複加權；抓取時間不等於可成交報價，缺值不補零。"),
        category("tw_institution", "台股法人籌碼", [
            ("institution_net", "三大法人淨買賣股數"), ("foreign_net", "外資股數"),
            ("trust_net", "投信股數"), ("dealer_net", "自營商股數"),
        ], applicable=market == "TW" and not etf, available=row.get("institution_available") is True,
                 as_of=row.get("institution_date") if market == "TW" else None,
                 source=row.get("institution_source") if market == "TW" else None,
                 note="仍須通過中央日期／來源／涵蓋率核對；已計入的法人不再加分，美股不套用。"),
        category("news_risk", "事件與新聞風險", [
            ("news_data_available", "新聞資料可得"), ("news_verified", "既有風險核對"),
            ("news_risk_level", "風險等級"), ("news_summary", "摘要"),
        ], as_of=row.get("news_scanned_at"), source="既有新聞風險模組" if row.get("news_scanned_at") else None,
                 note="掃描時間不等於事件發生時間；未發現負面新聞不代表沒有風險。來源核對仍由原新聞規則負責。"),
    ]
    if market == "US" and snapshot.get("source") == "Yahoo Finance daily bars":
        for item in categories[:3]:
            item["note"] = "既有 Yahoo 來源診斷，不用於本結論進場資格；未新增資料請求。" + item["note"]
    if not snapshot["ohlcv_complete"]:
        categories[0]["status"] = "unverified"
        for item in categories[0]["items"]:
            if item["key"] in {"official_volume", "kline_pattern", "daily_volume_ratio", "volume_price_pattern"}:
                item["value"] = None
    return categories
