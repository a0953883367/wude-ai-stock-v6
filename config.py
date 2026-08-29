"""Configuration for Wude AI stock briefings."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent
TAIPEI = ZoneInfo("Asia/Taipei")


@dataclass(frozen=True)
class Settings:
    search_data_path: Path = ROOT / "search_data.json"
    reports_dir: Path = ROOT / "reports"
    finmind_token: str = os.getenv("FINMIND_TOKEN", "").strip()
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    telegram_live_bot_token: str = os.getenv("TELEGRAM_LIVE_BOT_TOKEN", "").strip()
    telegram_live_chat_id: str = os.getenv("TELEGRAM_LIVE_CHAT_ID", "").strip()
    telegram_live_batch_seconds: float = float(os.getenv("TELEGRAM_LIVE_BATCH_SECONDS", "10"))
    top_n: int = int(os.getenv("TOP_N", "10"))
    history_days: int = int(os.getenv("HISTORY_DAYS", "45"))
    request_timeout: int = int(os.getenv("REQUEST_TIMEOUT", "25"))


SETTINGS = Settings()
