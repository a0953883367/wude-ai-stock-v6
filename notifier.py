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


def render_markdown(report: dict[str, Any]) -> str:
    period_names = {"morning": "早報", "noon": "午報", "evening": "晚報"}
    lines = [
        f"# 武得 AI 股票{period_names[report['period']]}",
        "",
        f"更新：{report['updated_at']}（台灣時間）",
        f"候選池：{report['universe_count']} 檔｜成功分析：{report['analyzed_count']} 檔",
        "",
        "## 國際與大盤",
    ]
    for name, item in report["market"].items():
        change = item.get("change_pct")
        sign = "+" if change is not None and change >= 0 else ""
        lines.append(f"- {name}: {_num(item.get('price'))}（{sign}{_num(change)}%）")
    lines.extend(["", "## 台股動態 TOP 10", ""])
    for row in report["top"]:
        change_sign = "+" if row["change_pct"] >= 0 else ""
        attack_sign = "+" if row["attack_volume"] >= 0 else ""
        lines.extend([
            f"### {row['rank']}. {row['name']}（{row['symbol']}）｜{row['score']} 分",
            f"- 現價 {_num(row['price'])}｜漲跌 {change_sign}{_num(row['change_pct'])}%｜相對量能 {_num(row['volume_pace'])} 倍｜攻擊量 {attack_sign}{_num(row['attack_volume'], 1)}%",
            f"- 法人合計 {int(row['institution_net']):+,} 股｜族群 {row['theme']}（{row['theme_change_pct']:+.2f}%）",
            f"- 支撐 {_num(row['support1'])} / {_num(row['support2'])}｜壓力 {_num(row['resistance1'])} / {_num(row['resistance2'])}",
            f"- 建議：{row['action']}｜參考買價 {_num(row['buy_price'])}｜風控 {_num(row['stop_price'])}｜風險：{row['risk']}",
            "",
        ])
    lines.extend([
        "## 判讀原則",
        "排名會隨盤中分時量能、攻擊量、股價、法人與族群強弱重新洗牌，不使用開盤前固定名次。攻擊量為 5 分 K 上漲量減下跌量的代理值，不等同交易所逐筆主動買賣資料。",
        "",
        "> 本報告為資料整理與風險輔助，不保證獲利，也不是代客下單建議。",
    ])
    return "\n".join(lines)


def save_report(report: dict[str, Any], markdown: str) -> tuple[Path, Path]:
    SETTINGS.reports_dir.mkdir(parents=True, exist_ok=True)
    latest_json = SETTINGS.reports_dir / "latest.json"
    latest_md = SETTINGS.reports_dir / "latest.md"
    latest_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_md.write_text(markdown, encoding="utf-8")
    archive = SETTINGS.reports_dir / "archive"
    archive.mkdir(exist_ok=True)
    archive_name = f"{report['updated_at'][:10]}-{report['period']}.json"
    (archive / archive_name).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return latest_json, latest_md


def send_telegram(markdown: str) -> bool:
    if not SETTINGS.telegram_bot_token or not SETTINGS.telegram_chat_id:
        LOG.info("Telegram secrets not set; report saved without sending")
        return False
    url = f"https://api.telegram.org/bot{SETTINGS.telegram_bot_token}/sendMessage"
    chunks: list[str] = []
    remaining = markdown
    while remaining:
        if len(remaining) <= 3500:
            chunks.append(remaining)
            break
        split = remaining.rfind("\n", 0, 3500)
        split = split if split > 1000 else 3500
        chunks.append(remaining[:split])
        remaining = remaining[split:].lstrip()
    for chunk in chunks:
        response = requests.post(
            url,
            json={"chat_id": SETTINGS.telegram_chat_id, "text": chunk, "disable_web_page_preview": True},
            timeout=SETTINGS.request_timeout,
        )
        response.raise_for_status()
    return True

