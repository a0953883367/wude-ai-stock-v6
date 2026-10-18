"""Report rendering and Telegram delivery."""

from __future__ import annotations

import json
import logging
from pathlib import Path
import threading
from typing import Any

import requests

from config import SETTINGS


LOG = logging.getLogger(__name__)
_LIVE_CHAT_LOCK = threading.Lock()
_LIVE_DISCOVERED_CHAT_ID = ""
_LIVE_FRIEND_CHAT_LOCK = threading.Lock()
_LIVE_DISCOVERED_FRIEND_CHAT_ID = ""


def _num(value: Any, digits: int = 2) -> str:
    if value is None:
        return "N/A"
    return f"{float(value):,.{digits}f}"


def _code(row: dict[str, Any]) -> str:
    symbol = str(row.get("symbol", ""))
    return symbol.split(".")[0] if row.get("market") == "TW" else symbol


def _change(value: Any) -> str:
    number = float(value or 0)
    return f"{number:+.2f}%"


def _optional_percent(value: Any) -> str:
    return "N/A" if value is None else _change(value)


def _signed(value: Any) -> str:
    if value is None:
        return "N/A"
    return f"{float(value):+,.0f}"


def _judgment_lines(research):
    j = research.get('judgment')
    if not isinstance(j, dict):
        return []
    short = j['short']
    return [f"   綜合研究判斷（非即時）｜{j.get('source_session_date') or '日期缺失'}｜{short['horizon']}：{j['headline']}",
            f"   新進場：{short['new_entry']}", f"   若已持有（未假設實際持倉）：{short['if_already_held']}",
            f"   主要依據：{short.get('reason') or '缺同批既有中樞理由'}；"+'；'.join(short.get('legacy_outlook_reasons') or []),
            "   支持凍結方向的因素："+'、'.join(short['supporting_reasons'] or ['未保存']),
            "   反對凍結方向的因素："+'、'.join(short['opposing_reasons'] or ['未保存']),
            "   重要衝突："+'；'.join(short['important_conflicts'] or ['未讀到可核對衝突，不代表模型一致']),
            f"   改判觀察：{short.get('change_condition') or '未保存'}（{short['condition_status']}）",
            "   尚缺："+'；'.join(short['missing']),
            "   持倉另缺："+'、'.join(short['position_information_missing']),
            "   三個月："+j['quarter']['conclusion']+'；缺項：'+'；'.join(j['quarter']['missing']),
            "   三個月因素（非已證實因果）：支持各模型方向 "+'、'.join(j['quarter']['supporting_reasons'] or ['未保存'])+'；反對 '+'、'.join(j['quarter']['opposing_reasons'] or ['未保存']),
            "   5～10年："+j['long_term']['conclusion']+'；缺項：'+'；'.join(j['long_term']['missing'])]


def _research_lines(research):
    lines = ["   研究方向與證據（歷史凍結，非新預測／買入指令；完整因素、版本、驗證結果見同批全池資料）："]
    for item in research.get("horizons", []):
        code = item.get("horizon_code")
        if item.get("status") != "frozen_research":
            lines.append(f"   • {code}：研究缺失／未通過來源核對")
            continue
        support = item.get('supporting_evidence') or []
        opposition = item.get('opposing_evidence') or []
        positive = str(support[0].get('label') or support[0].get('factor')) if support else '未保存'
        negative = str(opposition[0].get('label') or opposition[0].get('factor')) if opposition else '未保存'
        lines.append(f"   • {code}／{item['horizon_sessions']}日｜{item['session_date']} 凍結｜{item['direction']} {_optional_percent(item.get('expected_return_pct'))}｜主要支持方向：{positive}；反對方向：{negative}")
    data = research.get("data_sufficiency") or {}
    lines.append(f"   資料：{data.get('market_data_quality') or '未提供'}｜來源日 {data.get('official_session_date') or '未知'}；時效與完整性仍須核對。因素為模型貢獻，不是因果。")
    conflicts = (research.get("model_disagreement") or {}).get("same_session_horizon_conflicts") or []
    lines.append(f"   同日同期間模型分歧 {len(conflicts)} 組；跨期間／系統不合併投票，缺分歧資料不代表一致。")
    condition = (research.get("existing_conditions") or {}).get("outlook_change_condition")
    lines.append(f"   既有當期轉向條件：{condition or '未保存'}；凍結模型未保存觸發／失效點，不補造。")
    lines.append("   執行：不放行行動或計畫價位；零可執行指示不是零研究方向。歷史例數不是總驗證樣本，機率未校準。")
    return lines


