"""Independent health guard for the stock assistant reporting pipeline.

The guard only observes report freshness, consistency, coverage and publish
outcomes.  It never changes rankings, model weights or trading settings.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


TAIPEI = ZoneInfo("Asia/Taipei")
LEVEL_ORDER = {"ok": 0, "info": 0, "warning": 1, "critical": 2}
LEVEL_LABEL = {"ok": "正常", "info": "資訊", "warning": "注意", "critical": "異常"}


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


def _publish_check(name: str, outcome: str, previous: dict[str, Any]) -> dict[str, Any]:
    value = str(outcome or "unknown").strip().lower()
    code = f"publish_{name}"
    title = "朋友版同步" if name == "friend" else "本人版同步"
    if value in {"unknown", ""}:
        old = next((item for item in previous.get("checks", []) if item.get("code") == code), None)
        if old:
            return deepcopy(old)
        return _check(code, title, "info", "尚未取得這次發布結果", "等待下一次股票報告排程")
    if value == "success":
        return _check(code, title, "ok", "最近一次發布已成功")
    if value == "skipped":
        return _check(code, title, "warning", "最近一次發布步驟被略過", "檢查網站網址與發布授權是否已設定")
    return _check(code, title, "critical", f"最近一次發布結果：{value}", "檢查發布網站狀態與授權，修復後重新執行報告")


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
    if expected_tw and min(official_prices, institutions, credit) == 0:
        checks.append(_check("tw_core_data", "台股核心資料", "critical", f"預期 {expected_tw} 檔，但價格／法人／信用資料至少一項為0", "檢查 FinMind、官方資料源與當日市場狀態"))
    elif expected_tw and min(official_prices, institutions, credit) < expected_tw * 0.8:
        checks.append(_check("tw_core_data", "台股核心資料", "warning", f"價格 {official_prices}、法人 {institutions}、信用 {credit}；預期 {expected_tw}", "缺資料股票不得取得完整買進資格"))
    else:
        checks.append(_check("tw_core_data", "台股核心資料", "ok", f"價格 {official_prices}、法人 {institutions}、信用 {credit}"))

    broker = int(data_status.get("broker_count") or 0)
    if broker <= 0:
        checks.append(_check(
            "broker_data", "券商分點", "warning", "目前沒有取得券商分點資料",
            "每次正式報告自動重試；此選配欄位不阻斷現價、趨勢與風險判斷",
        ))
    else:
        checks.append(_check("broker_data", "券商分點", "ok", f"已取得 {broker} 檔分點資料"))

    financial = int(data_status.get("financial_quality_count") or 0)
    if expected_tw and financial < expected_tw:
        checks.append(_check(
            "financial_quality", "財務品質", "warning",
            f"已取得 {financial}/{expected_tw} 檔，仍在分批補齊",
            "每次排程續補；只降低6個月信心，不把整檔標成資料不足",
        ))
    else:
        checks.append(_check("financial_quality", "財務品質", "ok", f"已取得 {financial} 檔"))

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

    if stockq.get("status") in {"ok", "stale_cache"} and int(stockq.get("indicator_count") or 0) > 0:
        checks.append(_check(
            "stockq_context", "StockQ 市場背景", "ok",
            f"已取得 {int(stockq.get('indicator_count') or 0)} 項全球指標；只作市場背景",
        ))
    else:
        checks.append(_check(
            "stockq_context", "StockQ 市場背景", "warning",
            "StockQ 指標暫時不可用；不影響個股資料與正式排名",
            "下次排程自動重試並優先使用三日內快取",
        ))

    if not validation_60d:
        checks.append(_check("validation_60d", "60日向前驗證", "warning", "尚未建立統一60日進度檔", "下一次報告自動重建"))
    else:
        checks.append(_check(
            "validation_60d", "60日向前驗證", "ok",
            f"真實交易日 {int(validation_60d.get('trading_days_collected') or 0)}/{int(validation_60d.get('target_trading_days') or 60)}；未補造缺日",
        ))

    if graduation.get("status") == "ready":
        checks.append(_check("model_graduation", "模型畢業控制器", "ok", "已自動產生畢業結論；升級仍須人工決定"))
    else:
        checks.append(_check("model_graduation", "模型畢業控制器", "warning", "尚未產生完整畢業結論", "下一次報告自動重建"))

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
    parser.add_argument("--state-change-only", action="store_true")
    args = parser.parse_args()
    reports_dir = Path(args.reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    output = reports_dir / "system_guard.json"
    previous = _load(output)
    guard = build_guard(
        reports_dir,
        friend_publish=args.friend_publish,
        owner_publish=args.owner_publish,
    )
    if args.state_change_only and previous and _semantic(previous) == _semantic(guard):
        print(f"System guard unchanged: {guard['status_label']}")
        return 0
    output.write_text(json.dumps(guard, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"System guard: {guard['status_label']} -> {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
