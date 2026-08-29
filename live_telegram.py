"""Ten-second batching for the separate real-time Telegram conversation."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any, Protocol


LOG = logging.getLogger(__name__)


class TimerLike(Protocol):
    def start(self) -> None: ...
    def cancel(self) -> None: ...


TimerFactory = Callable[[float, Callable[[], None]], TimerLike]


def _amount(value: float, market: str) -> str:
    def compact(number: float, digits: int) -> str:
        return f"{number:,.{digits}f}".rstrip("0").rstrip(".")

    currency = "NT$" if market == "TW" else "US$"
    absolute = abs(value)
    if market == "TW" and absolute >= 10_000:
        return f"{currency}{compact(absolute / 10_000, 1)}萬"
    if absolute >= 1_000_000:
        return f"{currency}{compact(absolute / 1_000_000, 2)}M"
    if absolute >= 1_000:
        return f"{currency}{compact(absolute / 1_000, 1)}K"
    return f"{currency}{absolute:,.0f}"


def format_live_telegram_batch(alerts: list[dict[str, Any]]) -> str:
    ordered = sorted(alerts, key=lambda item: (str(item.get("market") or ""), int(item.get("sequence") or 0)))
    counts = {
        market: len({str(item.get("symbol")) for item in ordered if item.get("market") == market})
        for market in ("TW", "US")
    }
    count_text = "・".join(
        f"{'台股' if market == 'TW' else '美股'} {counts[market]}檔"
        for market in ("TW", "US") if counts[market]
    )
    lines = [f"🚨 10秒大量買賣通知｜{count_text or f'{len(ordered)}檔'}"]
    current_market = ""
    for alert in ordered:
        market = "US" if alert.get("market") == "US" else "TW"
        if market != current_market:
            lines.append(f"\n{'🇺🇸 美股' if market == 'US' else '🇹🇼 台股'}")
            current_market = market
        is_sell = alert.get("alert_side") == "sell"
        side = "賣" if is_sell else "買"
        icon = "🔻" if is_sell else "🟢"
        value = float(alert.get("sell_value" if is_sell else "buy_value") or alert.get("value") or 0)
        ratio = float(alert.get("aggressive_sell_ratio_pct" if is_sell else "aggressive_buy_ratio_pct") or 0)
        lines.append(
            f"{icon} {alert.get('name') or alert.get('symbol')}・{alert.get('symbol')}｜"
            f"{alert.get('trigger_label') or f'大量{side}進'}｜{_amount(value, market)}｜"
            f"{side}{ratio:.0f}%｜價 {float(alert.get('price') or 0):,.2f}"
        )
    lines.extend([
        "",
        "同檔同方向3分鐘內不重複；僅為成交方向警示，不識別買方身分、不改排名、不自動下單。",
    ])
    return "\n".join(lines)


class LiveTelegramBatcher:
    """Collect alerts and deliver one compact Telegram batch every window."""

    def __init__(
        self,
        sender: Callable[[str], Any],
        *,
        window_seconds: float = 10.0,
        timer_factory: TimerFactory = threading.Timer,
    ) -> None:
        self.sender = sender
        self.window_seconds = max(1.0, float(window_seconds))
        self.timer_factory = timer_factory
        self._pending: list[dict[str, Any]] = []
        self._timer: TimerLike | None = None
        self._lock = threading.Lock()

    def enqueue(self, alert: dict[str, Any]) -> None:
        with self._lock:
            self._pending.append(dict(alert))
            if self._timer is None:
                self._timer = self.timer_factory(self.window_seconds, self.flush)
                self._timer.start()

    def flush(self) -> bool:
        with self._lock:
            alerts = self._pending
            self._pending = []
            self._timer = None
        if not alerts:
            return False
        try:
            return bool(self.sender(format_live_telegram_batch(alerts)))
        except Exception:
            LOG.exception("Failed to send live Telegram alert batch")
            return False


def fanout_alert(*callbacks: Callable[[dict[str, Any]], Any]) -> Callable[[dict[str, Any]], None]:
    """Notify independent sinks without letting one failure block another."""
    def notify(alert: dict[str, Any]) -> None:
        for callback in callbacks:
            try:
                callback(alert)
            except Exception:
                LOG.exception("One real-time alert sink failed")
    return notify