def _stock_block(row: dict[str, Any]) -> str:
    action = str(row.get("action", "🟡 觀察"))
    light = action[:1] if action[:1] in {"🟢", "🟡", "🔴"} else "🟡"
    action_text = action[1:].strip()
    judgment_lines = _judgment_lines(row.get("research") or {})
    if judgment_lines:
        judgment_lines[0] = f"{row['name']} {_code(row)}｜" + judgment_lines[0].strip()
    lines = judgment_lines + [
        f"{light} {row['name']} {_code(row)}｜{_num(row.get('price'))}｜{_change(row.get('change_pct'))}｜量 {_num(row.get('volume_pace'))}x",
        f"   {row.get('outlook_direction', '↔️ 震盪平盤')}｜未來1～5日｜指標信心 {_num(row.get('outlook_confidence'), 1)}分（非校準勝率）",
        f"   均線 5/10/20：{_num(row.get('ma5'))}/{_num(row.get('ma10'))}/{_num(row.get('ma20'))}",
        f"   K線：{row.get('kline_pattern', 'N/A')}｜{row.get('volume_price_pattern', '量價中性')}｜日量 {_num(row.get('daily_volume_ratio'))}x",
        f"   買進區 {_num(row.get('buy_zone_low'))}～{_num(row.get('buy_zone_high'))}｜支撐 {_num(row.get('support1'))}｜壓力 {_num(row.get('resistance1'))}｜{action_text}｜{row.get('risk', '一般波動')}",
    ]
    news_level = str(row.get("news_risk_level") or "")
    news_penalty = float(row.get("news_penalty") or 0)
    if news_penalty > 0 or news_level.startswith(("🔴", "🟡")):
        effect = f"｜計分 -{news_penalty:.1f}" if news_penalty > 0 else "｜僅觀察、不扣分"
        lines.append(
            f"   📰 {news_level}{effect}｜{row.get('news_summary', '近期負面消息請留意')}"
        )
        for article in (row.get("news_articles") or [])[:2]:
            lines.append(
                f"      {article.get('published_at', '—')}｜{article.get('publisher', '來源未標示')}｜{article.get('title', '')}"
            )
    if row.get("institution_available"):
        lines.insert(2, f"   法人 1/5/10日：{_num(row.get('institution_1d'), 0)}/{_num(row.get('institution_5d'), 0)}/{_num(row.get('institution_10d'), 0)} 股")
    if row.get("credit_available"):
        lines.insert(-1, f"   信用 5日：融資 {_signed(row.get('margin_5d_change'))}｜融券 {_signed(row.get('short_5d_change'))}｜借券賣出 {_signed(row.get('sbl_5d_change'))} 股")
    if row.get("fundamental_available"):
        lines.insert(-1, f"   基本面：PER {_num(row.get('per'))}｜PBR {_num(row.get('pbr'))}｜殖利率 {_num(row.get('dividend_yield'))}%｜營收年增 {_change(row.get('revenue_yoy_pct'))}")
    if row.get("financial_quality_available"):
        lines.insert(-1, f"   財務品質：EPS {_num(row.get('eps'))}（年增 {_optional_percent(row.get('eps_yoy_pct'))}）｜毛利 {_optional_percent(row.get('gross_margin_pct'))}｜營益 {_optional_percent(row.get('operating_margin_pct'))}｜ROE估 {_optional_percent(row.get('roe_pct'))}｜負債 {_optional_percent(row.get('debt_ratio_pct'))}")
        cash_text = "正" if row.get("operating_cash_flow_positive") else "負"
        lines.insert(-1, f"   現金流：營業現金流為{cash_text}｜財務品質分 {_num(row.get('financial_quality_score'), 1)}")
    if row.get("broker_available"):
        buyers = "、".join(str(item.get("name", "")) for item in row.get("top_brokers_buy", [])[:3]) or "N/A"
        sellers = "、".join(str(item.get("name", "")) for item in row.get("top_brokers_sell", [])[:3]) or "N/A"
        lines.insert(-1, f"   分點：買 {buyers}｜賣 {sellers}")
    if row.get("positioning_signal"):
        lines.append(
            f"   🎯 主力多空雷達：{row.get('positioning_signal')}｜"
            f"{_num(row.get('positioning_score'), 1)}分｜{row.get('positioning_data_quality', '部分資料')}"
        )
        for item in row.get("positioning_evidence", [])[:3]:
            lines.append(f"   • {item}")
        if row.get("positioning_disclaimer"):
            lines.append(f"   註：{row.get('positioning_disclaimer')}")
    if row.get("scenario_continuation"):
        lines.extend([
            f"   📋 {row.get('scenario_title', '明日劇本')}（{row.get('scenario_data_quality', '部分資料')}）",
            f"   依據：{row.get('scenario_basis')}",
            f"   🔥 {row.get('scenario_continuation')}",
            f"   ⚠️ {row.get('scenario_no_chase')}",
            f"   🛡️ {row.get('scenario_breakdown_text')}",
        ])
    if isinstance(row.get("research"), dict):
        lines.extend(_research_lines(row["research"]))
    return "\n".join(lines)


