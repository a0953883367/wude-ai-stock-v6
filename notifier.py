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


def _stock_block(row: dict[str, Any]) -> str:
    action = str(row.get("action", "🟡 觀察"))
    light = action[:1] if action[:1] in {"🟢", "🟡", "🔴"} else "🟡"
    action_text = action[1:].strip()
    return "\n".join([
        f"{light} {row['name']} {_code(row)}｜{_num(row.get('price'))}｜{_change(row.get('change_pct'))}｜量 {_num(row.get('volume_pace'))}x",
        f"   買 {_num(row.get('buy_price'))}｜支撐 {_num(row.get('support1'))}｜壓力 {_num(row.get('resistance1'))}｜{action_text}｜{row.get('risk', '一般波動')}",
    ])


def render_markdown(report: dict[str, Any]) -> str:
    period_names = {"morning": "早報", "noon": "午報", "evening": "晚報"}
    lines = [
        f"📊 武得 AI 股票{period_names[report['period']]}",
        f"🕒 {report['updated_at']}（台灣時間）",
        f"固定清單 {report['watchlist_analyzed_count']}/{report['watchlist_count']} 檔｜背景掃描 {report['universe_count']} 檔",
        "",
        "🌏 國際與大盤",
    ]
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
