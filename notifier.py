"""Report rendering and Telegram delivery."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import requests

from config import SETTINGS


LOG = logging.getLogger(__name__)


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


def _stock_block(row: dict[str, Any]) -> str:
    action = str(row.get("action", "🟡 觀察"))
    light = action[:1] if action[:1] in {"🟢", "🟡", "🔴"} else "🟡"
    action_text = action[1:].strip()
    lines = [
        f"{light} {row['name']} {_code(row)}｜{_num(row.get('price'))}｜{_change(row.get('change_pct'))}｜量 {_num(row.get('volume_pace'))}x",
        f"   均線 5/10/20：{_num(row.get('ma5'))}/{_num(row.get('ma10'))}/{_num(row.get('ma20'))}",
        f"   K線：{row.get('kline_pattern', 'N/A')}｜{row.get('volume_price_pattern', '量價中性')}｜日量 {_num(row.get('daily_volume_ratio'))}x",
        f"   買 {_num(row.get('buy_price'))}｜支撐 {_num(row.get('support1'))}｜壓力 {_num(row.get('resistance1'))}｜{action_text}｜{row.get('risk', '一般波動')}",
    ]
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
    if row.get("scenario_continuation"):
        lines.extend([
            f"   📋 明日劇本（{row.get('scenario_data_quality', '部分資料')}）",
            f"   依據：{row.get('scenario_basis')}",
            f"   🔥 {row.get('scenario_continuation')}",
            f"   ⚠️ {row.get('scenario_no_chase')}",
            f"   🛡️ {row.get('scenario_breakdown_text')}",
        ])
    return "\n".join(lines)


def render_markdown(report: dict[str, Any]) -> str:
    period_names = {"morning": "早報", "noon": "午報", "evening": "晚報"}
    lines = [
        f"📊 武得 AI 股票{period_names[report['period']]}",
        f"🕒 {report['updated_at']}（台灣時間）",
        f"固定清單 {report['watchlist_analyzed_count']}/{report['watchlist_count']} 檔｜背景掃描 {report['universe_count']} 檔",
    ]
    status = report.get("data_status", {})
    if not status.get("finmind_configured"):
        lines.append("⚠️ 籌碼通報：FINMIND_TOKEN 未設定，本次使用中性籌碼分數")
    else:
        missing = []
        if not status.get("institutional_count"):
            missing.append("法人")
        if not status.get("credit_count"):
            missing.append("融資融券／借券")
        if not status.get("fundamental_count"):
            missing.append("基本面／估值")
        if missing:
            lines.append(f"⚠️ 籌碼通報：{'、'.join(missing)}資料未取得，請檢查 Token／API額度；本次自動採中性分數")
        if not status.get("broker_count"):
            lines.append("ℹ️ 分點通報：券商分點未取得，可能為 Sponsor 試用到期；其他免費籌碼不受影響")
        quality_count = int(status.get("financial_quality_count") or 0)
        expected_count = int(status.get("expected_tw_count") or 0)
        if quality_count == 0:
            lines.append("⚠️ 財務品質通報：本次未取得財報資料，請檢查Token／API額度；不完整股票使用中性分數")
        elif quality_count < expected_count:
            lines.append(f"ℹ️ 財務品質：已快取 {quality_count}/{expected_count} 檔；為避免免費額度超限，每次最多補30檔")
    performance = report.get("performance", {})
    calibration = performance.get("calibration", {})
    five_day = performance.get("horizons", {}).get("5", {})
    if five_day.get("samples"):
        lines.append(
            f"🎯 回測5日：{five_day['samples']}筆｜上漲命中 {five_day['win_rate_pct']:.1f}%｜平均 {_change(five_day['avg_return_pct'])}｜最差 {_change(five_day['worst_return_pct'])}"
        )
    else:
        lines.append("🎯 回測：已開始留存排名；累積5個交易日後顯示首批命中率")
    if calibration.get("affects_ai_score"):
        samples = int(calibration.get("eligible_one_day_samples") or 0)
        lines.append(f"✅ 實績校正：已用 {samples} 筆真實漲跌結果，小幅校正AI分數")
    else:
        lines.append("🛡️ 實績校正：等待首批真實隔日漲跌；目前調整為0分")
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

    lines.extend(["", "🏆 全台股背景掃描最強 5 檔", ""])
    lines.extend(_stock_block(row) + "\n" for row in report["top"])
    lines.extend([
        "",
        "判讀：🟢可分批｜🟡等拉回或確認｜🔴暫不買",
        "本報告為資料整理與風險輔助，不保證獲利，也不是代客下單建議。",
    ])
    return "\n".join(lines).strip()


def save_report(report: dict[str, Any], markdown: str) -> tuple[Path, Path]:
    SETTINGS.reports_dir.mkdir(parents=True, exist_ok=True)
    latest_json = SETTINGS.reports_dir / "latest.json"
    latest_md = SETTINGS.reports_dir / "latest.md"
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    latest_json.write_text(payload, encoding="utf-8")
    latest_md.write_text(markdown, encoding="utf-8")
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
