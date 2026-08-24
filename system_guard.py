"""Independent health guard for the stock assistant reporting pipeline.

The guard only observes report freshness, consistency, coverage and publish
outcomes.  It never changes rankings, model weights or trading settings.
"""

from __future__ import annotations

import argparse
import json
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
        checks.append(_check("broker_data", "券商分點", "warning", "目前沒有取得券商分點資料", "保留黃燈；不要用其他資料冒充分點資料"))
    else:
        checks.append(_check("broker_data", "券商分點", "ok", f"已取得 {broker} 檔分點資料"))

    financial = int(data_status.get("financial_quality_count") or 0)
    if expected_tw and financial < expected_tw:
        checks.append(_check("financial_quality", "財務品質", "warning", f"已取得 {financial}/{expected_tw} 檔，仍在分批補齊", "缺少財報的股票限制中長線資格"))
    else:
        checks.append(_check("financial_quality", "財務品質", "ok", f"已取得 {financial} 檔"))

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
