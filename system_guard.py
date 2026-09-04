"""Independent health guard for the stock assistant reporting pipeline.

The guard only observes report freshness, consistency, coverage and publish
outcomes.  It never changes rankings, model weights or trading settings.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests


TAIPEI = ZoneInfo("Asia/Taipei")
LEVEL_ORDER = {"ok": 0, "info": 0, "warning": 1, "critical": 2}
LEVEL_LABEL = {"ok": "正常", "info": "資訊", "warning": "注意", "critical": "異常"}
DEFAULT_PRIMARY_APP_URL = "https://a0953883367.github.io/wude-ai-stock-v6/"
DEFAULT_LIVE_HEALTH_URL = "https://wude-ai-stock-v6-production.up.railway.app/health"


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def _parse_taipei(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=TAIPEI) if parsed.tzinfo is None else parsed.astimezone(TAIPEI)


def _check(code: str, title: str, level: str, detail: str, action: str = "") -> dict[str, Any]:
    return {
        "code": code,
        "title": title,
        "level": level,
        "label": LEVEL_LABEL[level],
        "detail": detail,
        "action": action,
    }


def _http_probe(url: str, *, expect_json: bool = False, timeout: float = 10.0) -> dict[str, Any]:
    """Probe a public runtime endpoint without sending credentials."""
    checked_url = str(url or "").strip()
    if not checked_url:
        return {"probe_ok": False, "error": "網址未設定", "url": ""}
    try:
        response = requests.get(checked_url, timeout=timeout)
        response.raise_for_status()
        result: dict[str, Any] = {
            "probe_ok": True,
            "status_code": response.status_code,
            "url": checked_url,
        }
        if expect_json:
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("回傳內容不是 JSON 物件")
            result["payload"] = payload
        return result
    except (requests.RequestException, ValueError, TypeError) as exc:
        return {
            "probe_ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "url": checked_url,
        }


def _publish_check(name: str, outcome: str, previous: dict[str, Any]) -> dict[str, Any]:
    value = str(outcome or "unknown").strip().lower()
    code = f"publish_{name}"
    title = "朋友版同步" if name == "friend" else "舊本人版同步（非主 App）"
    # The owner's primary entry is now the GitHub Pages home-screen App. The
    # former ChatGPT Sites owner snapshot remains only as a legacy backup, so a
    # missing device grant or failed legacy publish must never turn the report
    # pipeline, Railway stream or formal ranking red.
    if name == "owner":
        if value == "success":
            detail = "舊入口最近一次同步成功；主 App 狀態另行判斷"
        elif value in {"unknown", ""}:
            detail = "本次未檢查舊入口；不影響主 App、Railway、報表或排名"
        else:
            detail = f"舊入口同步結果：{value}；已與主 App 及正式計算隔離"
        return _check(code, title, "info", detail)
    if value in {"unknown", ""}:
        old = next((item for item in previous.get("checks", []) if item.get("code") == code), None)
        if old:
            return deepcopy(old)
        return _check(code, title, "info", "尚未取得這次發布結果", "等待下一次股票報告排程")
    if value == "success":
        return _check(code, title, "ok", "最近一次發布已成功")
    if value == "skipped":
        return _check(code, title, "warning", "最近一次發布步驟被略過", "檢查網站網址與發布授權是否已設定")
    return _check(
        code, title, "warning", f"最近一次發布結果：{value}；主 App 與正式報表維持獨立",
        "檢查朋友版發布狀態與授權；不要把主 App、Railway 或正式排名一起判成失敗",
    )


def _primary_app_check(
    reports_dir: Path,
    runtime_probe: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Check the primary PWA without conflating a single device's grant."""
    root = reports_dir.parent
    required = (
        "index.html",
        "live-flow.html",
        "decision-hub.html",
        "inverse-etf-shadow.html",
        "valuation-risk-shadow.html",
        "app_shell.js",
    )
    missing = [name for name in required if not (root / name).is_file()]
    if missing:
        return _check(
            "primary_app_output",
            "主畫面 App",
            "warning",
            "缺少主 App 檔案：" + "、".join(missing),
            "修復 GitHub Pages App 檔案後重新部署；Railway、Telegram 與正式排名維持獨立",
        )
    if runtime_probe is not None and not runtime_probe.get("probe_ok"):
        return _check(
            "primary_app_output",
            "主畫面 App",
            "warning",
            f"主 App 檔案完整，但公開網址無法開啟：{runtime_probe.get('error') or '未知錯誤'}",
            "檢查 GitHub Pages 發布；不要把手機授權或 Railway 後端一起判成故障",
        )
    return _check(
        "primary_app_output",
        "主畫面 App",
        "ok",
        "主 App 公開網址可開啟，五個功能頁與共用導覽檔完整"
        if runtime_probe is not None
        else "五個功能頁與共用導覽檔完整；本次未執行公開網址探測",
    )