def render_markdown(report: dict[str, Any]) -> str:
    period_names = {"morning": "早報", "noon": "午報", "evening": "晚報"}
    lines = [
        f"📊 武得 AI 股票{period_names[report['period']]}",
        f"🕒 {report['updated_at']}（台灣時間）",
        f"固定清單 {report['watchlist_analyzed_count']}/{report['watchlist_count']} 檔｜背景掃描 {report['universe_count']} 檔",
    ]
    if report.get("research_reference"):
        integration = report.get("research_integration") or {}
        lines.extend([
            "🧭 研究方向／資料是否足夠／執行是否放行分開解讀；資料合格計畫不是買點，零可執行指示不是零研究機會。",
            f"完整研究池：reports/all_analysis.json｜必須核對批次 {report['updated_at']}｜整合 {integration.get('status', '請讀目標檔狀態，不假定已完成')}",
            "文字固定清單不是完整研究池；逐檔逐期間證據、條件缺口及結果以同批完整資料為準。ChatGPT原生任務接入尚未驗收。",
        ])
    status = report.get("data_status", {})
    if not status.get("finmind_configured"):
        lines.append("⚠️ 籌碼通報：FINMIND_TOKEN 未設定；缺少的籌碼維度不納入計分，並降低資料信心")
    else:
        missing = []
        if not status.get("institutional_count"):
            missing.append("法人")
        if not status.get("credit_count"):
            missing.append("融資融券／借券")
        if not status.get("fundamental_count"):
            missing.append("基本面／估值")
        if missing:
            lines.append(f"⚠️ 籌碼通報：{'、'.join(missing)}資料未取得，請檢查 Token／API額度；缺少維度不納入計分並降低資料信心")
        if not status.get("broker_count"):
            lines.append("ℹ️ 分點通報：FinMind Sponsor 分點目前未回傳；不以法人或成交量冒充分點，其他免費籌碼不受影響")
        quality_count = int(status.get("financial_quality_count") or 0)
        expected_count = int(
            status.get("expected_financial_quality_count")
            or sum(
                str(row.get("market") or "").upper() == "TW"
                and "ETF" not in str(row.get("type") or "").upper()
                for row in report.get("watchlist", [])
                if isinstance(row, dict)
            )
            or status.get("expected_tw_count")
            or 0
        )
        if quality_count == 0:
            lines.append("⚠️ 財務品質通報：本次未取得財報資料，請檢查 Token／API額度；缺少維度不納入計分並限制中長線資格")
        elif quality_count < expected_count:
            lines.append(f"ℹ️ 財務品質：已快取 {quality_count}/{expected_count} 檔；為避免免費額度超限，每次最多補30檔")
    performance = report.get("performance", {})
    calibration = performance.get("calibration", {})
    tracks = performance.get("tracks", {})
    five_day = performance.get("horizons", {}).get("5", {})
    if int(performance.get("methodology_version") or 0) < 6:
        lines.append("🧭 隔日預測V6：台／美股分開配分；等待各市場正式開盤與收盤結果")
    elif any(int((tracks.get(key) or {}).get("samples") or 0) for key in ("overnight", "session", "full_day")):
        labels = {"overnight": "隔夜", "session": "盤中", "full_day": "全天"}
        parts = []
        for key in ("overnight", "session", "full_day"):
            metric = tracks.get(key, {})
            if int(metric.get("samples") or 0):
                parts.append(
                    f"{labels[key]} {metric['win_rate_pct']:.1f}%/{metric['samples']}筆"
                )
        lines.append("🎯 隔日三段實測：" + "｜".join(parts))
    else:
        lines.append("🧪 隔日預測V6：市場分流、三段各自投票；尚無完成結果，不顯示假命中率")
    if five_day.get("samples"):
        lines.append(
            f"🎯 共識模型5日：{five_day['samples']}筆｜方向命中 {five_day['win_rate_pct']:.1f}%｜平均 {_change(five_day['avg_return_pct'])}｜最差 {_change(five_day['worst_return_pct'])}"
        )
    if calibration.get("ready_for_model_selection"):
        samples = int(calibration.get("eligible_one_day_samples") or 0)
        lines.append(f"✅ 模型遴選：已達60交易日及200筆共識訊號門檻（目前 {samples} 筆；審核前不影響正式排名）")
    else:
        lines.append("🛡️ 實績校正：未達驗收門檻前調整為0分，不讓少量結果扭曲排名")
    macro = report.get("macro_regime", {})
    macro_calibration = macro.get("calibration", {})
    if macro_calibration.get("affects_ai_score"):
        lines.append(
            f"🌐 總體風險：{macro.get('regime', '中性')} {macro.get('score', 50):.1f}分｜已納入計分"
        )
    else:
        remaining = macro_calibration.get("remaining_trading_days")
        if remaining is not None:
            lines.append(
                f"🌐 總體風險：{macro.get('regime', '中性')} {macro.get('score', 50):.1f}分｜尚差 {remaining} 個交易日，暫不計分"
            )
    treasury = report.get("treasury_stress") or {}
    if treasury:
        parts = [f"{treasury.get('light', '⚪')} 美債監控：{treasury.get('label', '等待資料')}"]
        if treasury.get("tlt_change_pct") is not None:
            parts.append(f"TLT {_change(treasury['tlt_change_pct'])}")
        if treasury.get("ief_change_pct") is not None:
            parts.append(f"IEF {_change(treasury['ief_change_pct'])}")
        if treasury.get("us10y_yield_change_bps") is not None:
            parts.append(f"10年債殖利率 {treasury['us10y_yield_change_bps']:+.1f}bp")
        parts.append("影子風險層，不改正式排名")
        lines.append("｜".join(parts))
    lines.extend(["", "🌏 國際與大盤"])
    for name, item in report["market"].items():
        lines.append(f"{name}｜{_num(item.get('price'))}｜{_change(item.get('change_pct'))}")

    for market, title in (("TW", "🇹🇼 台股固定觀察"), ("US", "🇺🇸 美股／ETF固定觀察")):
        rows = [row for row in report["watchlist"] if row.get("market") == market]
        lines.extend(["", title, ""])
        lines.extend(_stock_block(row) + "\n" for row in rows)

    if report.get("unavailable"):
        missing = "、".join(f"{item['name']}({_code(item)})" for item in report["unavailable"])
        lines.extend(["", "⚪ 暫無可靠行情", missing])

    if report.get("top"):
        lines.extend(["", "🏆 全台股通過風控首選（最多 5 檔）", ""])
        lines.extend(_stock_block(row) + "\n" for row in report["top"])
    else:
        lines.extend([
            "",
            "🛡️ 全台股背景掃描：目前沒有通過完整風控的買進首選",
            "未合格股票只保留在觀察清單，不列為最強或買進排名。",
        ])
    lines.extend([
        "",
        "判讀：🟢可分批｜🟡等拉回或確認｜🔴暫不買",
        "本報告為資料整理與風險輔助，不保證獲利，也不是代客下單建議。",
    ])
    return "\n".join(lines).strip()