def _live_runtime_checks(
    runtime_probe: dict[str, Any] | None,
    previous: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Keep Railway, each stream, Telegram and device pairing independent."""
    titles = {"TW": "台股即時串流", "US": "美股即時串流"}
    if runtime_probe is None:
        return [
            _check("live_backend", "Railway 即時後端", "info", "本次未執行 Railway 健康探測"),
            *[
                _check(f"stream_{market.lower()}", title, "info", "本次未取得即時串流狀態")
                for market, title in titles.items()
            ],
            _check("telegram_delivery", "Telegram 即時通知", "info", "本次未取得通知送達狀態"),
            _check("device_authorization", "手機唯讀授權", "info", "本次未取得手機授權服務狀態"),
        ]

    if not runtime_probe.get("probe_ok"):
        error = str(runtime_probe.get("error") or "未知錯誤")
        old = next(
            (
                item for item in (previous or {}).get("checks", [])
                if item.get("code") == "live_backend"
            ),
            {},
        )
        consecutive = int(old.get("consecutive_failures") or 0) + 1
        backend_level = "critical" if consecutive >= 3 else "warning"
        backend = _check(
            "live_backend", "Railway 即時後端", backend_level,
            f"Railway 健康網址連續 {consecutive} 次無法連線：{error}",
            "檢查 Railway 服務、部署紀錄與公開網域；正式收盤報表仍由 GitHub Actions 分開判斷",
        )
        backend["consecutive_failures"] = consecutive
        return [
            backend,
            *[
                _check(
                    f"stream_{market.lower()}", title, "info",
                    "因 Railway 無法連線，本次不能判定串流；不誤標成個別行情來源故障",
                )
                for market, title in titles.items()
            ],
            _check(
                "telegram_delivery", "Telegram 即時通知", "info",
                "因 Railway 無法連線，本次不能判定 Telegram；不影響正式報表保存",
            ),
            _check(
                "device_authorization", "手機唯讀授權", "info",
                "因 Railway 無法連線，本次不能判定授權服務；不代表正式排名失敗",
            ),
        ]

    payload = runtime_probe.get("payload")
    if not isinstance(payload, dict) or not payload.get("ok"):
        return [
            _check(
                "live_backend", "Railway 即時後端", "critical",
                "Railway 有回應，但健康內容無效",
                "檢查 Railway /health 回傳；不要用手機畫面狀態代替後端健康狀態",
            ),
            *[
                _check(f"stream_{market.lower()}", title, "info", "後端健康內容無效，暫不判定串流")
                for market, title in titles.items()
            ],
            _check("telegram_delivery", "Telegram 即時通知", "info", "後端健康內容無效，暫不判定送達"),
            _check("device_authorization", "手機唯讀授權", "info", "後端健康內容無效，暫不判定授權服務"),
        ]

    backend = _check(
        "live_backend", "Railway 即時後端", "ok",
        f"{payload.get('service') or '即時服務'} 正常回應；與 App 畫面及手機授權分開判斷",
    )
    backend["consecutive_failures"] = 0
    checks = [backend]
    monitor = payload.get("large_buy_monitor") if isinstance(payload.get("large_buy_monitor"), dict) else {}
    streams = monitor.get("streams") if isinstance(monitor.get("streams"), dict) else {}
    enabled = bool(monitor.get("enabled", True))
    for market, title in titles.items():
        row = streams.get(market) if isinstance(streams.get(market), dict) else {}
        state = str(row.get("state") or "unknown")
        subscribed = int(row.get("subscribed") or 0)
        error = str(row.get("error") or "")
        if not enabled or state == "disabled":
            checks.append(_check(
                f"stream_{market.lower()}", title, "warning",
                "即時大量買賣監控已停用",
                "檢查 LARGE_BUY_MONITOR_ENABLED；不修改正式排名或收盤報表",
            ))
        elif state in {"connected", "premarket_connected"} and subscribed > 0:
            phase = "盤前" if state == "premarket_connected" else "正式盤"
            checks.append(_check(
                f"stream_{market.lower()}", title, "ok",
                f"{phase}已連線，訂閱 {subscribed} 檔",
            ))
        elif state == "waiting_market_open":
            checks.append(_check(
                f"stream_{market.lower()}", title, "info",
                "市場未開盤，串流正在等待；不視為更新或故障",
            ))
        else:
            detail = f"串流狀態：{state}"
            if error:
                detail += f"；{error}"
            checks.append(_check(
                f"stream_{market.lower()}", title, "warning", detail,
                "檢查對應行情授權與重新連線紀錄；另一市場、正式報表及排名維持獨立",
            ))

    telegram = monitor.get("telegram_delivery") if isinstance(monitor.get("telegram_delivery"), dict) else {}
    configured = bool(telegram.get("configured", monitor.get("telegram_configured", False)))
    telegram_state = str(telegram.get("state") or "")
    last_success = str(telegram.get("last_success_at") or "")
    last_error = str(telegram.get("last_error") or "")
    if not configured:
        checks.append(_check(
            "telegram_delivery", "Telegram 即時通知", "warning",
            "即時通知尚未設定；網站訊號與後端統計仍繼續",
            "在 Railway 設定專用 Telegram Bot，勿把 Token 寫入 GitHub 或 App",
        ))
    elif telegram_state == "delivery_failed" or last_error:
        checks.append(_check(
            "telegram_delivery", "Telegram 即時通知", "warning",
            f"最近一次通知未送達：{last_error or '未知錯誤'}；後端與串流未停止",
            "檢查專用 Bot 對話與 Railway Telegram 設定",
        ))
    elif telegram_state == "delivered" or last_success:
        checks.append(_check(
            "telegram_delivery", "Telegram 即時通知", "ok",
            f"最近一次已送達：{last_success or '時間未提供'}",
        ))
    else:
        checks.append(_check(
            "telegram_delivery", "Telegram 即時通知", "warning",
            "已設定，但尚未取得成功送達紀錄；後端與串流未停止",
            "確認專用 Bot 已按 /start，等待連線確認訊息",
        ))

    if payload.get("device_pairing_configured"):
        checks.append(_check(
            "device_authorization", "手機唯讀授權", "ok",
            "手機驗證服務已設定；單支手機權杖失效只影響該裝置",
        ))
    else:
        checks.append(_check(
            "device_authorization", "手機唯讀授權", "info",
            "手機驗證服務尚未完整設定；不影響 Railway、串流、正式報表或排名",
            "需要手機即時頁時再完成唯讀授權設定",
        ))
    return checks


def _cohort_name(row: dict[str, Any]) -> str:
    market = str(row.get("market") or "").upper()
    asset = "ETF" if "ETF" in str(row.get("type") or "").upper() else "STOCK"
    return f"{market}_{asset}" if market in {"TW", "US"} else ""


def _market_session_check(rows: list[dict[str, Any]]) -> dict[str, Any]:
    cohorts: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        cohort = _cohort_name(row)
        if cohort:
            cohorts.setdefault(cohort, []).append(row)
    issues: list[str] = []
    summaries: list[str] = []
    for cohort, cohort_rows in sorted(cohorts.items()):
        sessions = [
            str(row.get("official_session_date") or "")
            for row in cohort_rows
            if row.get("official_session_date")
        ]
        if not sessions:
            issues.append(f"{cohort} 全部缺少交易日")
            continue
        session, count = Counter(sessions).most_common(1)[0]
        coverage = count / len(cohort_rows)
        summaries.append(f"{cohort} {session} {count}/{len(cohort_rows)}")
        if coverage < 0.9:
            issues.append(
                f"{cohort} 主交易日 {session} 僅 {count}/{len(cohort_rows)}（{coverage:.1%}）"
            )
    if not cohorts:
        return _check(
            "market_session_consistency", "市場交易日一致性", "critical",
            "完整分析中沒有可辨識的台股、美股或 ETF 資料",
            "停止使用本次排名並重新產生完整報表",
        )
    if issues:
        return _check(
            "market_session_consistency", "市場交易日一致性", "critical",
            "；".join(issues),
            "停止使用混日排名；檢查行情來源後重新執行完整報表",
        )
    return _check(
        "market_session_consistency", "市場交易日一致性", "ok", "；".join(summaries)
    )


def _holding_valuation_check(holding: dict[str, Any]) -> dict[str, Any]:
    if not holding:
        return _check(
            "holding_valuation_consistency", "持有估值日期", "critical",
            "找不到 holding_simulation.json 或內容無法讀取",
            "不要依持有損益調整部位；重新產生完整持有模擬",
        )
    issues: list[str] = []
    medium = holding.get("medium") if isinstance(holding.get("medium"), dict) else {}
    for market in ("TW", "US"):
        portfolio = medium.get(market) if isinstance(medium.get(market), dict) else {}
        positions = portfolio.get("positions") if isinstance(portfolio.get("positions"), list) else []
        benchmark_positions = (
            portfolio.get("benchmark_positions")
            if isinstance(portfolio.get("benchmark_positions"), list)
            else []
        )
        position_dates = sorted({
            str(position.get("last_valuation_date") or "")
            for position in positions
            if isinstance(position, dict) and position.get("last_valuation_date")
        })
        benchmark_dates = sorted({
            str(position.get("last_valuation_date") or "")
            for position in benchmark_positions
            if isinstance(position, dict) and position.get("last_valuation_date")
        })
        dates = sorted(set(position_dates + benchmark_dates))
        portfolio_date = str(portfolio.get("last_valuation_date") or "")
        if positions and not position_dates:
            issues.append(f"中期 {market} 持倉全部缺少估值日")
        elif positions and not benchmark_positions:
            issues.append(f"中期 {market} 缺少同期基準持倉")
        elif benchmark_positions and not benchmark_dates:
            issues.append(f"中期 {market} 同期基準缺少估值日")
        elif len(dates) > 1:
            issues.append(f"中期 {market} 持倉與基準估值日混雜：{','.join(dates)}")
        if len(dates) == 1 and portfolio_date != dates[0]:
            issues.append(
                f"中期 {market} 組合估值日 {portfolio_date or '缺少'}，共同估值日為 {dates[0]}"
            )

    long_portfolio = holding.get("long") if isinstance(holding.get("long"), dict) else {}
    long_positions = (
        long_portfolio.get("positions")
        if isinstance(long_portfolio.get("positions"), list)
        else []
    )
    long_dates = (
        long_portfolio.get("last_valuation_date")
        if isinstance(long_portfolio.get("last_valuation_date"), dict)
        else {}
    )
    for market in ("TW", "US"):
        position_dates = sorted({
            str(position.get("last_valuation_date") or "")
            for position in long_positions
            if isinstance(position, dict)
            and str(position.get("market") or "").upper() == market
            and position.get("last_valuation_date")
        })
        benchmark_dates = sorted({
            str(position.get("last_valuation_date") or "")
            for position in long_portfolio.get("benchmark_positions", [])
            if isinstance(position, dict)
            and str(position.get("market") or "").upper() == market
            and position.get("last_valuation_date")
        })
        dates = sorted(set(position_dates + benchmark_dates))
        if position_dates and not benchmark_dates:
            issues.append(f"長期 {market} 缺少同期基準估值日")
        elif len(dates) > 1:
            issues.append(f"長期 {market} 持倉與基準估值日混雜：{','.join(dates)}")
        recorded = str(long_dates.get(market) or "")
        if len(dates) == 1 and recorded != dates[0]:
            issues.append(
                f"長期 {market} 組合估值日 {recorded or '缺少'}，共同估值日為 {dates[0]}"
            )
    if issues:
        return _check(
            "holding_valuation_consistency", "持有估值日期", "critical",
            "；".join(issues),
            "不要依本次持有損益交易；修復資料日期後重新驗證",
        )
    return _check(
        "holding_valuation_consistency", "持有估值日期", "ok",
        "中期與長期持倉估值日期一致且未發現內部倒退",
    )


def build_guard(
    reports_dir: Path,
    *,
    now: datetime | None = None,
    friend_publish: str = "unknown",
    owner_publish: str = "unknown",
    primary_app_probe: dict[str, Any] | None = None,
    live_runtime_probe: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = now.astimezone(TAIPEI) if now and now.tzinfo else (now.replace(tzinfo=TAIPEI) if now else datetime.now(TAIPEI))
    previous = _load(reports_dir / "system_guard.json")
    latest = _load(reports_dir / "latest.json")
    rankings = _load(reports_dir / "rankings.json")
    all_analysis = _load(reports_dir / "all_analysis.json")
    holding = _load(reports_dir / "holding_simulation.json")
    rotation_health = _load(reports_dir / "market_rotation_shadow_health.json")
    valuation_health = _load(reports_dir / "valuation_risk_shadow_health.json")
    decision_health = _load(reports_dir / "decision_hub_health.json")
    stockq = _load(reports_dir / "stockq_market_context.json")
    validation_60d = _load(reports_dir / "validation_60d.json")
    validation_progress = _load(reports_dir / "validation_progress_monitor.json")
    model_learning = _load(reports_dir / "model_learning.json")
    model_unit_learning = _load(reports_dir / "model_unit_learning.json")
    prediction_engine = _load(reports_dir / "prediction_engine.json")
    forward_outcomes = _load(reports_dir / "forward_outcome_ledger.json")
    graduation = _load(reports_dir / "model_graduation.json")
    unified_evidence = _load(reports_dir / "unified_evidence.json")
    official_financial = _load(reports_dir / "tw_financial_official_cache.json")
    checks: list[dict[str, Any]] = []

    updated = _parse_taipei(latest.get("updated_at"))
    if not updated:
        age_minutes = None
        checks.append(_check("report_freshness", "股票資料更新", "critical", "找不到可辨識的最新報表時間", "重新執行股票報告排程"))
    else:
        age_minutes = round((now - updated).total_seconds() / 60, 1)
        if age_minutes < -10:
            checks.append(_check("report_freshness", "股票資料更新", "warning", "報表時間晚於系統時間", "檢查執行設備的日期、時區與時間同步"))
        elif age_minutes > 720:
            checks.append(_check("report_freshness", "股票資料更新", "critical", f"已 {age_minutes / 60:.1f} 小時沒有完成新報表", "檢查 GitHub Actions、電腦排程及上游行情來源"))
        elif age_minutes > 270:
            checks.append(_check("report_freshness", "股票資料更新", "warning", f"最近更新距今 {age_minutes / 60:.1f} 小時", "若已跨過下一個排程時間，檢查排程是否延遲"))
        else:
            checks.append(_check("report_freshness", "股票資料更新", "ok", f"最近更新距今 {max(0, age_minutes):.0f} 分鐘"))

    timestamps = {
        "latest": str(latest.get("updated_at") or ""),
        "rankings": str(rankings.get("updated_at") or ""),
        "all_analysis": str(all_analysis.get("updated_at") or ""),
    }
    present_times = [value for value in timestamps.values() if value]
    if len(present_times) != 3:
        checks.append(_check("report_consistency", "三份報表一致性", "critical", "latest、rankings 或 all_analysis 有檔案／時間缺失", "重新產生完整報表，禁止只補單一檔案"))
    elif len(set(present_times)) != 1:
        checks.append(_check("report_consistency", "三份報表一致性", "critical", "三份報表不是同一次完整運算結果", "停止使用混合資料並重新執行完整報告"))
    else:
        checks.append(_check("report_consistency", "三份報表一致性", "ok", "排行、完整分析與首頁報表時間一致"))

    # all_analysis candidate_count includes fixed watchlists and the background
    # universe, so it is the denominator that matches analyzed_count.
    universe = int(all_analysis.get("candidate_count") or latest.get("universe_count") or 0)
    analyzed = int(latest.get("analyzed_count") or all_analysis.get("analyzed_count") or 0)
    ranking_rows = rankings.get("data") if isinstance(rankings.get("data"), list) else []
    analysis_rows = all_analysis.get("data") if isinstance(all_analysis.get("data"), list) else []
    ratio = analyzed / universe if universe > 0 else 0
    if analyzed <= 0 or not ranking_rows or not analysis_rows:
        checks.append(_check("analysis_output", "分析輸出", "critical", "排名或完整分析結果為空", "不要使用本次結果；檢查行情抓取與 briefing.py 執行紀錄"))
    elif universe and ratio < 0.5:
        checks.append(_check("analysis_output", "分析輸出", "critical", f"只完成 {analyzed}/{universe} 檔分析", "檢查上游行情大量失敗或排程逾時"))
    elif universe and ratio < 0.8:
        checks.append(_check("analysis_output", "分析輸出", "warning", f"完成 {analyzed}/{universe} 檔分析，涵蓋率偏低", "檢查 unavailable 清單與行情來源"))
    else:
        checks.append(_check("analysis_output", "分析輸出", "ok", f"完成 {analyzed}/{universe or analyzed} 檔；排行 {len(ranking_rows)} 檔"))

    checks.append(_market_session_check(analysis_rows))
    checks.append(_holding_valuation_check(holding))

    data_status = latest.get("data_status") if isinstance(latest.get("data_status"), dict) else {}
    expected_tw = int(data_status.get("expected_tw_count") or 0)
    official_prices = int(data_status.get("tw_official_price_count") or 0)
    institutions = int(data_status.get("institutional_count") or data_status.get("tw_official_institution_count") or 0)
    credit = int(data_status.get("credit_count") or data_status.get("tw_official_credit_count") or 0)

    # A non-trading-day report deliberately does not query the official Taiwan
    # endpoints again.  The completed-session rows still carry the validated
    # official close, institution and credit snapshot, so treating the current
    # fetch count of zero as missing core data creates a false red alert on
    # weekends and holidays.  Count the usable carried snapshot as coverage;
    # market-session consistency above separately rejects mixed or missing dates.
    tw_rows = [row for row in analysis_rows if str(row.get("market") or "").upper() == "TW"]
    carried_prices = sum(
        bool(row.get("official_session_date"))
        and float(row.get("official_close_price") or 0) > 0
        for row in tw_rows
    )
    carried_institutions = sum(bool(row.get("institution_available")) for row in tw_rows)
    carried_credit = sum(float(row.get("credit_available") or 0) > 0 for row in tw_rows)
    usable_prices = max(official_prices, carried_prices)
    usable_institutions = max(institutions, carried_institutions)
    usable_credit = max(credit, carried_credit)
    carried_note = "（沿用最近完整收盤）" if official_prices == 0 and carried_prices else ""
    if expected_tw and min(usable_prices, usable_institutions, usable_credit) == 0:
        checks.append(_check("tw_core_data", "台股核心資料", "critical", f"預期 {expected_tw} 檔，但價格／法人／信用資料至少一項為0", "檢查 FinMind、官方資料源與當日市場狀態"))
    elif expected_tw and min(usable_prices, usable_institutions, usable_credit) < expected_tw * 0.8:
        checks.append(_check("tw_core_data", "台股核心資料", "warning", f"價格 {usable_prices}、法人 {usable_institutions}、信用 {usable_credit}；預期 {expected_tw}{carried_note}", "缺資料股票不得取得完整買進資格"))
    else:
        checks.append(_check("tw_core_data", "台股核心資料", "ok", f"價格 {usable_prices}、法人 {usable_institutions}、信用 {usable_credit}{carried_note}"))

    broker = int(data_status.get("broker_count") or 0)
    if broker <= 0:
        checks.append(_check(
            "broker_data", "券商分點", "info",
            "FinMind Sponsor 分點目前未回傳；未以其他資料冒充",
            "每次正式報告保留重試；需合法 Sponsor／資料授權才可完整取得",
        ))
    else:
        checks.append(_check("broker_data", "券商分點", "ok", f"已取得 {broker} 檔分點資料"))

    financial = int(data_status.get("financial_quality_count") or 0)
    watchlist_rows = (
        latest.get("watchlist") if isinstance(latest.get("watchlist"), list) else []
    )
    derived_financial_expected = sum(
        str(row.get("market") or "").upper() == "TW"
        and "ETF" not in str(row.get("type") or "").upper()
        for row in watchlist_rows
        if isinstance(row, dict)
    )
    financial_expected = int(
        data_status.get("expected_financial_quality_count")
        or derived_financial_expected
        or expected_tw
        or 0
    )
    financial_not_applicable = int(
        data_status.get("financial_quality_not_applicable_count")
        or max(0, expected_tw - financial_expected)
        or 0
    )
    if financial_expected and financial < financial_expected:
        checks.append(_check(
            "financial_quality", "財務品質", "warning",
            f"已取得 {financial}/{financial_expected} 檔公司，仍在分批補齊",
            "每次排程續補；只降低6個月信心，不把整檔標成資料不足",
        ))
    else:
        suffix = (
            f"；另有 {financial_not_applicable} 檔 ETF 不適用公司財報"
            if financial_not_applicable else ""
        )
        checks.append(_check(
            "financial_quality", "財務品質", "ok",
            f"已取得 {financial}/{financial_expected or financial} 檔公司{suffix}",
        ))

    official_requested = int(official_financial.get("requested_count") or 0)
    official_available = int(official_financial.get("available_count") or 0)
    official_coverage = float(official_financial.get("coverage_pct") or 0)
    if not official_financial:
        checks.append(_check(
            "tw_official_financial", "台股官方財報", "warning",
            "尚未建立交易所／櫃買中心官方財報快取",
            "下一次完整報告自動重抓；缺值不以其他數字冒充",
        ))
    elif official_requested and official_coverage < 95:
        checks.append(_check(
            "tw_official_financial", "台股官方財報", "warning",
            f"官方財報 {official_available}/{official_requested}（{official_coverage:.2f}%）",
            "持續從 TWSE／TPEx 補抓；未申報股票只降低長期信心",
        ))
    else:
        checks.append(_check(
            "tw_official_financial", "台股官方財報", "ok",
            f"官方財報 {official_available}/{official_requested}（{official_coverage:.2f}%）",
        ))

    if stockq.get("status") in {"ok", "stale_cache", "stale_fallback"} and int(stockq.get("indicator_count") or 0) > 0:
        checks.append(_check(
            "stockq_context", "StockQ 收盤後備援", "ok",
            f"已取得 {int(stockq.get('indicator_count') or 0)} 項全球指標；只在主要來源缺值時補入",
        ))
    else:
        checks.append(_check(
            "stockq_context", "StockQ 收盤後備援", "warning",
            "StockQ 指標暫時不可用；不影響個股資料與正式排名",
            "等待收盤後排程自動重試，盤中只沿用三日內快取",
        ))

    tiingo_configured = bool(data_status.get("tiingo_us_close_configured"))
    tiingo_status = str(data_status.get("tiingo_us_close_status") or "")
    tiingo_covered = int(data_status.get("tiingo_us_close_covered_count") or 0)
    tiingo_staging = int(data_status.get("tiingo_us_close_staging_count") or 0)
    if tiingo_status in {"ok", "stale_fallback"} and tiingo_covered:
        checks.append(_check(
            "tiingo_us_close", "Tiingo 免費版覆蓋測試", "ok",
            f"私密完整快取涵蓋 {tiingo_covered} 檔；原始價格未進入網站或計算",
        ))
    elif tiingo_configured and tiingo_status == "collecting":
        checks.append(_check(
            "tiingo_us_close", "Tiingo 免費版覆蓋測試", "info",
            f"免費版分批收集中，目前已嘗試 {tiingo_staging} 檔；尚未切換半套快取",
            "後續固定報告會自動續抓；只測涵蓋率，不影響正式排名與結算",
        ))
    elif tiingo_configured:
        checks.append(_check(
            "tiingo_us_close", "Tiingo 免費版覆蓋測試", "info",
            "本次免費備援未取得完整快取；Yahoo／StockQ與最後完整值仍照常運作",
            "等待下一次美股收盤後排程自動重試",
        ))
    else:
        checks.append(_check(
            "tiingo_us_close", "Tiingo 免費版覆蓋測試", "info",
            "尚未設定免費 API key；此為選用備援，不影響整體報告",
            "需要啟用時在 GitHub Actions Secrets 設定 TIINGO_API_KEY",
        ))

    sec_available = int(data_status.get("us_sec_company_count") or 0)
    sec_used = int(data_status.get("us_sec_fallback_count") or 0)
    if sec_available:
        checks.append(_check(
            "us_sec_fundamentals", "SEC 美股財報備援", "ok",
            f"官方財報可用 {sec_available} 檔；本次實際補值 {sec_used} 檔",
        ))
    else:
        checks.append(_check(
            "us_sec_fundamentals", "SEC 美股財報備援", "info",
            "本次沒有需要SEC補值的美股，或免費官方快取尚待建立",
            "只在美股個股財報缺值時分批啟用，不影響價格結算",
        ))

    if not validation_60d:
        checks.append(_check("validation_60d", "60日向前驗證", "warning", "尚未建立統一60日進度檔", "下一次報告自動重建"))
    elif validation_progress.get("status") in {"warning", "critical"}:
        progress_level = str(validation_progress["status"])
        stalled = [
            f"{market} 連續 {int(row.get('stalled_sessions') or 0)} 次"
            for market, row in (validation_progress.get("markets") or {}).items()
            if isinstance(row, dict) and row.get("status") in {"warning", "critical"}
        ]
        signal_stalled = [
            f"{market}交易訊號連續 {int(row.get('stagnant_sessions') or 0)} 日未增加"
            for market, row in (validation_progress.get("signal_health") or {}).items()
            if isinstance(row, dict) and row.get("status") in {"warning", "critical"}
        ]
        issues = stalled + signal_stalled
        checks.append(_check(
            "validation_60d", "60日向前驗證", progress_level,
            f"真實交易日 {int(validation_60d.get('trading_days_collected') or 0)}/{int(validation_60d.get('target_trading_days') or 60)}；"
            + "、".join(issues),
            "檢查完成交易日、隔離紀錄、買點門檻與資料契約；只啟動影子診斷，不自動修改模型、權重或正式排名",
        ))
    elif validation_progress:
        market_parts = [
            f"{market} {row.get('last_session_date') or '待建立'}／{int(row.get('last_completed_days') or 0)}天"
            for market, row in (validation_progress.get("markets") or {}).items()
            if isinstance(row, dict)
        ]
        suffix = f"；{'、'.join(market_parts)}" if market_parts else ""
        checks.append(_check(
            "validation_60d", "60日向前驗證", "ok",
            f"真實交易日 {int(validation_60d.get('trading_days_collected') or 0)}/{int(validation_60d.get('target_trading_days') or 60)}；進度監控正常{suffix}",
        ))
    else:
        checks.append(_check(
            "validation_60d", "60日向前驗證", "info",
            f"真實交易日 {int(validation_60d.get('trading_days_collected') or 0)}/{int(validation_60d.get('target_trading_days') or 60)}；等待下一次固定報告建立自動進度基準",
        ))

    if graduation.get("status") == "ready":
        checks.append(_check("model_graduation", "正式V6畢業控制器", "ok", "正式V6畢業仍須人工；隔離影子模型可依守門規則自動升級或退版"))
    else:
        checks.append(_check("model_graduation", "模型畢業控制器", "warning", "尚未產生完整畢業結論", "下一次報告自動重建"))

    learning_policy = model_learning.get("policy") or {}
    learning_schema = int(model_learning.get("schema_version") or 0)
    learning_coverage = float(
        (((model_learning.get("complete_learning") or {}).get("summary") or {}).get("learning_governance_coverage_pct") or 0)
    )
    if (
        learning_schema >= 1
        and learning_policy.get("formal_v6_frozen") is True
        and learning_policy.get("automatic_merge") is False
        and learning_policy.get("broker_orders") is False
        and (learning_schema == 1 or learning_coverage == 100.0)
    ):
        learning = model_learning.get("error_learning") or {}
        candidates = model_learning.get("shadow_candidates") or []
        checks.append(_check(
            "model_learning", "錯題學習／影子成長", "ok",
            f"已將 {int(learning.get('raw_error_rows') or 0)} 筆錯誤列合併為 "
            f"{int(learning.get('independent_events') or 0)} 個事件；影子候選 {len(candidates)} 組；"
            f"完整學習治理 {int((((model_learning.get('complete_learning') or {}).get('summary') or {}).get('connected_units') or 0))}/"
            f"{int((((model_learning.get('complete_learning') or {}).get('summary') or {}).get('registered_units') or 0))} 項，正式V6鎖定",
        ))
    elif model_learning:
        checks.append(_check(
            "model_learning", "錯題學習／影子成長", "critical",
            "模型成長報告缺少正式V6隔離保證",
            "停止採用該學習報告；不得修改正式排名、權重或下單",
        ))
    else:
        checks.append(_check(
            "model_learning", "錯題學習／影子成長", "warning",
            "尚未建立錯題學習報告；正式V6不受影響",
            "下一次台股晚報或美股早報自動重建",
        ))

    unit_summary = model_unit_learning.get("summary") or {}
    unit_policy = model_unit_learning.get("policy") or {}
    if (
        model_unit_learning.get("status") == "ready"
        and int(unit_summary.get("dedicated_ledger_units") or 0) == 11
        and unit_policy.get("formal_v6_unchanged") is True
        and unit_policy.get("automatic_orders") is False
    ):
        recent_material = [
            event for event in (model_unit_learning.get("recent_events") or [])
            if isinstance(event, dict) and event.get("event") in {"promoted", "updated", "rolled_back"}
        ]
        checks.append(_check(
            "model_unit_learning", "11個證據單元獨立學習", "ok",
            f"專屬帳本 11/11；已成熟 {int(unit_summary.get('matured_rows') or 0)} 筆；"
            f"啟用影子信任 {int(unit_summary.get('active_shadow_trust_streams') or 0)} 路；"
            f"近期升級／退版事件 {len(recent_material)} 件，正式V6未變更",
        ))
    elif model_unit_learning:
        checks.append(_check(
            "model_unit_learning", "11個證據單元獨立學習", "critical",
            "單元帳本不完整或缺少正式V6隔離保證",
            "停用中央影子信任調整；正式V6繼續鎖定",
        ))
    else:
        checks.append(_check(
            "model_unit_learning", "11個證據單元獨立學習", "warning",
            "尚未建立11個證據單元的獨立向前帳本；正式V6不受影響",
            "下一次固定報告自動建立，不回填歷史答案",
        ))

    competition = ((prediction_engine.get("run_summary") or {}).get("model_competition") or {})
    if prediction_engine:
        checks.append(_check(
            "shadow_model_competition", "多期間影子自動升退版", "ok",
            f"本次升級 {int(competition.get('controlled_shadow_promotions') or 0)}、"
            f"退版 {int(competition.get('controlled_shadow_rollbacks') or 0)}；正式V6升級 0",
        ))
    else:
        checks.append(_check(
            "shadow_model_competition", "多期間影子自動升退版", "info",
            "獨立多期間引擎尚待完成收盤檢查點；正式流程繼續",
        ))

    forward_policy = forward_outcomes.get("policy") or {}
    forward_markets = forward_outcomes.get("markets") or {}
    forward_cohorts = sum(
        int((forward_markets.get(market) or {}).get("cohort_count") or 0)
        for market in ("TW", "US")
    )
    if (
        forward_outcomes
        and forward_policy.get("same_day_backfill_forbidden") is True
        and forward_policy.get("future_data_forbidden") is True
        and forward_policy.get("shadow_learning_only") is True
        and forward_policy.get("formal_v6_modified") is False
    ):
        check = _check(
            "forward_outcome_ledger", "多期間真實答案帳本", "ok",
            f"台美股共 {forward_cohorts} 組事前樣本；5／45／60／126日依到期順序結算，正式V6未變更",
        )
        check["cohort_count"] = forward_cohorts
        check["ledger_updated_at"] = forward_outcomes.get("updated_at")
        checks.append(check)
    elif forward_outcomes:
        checks.append(_check(
            "forward_outcome_ledger", "多期間真實答案帳本", "critical",
            "答案帳本缺少防回填、影子隔離或正式V6鎖定保證",
            "停止將該帳本供影子訓練；正式V6與下單維持鎖定",
        ))
    else:
        checks.append(_check(
            "forward_outcome_ledger", "多期間真實答案帳本", "warning",
            "尚未建立5／45／60／126日逐股答案帳本；正式V6不受影響",
            "下一次台股晚報或美股早報自動建立，不回填歷史答案",
        ))

    if unified_evidence.get("status") == "ready" and int(unified_evidence.get("invalid_count") or 0) == 0:
        checks.append(_check(
            "unified_evidence", "統一證據格式", "ok",
            f"已驗證 {int(unified_evidence.get('evidence_count') or 0)} 筆證據，格式錯誤 0 筆",
        ))
    elif unified_evidence:
        checks.append(_check(
            "unified_evidence", "統一證據格式", "critical",
            f"發現 {int(unified_evidence.get('invalid_count') or 0)} 筆格式錯誤",
            "中央頁停止採用錯誤證據；正式排名維持不變",
        ))
    else:
        checks.append(_check("unified_evidence", "統一證據格式", "warning", "尚未建立統一證據檔", "下一次中央中樞更新時自動重建"))

    rotation_status = str(rotation_health.get("status") or "missing").lower()
    if rotation_status == "ok":
        checks.append(_check(
            "rotation_shadow", "市場規則／族群輪動", "ok",
            "影子模組正常；與正式排名及下單完全隔離",
        ))
    elif rotation_status == "warning":
        error_type = str(rotation_health.get("error_type") or "未分類錯誤")
        checks.append(_check(
            "rotation_shadow", "市場規則／族群輪動", "warning",
            f"影子模組異常（{error_type}）；正式排名與早中晚報仍繼續",
            "檢查輪動健康紀錄與本次報表執行日誌；不得因此修改正式排名",
        ))
    else:
        checks.append(_check(
            "rotation_shadow", "市場規則／族群輪動", "warning",
            "尚未取得影子模組健康紀錄；不影響正式排名與報表",
            "等待下一次完整股票報告建立獨立健康紀錄",
        ))

    valuation_status = str(valuation_health.get("status") or "missing").lower()
    if valuation_status == "ok":
        checks.append(_check(
            "valuation_risk_shadow", "估值風險雷達", "ok",
            "影子估值正常；尚未加入正式排名或權重",
        ))
    elif valuation_status == "warning":
        error_type = str(valuation_health.get("error_type") or "未分類錯誤")
        checks.append(_check(
            "valuation_risk_shadow", "估值風險雷達", "warning",
            f"估值影子異常（{error_type}）；正式排名仍繼續",
            "檢查估值影子健康紀錄；不得用不完整估值修改正式分數",
        ))
    else:
        checks.append(_check(
            "valuation_risk_shadow", "估值風險雷達", "warning",
            "尚未取得估值影子健康紀錄；不影響正式排名",
            "等待下一次完整股票報告建立估值影子紀錄",
        ))

    decision_status = str(decision_health.get("status") or "missing").lower()
    if decision_status == "ok":
        checks.append(_check(
            "decision_hub", "中央 AI 決策中樞", "ok",
            "證據整合與衝突判斷正常；正式排名、權重與下單維持隔離",
        ))
    elif decision_status == "warning":
        error_type = str(decision_health.get("error_type") or "未分類錯誤")
        checks.append(_check(
            "decision_hub", "中央 AI 決策中樞", "warning",
            f"中央決策中樞異常（{error_type}）；正式排名與早中晚報仍繼續",
            "檢查中央中樞健康紀錄；不得改用不完整資料自行補分",
        ))
    else:
        checks.append(_check(
            "decision_hub", "中央 AI 決策中樞", "warning",
            "尚未取得中央決策中樞健康紀錄；不影響正式排名",
            "等待下一次完整股票報告建立中央中樞健康紀錄",
        ))

    checks.append(_primary_app_check(reports_dir, primary_app_probe))
    checks.extend(_live_runtime_checks(live_runtime_probe, previous))
    checks.append(_publish_check("friend", friend_publish, previous))
    checks.append(_publish_check("owner", owner_publish, previous))
    severity = max((LEVEL_ORDER.get(item["level"], 0) for item in checks), default=0)
    overall = "critical" if severity >= 2 else "warning" if severity == 1 else "ok"
    counts = {level: sum(item["level"] == level for item in checks) for level in ("ok", "info", "warning", "critical")}
    return {
        "system": "武得 AI 系統值班員 V1",
        "status": overall,
        "status_label": {"ok": "🟢 系統正常", "warning": "🟡 需要注意", "critical": "🔴 需要處理"}[overall],
        "checked_at": now.isoformat(timespec="seconds"),
        "timezone": "Asia/Taipei",
        "report_updated_at": latest.get("updated_at"),
        "report_age_minutes": age_minutes,
        "counts": counts,
        "checks": checks,
        "action_required": [item["action"] for item in checks if item["level"] in {"warning", "critical"} and item["action"]],
        "safety": {
            "changes_rankings": False,
            "places_orders": False,
            "deletes_data": False,
            "automatic_fix": False,
            "note": "只監控、診斷與通報；不改模型、不下單、不刪除資料。",
        },
        "monitoring_boundaries": {
            "primary_app": "GitHub Pages 主畫面 App 與五個功能頁",
            "live_backend": "Railway 台股／美股串流與 Telegram 通知",
            "device_authorization": "單一手機唯讀授權；失效只影響該裝置",
            "legacy_owner_site": "舊 ChatGPT Sites 備援；失敗不影響整體",
        },
    }


def _semantic(value: dict[str, Any]) -> dict[str, Any]:
    clone = deepcopy(value)
    clone.pop("checked_at", None)
    clone.pop("report_age_minutes", None)
    for item in clone.get("checks", []):
        if item.get("code") == "report_freshness":
            item["detail"] = item.get("level")
    return clone


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reports-dir", default="reports")
    parser.add_argument("--friend-publish", default="unknown")
    parser.add_argument("--owner-publish", default="unknown")
    parser.add_argument(
        "--primary-app-url",
        default=os.getenv("PRIMARY_APP_URL", DEFAULT_PRIMARY_APP_URL),
    )
    parser.add_argument(
        "--live-health-url",
        default=os.getenv("LIVE_HEALTH_URL", DEFAULT_LIVE_HEALTH_URL),
    )
    parser.add_argument("--skip-runtime-probes", action="store_true")
    parser.add_argument("--state-change-only", action="store_true")
    args = parser.parse_args()
    reports_dir = Path(args.reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    output = reports_dir / "system_guard.json"
    previous = _load(output)
    primary_app_probe = None
    live_runtime_probe = None
    if not args.skip_runtime_probes:
        primary_app_probe = _http_probe(args.primary_app_url)
        live_runtime_probe = _http_probe(args.live_health_url, expect_json=True)
    guard = build_guard(
        reports_dir,
        friend_publish=args.friend_publish,
        owner_publish=args.owner_publish,
        primary_app_probe=primary_app_probe,
        live_runtime_probe=live_runtime_probe,
    )
    if args.state_change_only and previous and _semantic(previous) == _semantic(guard):
        print(f"System guard unchanged: {guard['status_label']}")
        return 0
    output.write_text(json.dumps(guard, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"System guard: {guard['status_label']} -> {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