def save_report(
    report: dict[str, Any],
    markdown: str,
    *,
    save_snapshot: bool = True,
) -> tuple[Path, Path]:
    """Save live output and, for fixed reports, the period snapshot/archive."""
    SETTINGS.reports_dir.mkdir(parents=True, exist_ok=True)
    latest_json = SETTINGS.reports_dir / "latest.json"
    latest_md = SETTINGS.reports_dir / "latest.md"
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    latest_json.write_text(payload, encoding="utf-8")
    latest_md.write_text(markdown, encoding="utf-8")
    if save_snapshot:
        period_json = SETTINGS.reports_dir / f"{report['period']}.json"
        period_json.write_text(payload, encoding="utf-8")
        archive = SETTINGS.reports_dir / "archive"
        archive.mkdir(exist_ok=True)
        archive_name = f"{report['updated_at'][:10]}-{report['period']}.json"
        (archive / archive_name).write_text(payload, encoding="utf-8")
    return latest_json, latest_md


def _telegram_chunks(text: str, limit: int = 3800) -> list[str]:
    chunks: list[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        candidate = paragraph if not current else f"{current}\n\n{paragraph}"
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        while len(paragraph) > limit:
            split = paragraph.rfind("\n", 0, limit)
            split = split if split > 500 else limit
            chunks.append(paragraph[:split])
            paragraph = paragraph[split:].lstrip()
        current = paragraph
    if current:
        chunks.append(current)
    return chunks


def send_telegram(markdown: str) -> bool:
    if not SETTINGS.telegram_bot_token or not SETTINGS.telegram_chat_id:
        LOG.info("Telegram secrets not set; report saved without sending")
        return False
    url = f"https://api.telegram.org/bot{SETTINGS.telegram_bot_token}/sendMessage"
    for chunk in _telegram_chunks(markdown):
        response = requests.post(
            url,
            json={"chat_id": SETTINGS.telegram_chat_id, "text": chunk, "disable_web_page_preview": True},
            timeout=SETTINGS.request_timeout,
        )
        response.raise_for_status()
    return True


def _discover_live_chat_id(token: str) -> str:
    """Resolve the latest private /start conversation for this dedicated bot."""
    response = requests.get(
        f"https://api.telegram.org/bot{token}/getUpdates",
        params={"limit": 100, "timeout": 0},
        timeout=SETTINGS.request_timeout,
    )
    response.raise_for_status()
    payload = response.json()
    updates = payload.get("result", []) if isinstance(payload, dict) else []
    for update in reversed(updates):
        message = update.get("message") if isinstance(update, dict) else None
        if not isinstance(message, dict):
            continue
        chat = message.get("chat")
        text = str(message.get("text") or "")
        if (
            isinstance(chat, dict)
            and chat.get("type") == "private"
            and text.startswith("/start")
            and chat.get("id") is not None
        ):
            return str(chat["id"])
    return ""


def _stored_live_chat_id(state_path: Path | None) -> str:
    if state_path is None:
        return ""
    try:
        stored = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return ""
    if not isinstance(stored, dict):
        return ""
    return str(stored.get("chat_id") or "").strip()


def _save_live_chat_id(state_path: Path | None, chat_id: str) -> None:
    if state_path is None or not chat_id:
        return
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps({"chat_id": chat_id}, ensure_ascii=False),
        encoding="utf-8",
    )


def _friend_channel_from_update(update: Any, expected_title: str) -> str:
    """Return a matching channel ID without accepting groups or private chats."""
    if not isinstance(update, dict) or not expected_title:
        return ""
    membership = update.get("my_chat_member")
    if isinstance(membership, dict):
        chat = membership.get("chat")
        new_member = membership.get("new_chat_member")
        status = str(new_member.get("status") or "") if isinstance(new_member, dict) else ""
        if status not in {"administrator", "creator"}:
            return ""
    else:
        message = update.get("channel_post")
        chat = message.get("chat") if isinstance(message, dict) else None
    if not isinstance(chat, dict):
        return ""
    if chat.get("type") != "channel" or str(chat.get("title") or "").strip() != expected_title:
        return ""
    return str(chat["id"]) if chat.get("id") is not None else ""


def _discover_live_friend_chat_id(token: str, expected_title: str) -> str:
    """Find only the named channel where the dedicated bot is an administrator."""
    response = requests.get(
        f"https://api.telegram.org/bot{token}/getUpdates",
        params={
            "limit": 100,
            "timeout": 0,
            "allowed_updates": json.dumps(["message", "my_chat_member", "channel_post"]),
        },
        timeout=SETTINGS.request_timeout,
    )
    response.raise_for_status()
    payload = response.json()
    updates = payload.get("result", []) if isinstance(payload, dict) else []
    for update in reversed(updates):
        chat_id = _friend_channel_from_update(update, expected_title)
        if chat_id:
            return chat_id
    return ""


def _stored_live_friend_chat_id(state_path: Path | None, expected_title: str) -> str:
    if state_path is None:
        return ""
    try:
        stored = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return ""
    if not isinstance(stored, dict) or stored.get("channel_title") != expected_title:
        return ""
    return str(stored.get("chat_id") or "").strip()


def _save_live_friend_chat_id(state_path: Path | None, expected_title: str, chat_id: str) -> None:
    if state_path is None or not chat_id:
        return
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps({"channel_title": expected_title, "chat_id": chat_id}, ensure_ascii=False),
        encoding="utf-8",
    )


def _live_telegram_credentials(
    settings: Any = SETTINGS,
    *,
    discover_chat: bool = False,
    state_path: Path | None = None,
) -> tuple[str, str]:
    """Return only the dedicated live-alert bot and its private conversation."""
    global _LIVE_DISCOVERED_CHAT_ID
    token = settings.telegram_live_bot_token
    if not token:
        return "", ""
    chat_id = (
        settings.telegram_live_chat_id
        or _LIVE_DISCOVERED_CHAT_ID
        or _stored_live_chat_id(state_path)
    )
    if token and not chat_id and discover_chat:
        with _LIVE_CHAT_LOCK:
            if not _LIVE_DISCOVERED_CHAT_ID:
                _LIVE_DISCOVERED_CHAT_ID = _discover_live_chat_id(token)
                _save_live_chat_id(state_path, _LIVE_DISCOVERED_CHAT_ID)
            chat_id = _LIVE_DISCOVERED_CHAT_ID
    return token, chat_id


def live_telegram_configured(settings: Any = SETTINGS) -> bool:
    # A private /start message lets the sender resolve the chat automatically.
    return bool(settings.telegram_live_bot_token)


def _live_friend_telegram_credentials(
    settings: Any = SETTINGS,
    *,
    discover_chat: bool = False,
    state_path: Path | None = None,
) -> tuple[str, str]:
    """Return the dedicated bot and the isolated friends-only alert channel."""
    global _LIVE_DISCOVERED_FRIEND_CHAT_ID
    token = settings.telegram_live_bot_token
    expected_title = settings.telegram_friend_alert_channel_title
    if not token or not expected_title:
        return "", ""
    chat_id = (
        settings.telegram_friend_alert_chat_id
        or _LIVE_DISCOVERED_FRIEND_CHAT_ID
        or _stored_live_friend_chat_id(state_path, expected_title)
    )
    if not chat_id and discover_chat:
        with _LIVE_FRIEND_CHAT_LOCK:
            if not _LIVE_DISCOVERED_FRIEND_CHAT_ID:
                _LIVE_DISCOVERED_FRIEND_CHAT_ID = _discover_live_friend_chat_id(
                    token, expected_title
                )
                _save_live_friend_chat_id(
                    state_path, expected_title, _LIVE_DISCOVERED_FRIEND_CHAT_ID
                )
            chat_id = _LIVE_DISCOVERED_FRIEND_CHAT_ID
    return token, chat_id


def _send_live_to_chat(token: str, chat_id: str, markdown: str) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for chunk in _telegram_chunks(markdown):
        response = requests.post(
            url,
            json={"chat_id": chat_id, "text": chunk, "disable_web_page_preview": True},
            timeout=SETTINGS.request_timeout,
        )
        response.raise_for_status()
    return True


def send_live_telegram(markdown: str, *, state_path: Path | None = None) -> bool:
    """Send only to the explicitly configured real-time alert conversation."""
    token, chat_id = _live_telegram_credentials(
        discover_chat=True, state_path=state_path
    )
    if not token or not chat_id:
        LOG.info("Live Telegram destination not set; alert kept in the website only")
        return False
    return _send_live_to_chat(token, chat_id, markdown)


def send_live_friend_telegram(markdown: str, *, state_path: Path | None = None) -> bool:
    """Send an approved friends-channel message without falling back to the owner chat."""
    token, chat_id = _live_friend_telegram_credentials(
        discover_chat=True, state_path=state_path
    )
    if not token or not chat_id:
        LOG.info("Friends Telegram channel is not paired; owner delivery remains active")
        return False
    return _send_live_to_chat(token, chat_id, markdown)


def send_live_alert_telegram(
    markdown: str,
    *,
    owner_state_path: Path | None = None,
    friend_state_path: Path | None = None,
) -> bool:
    """Deliver large-trade alerts to owner and friends as independent sinks."""
    owner_error: Exception | None = None
    owner_delivered = False
    try:
        owner_delivered = send_live_telegram(markdown, state_path=owner_state_path)
    except Exception as exc:
        owner_error = exc
    try:
        send_live_friend_telegram(markdown, state_path=friend_state_path)
    except Exception:
        LOG.exception("Friends Telegram delivery failed; owner delivery was not affected")
    if owner_error is not None:
        raise owner_error
    return owner_delivered
