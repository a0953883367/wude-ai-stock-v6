"""Ten-second aggressive-buy detector shared by Taiwan and US live streams.

The detector never places orders and never changes ranking data.  A trade is
classified as buyer-initiated when it executes at the latest ask; the tick rule
is used only when the book is unavailable.  Alerts are emitted for either one
large print or a 3-5 print cluster inside a rolling ten-second window.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import threading
import time
from typing import Any, Callable


MARKETS = ("TW", "US")


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


@dataclass(frozen=True)
class LargeBuyConfig:
    window_seconds: float = 10.0
    cluster_min_trades: int = 3
    cluster_max_trades: int = 5
    cluster_buy_ratio_min: float = 0.70
    cooldown_seconds: float = 180.0
    single_min_twd: float = 3_000_000.0
    single_min_usd: float = 100_000.0
    cluster_min_twd: float = 5_000_000.0
    cluster_min_usd: float = 250_000.0
    single_daily_value_ratio: float = 0.0002
    cluster_daily_value_ratio: float = 0.0005
    alert_history_limit: int = 300


@dataclass(frozen=True)
class StockBaseline:
    symbol: str
    name: str
    market: str
    price: float
    avg_volume20: float

    @property
    def average_daily_value(self) -> float:
        return max(self.price, 0.0) * max(self.avg_volume20, 0.0)


@dataclass(frozen=True)
class TradePrint:
    timestamp: float
    price: float
    size: float
    value: float
    is_aggressive_buy: bool
    classification: str


class LargeBuyDetector:
    def __init__(
        self,
        baselines: dict[str, StockBaseline],
        *,
        config: LargeBuyConfig | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.baselines = baselines
        self.config = config or LargeBuyConfig()
        self.clock = clock
        self._windows: dict[str, deque[TradePrint]] = defaultdict(deque)
        self._quotes: dict[str, tuple[float | None, float | None]] = {}
        self._last_price: dict[str, float] = {}
        self._last_side: dict[str, bool] = {}
        self._last_alert_at: dict[str, float] = {}
        self._lock = threading.RLock()

    def update_quote(self, symbol: str, *, bid: Any = None, ask: Any = None) -> None:
        with self._lock:
            self._quotes[symbol] = (_finite(bid), _finite(ask))

    def _thresholds(self, baseline: StockBaseline) -> tuple[float, float]:
        daily_value = baseline.average_daily_value
        if baseline.market == "TW":
            single_floor = self.config.single_min_twd
            cluster_floor = self.config.cluster_min_twd
        else:
            single_floor = self.config.single_min_usd
            cluster_floor = self.config.cluster_min_usd
        return (
            max(single_floor, daily_value * self.config.single_daily_value_ratio),
            max(cluster_floor, daily_value * self.config.cluster_daily_value_ratio),
        )

    def _classify(
        self,
        symbol: str,
        price: float,
        bid: float | None,
        ask: float | None,
    ) -> tuple[bool, str]:
        if ask is not None and ask > 0 and price >= ask - max(abs(ask) * 1e-8, 1e-9):
            return True, "at_ask"
        if bid is not None and bid > 0 and price <= bid + max(abs(bid) * 1e-8, 1e-9):
            return False, "at_bid"
        previous = self._last_price.get(symbol)
        if previous is not None and price != previous:
            return price > previous, "uptick" if price > previous else "downtick"
        return self._last_side.get(symbol, False), "tick_carry"

    def process_trade(
        self,
        symbol: str,
        *,
        price: Any,
        size: Any,
        timestamp: float | None = None,
        bid: Any = None,
        ask: Any = None,
    ) -> dict[str, Any] | None:
        trade_price = _finite(price)
        trade_size = _finite(size)
        if trade_price is None or trade_size is None or trade_price <= 0 or trade_size <= 0:
            return None
        baseline = self.baselines.get(symbol)
        if baseline is None:
            return None
        at = self.clock() if timestamp is None else float(timestamp)
        with self._lock:
            stored_bid, stored_ask = self._quotes.get(symbol, (None, None))
            incoming_bid = _finite(bid)
            incoming_ask = _finite(ask)
            current_bid = incoming_bid if incoming_bid is not None else stored_bid
            current_ask = incoming_ask if incoming_ask is not None else stored_ask
            is_buy, classification = self._classify(symbol, trade_price, current_bid, current_ask)
            self._last_price[symbol] = trade_price
            self._last_side[symbol] = is_buy
            if current_bid is not None or current_ask is not None:
                self._quotes[symbol] = (current_bid, current_ask)

            trade = TradePrint(
                timestamp=at,
                price=trade_price,
                size=trade_size,
                value=trade_price * trade_size,
                is_aggressive_buy=is_buy,
                classification=classification,
            )
            window = self._windows[symbol]
            window.append(trade)
            cutoff = at - self.config.window_seconds
            while window and window[0].timestamp < cutoff:
                window.popleft()

            if not is_buy:
                return None
            if at - self._last_alert_at.get(symbol, float("-inf")) < self.config.cooldown_seconds:
                return None

            single_threshold, cluster_threshold = self._thresholds(baseline)
            trigger_type = ""
            selected: list[TradePrint] = []
            if trade.value >= single_threshold:
                trigger_type = "single"
                selected = [trade]
            else:
                buy_trades = [item for item in window if item.is_aggressive_buy]
                selected = buy_trades[-self.config.cluster_max_trades:]
                total_value = sum(item.value for item in window)
                selected_value = sum(item.value for item in selected)
                buy_ratio = selected_value / total_value if total_value > 0 else 0.0
                if (
                    self.config.cluster_min_trades <= len(selected) <= self.config.cluster_max_trades
                    and selected_value >= cluster_threshold
                    and buy_ratio >= self.config.cluster_buy_ratio_min
                ):
                    trigger_type = "cluster"
            if not trigger_type:
                return None

            total_window_value = sum(item.value for item in window)
            aggressive_window_value = sum(item.value for item in window if item.is_aggressive_buy)
            alert_value = sum(item.value for item in selected)
            self._last_alert_at[symbol] = at
            return {
                "symbol": baseline.symbol,
                "name": baseline.name,
                "market": baseline.market,
                "trigger_type": trigger_type,
                "trigger_label": "單筆大買" if trigger_type == "single" else f"{len(selected)}筆連續大買",
                "trade_count": len(selected),
                "window_seconds": self.config.window_seconds,
                "price": trade_price,
                "shares": sum(item.size for item in selected),
                "buy_value": round(alert_value, 2),
                "window_total_value": round(total_window_value, 2),
                "window_aggressive_buy_value": round(aggressive_window_value, 2),
                "aggressive_buy_ratio_pct": round(
                    aggressive_window_value / total_window_value * 100, 2
                ) if total_window_value else 0.0,
                "single_threshold": round(single_threshold, 2),
                "cluster_threshold": round(cluster_threshold, 2),
                "classification": classification,
                "detected_at": datetime.fromtimestamp(at, timezone.utc).isoformat(timespec="milliseconds"),
                "detected_at_epoch": at,
                "informational_only": True,
            }


class JsonAlertStore:
    def __init__(self, path: Path, *, limit: int = 300) -> None:
        self.path = path
        self.limit = max(10, limit)
        self._lock = threading.RLock()
        self._sequence = 0
        self._alerts: deque[dict[str, Any]] = deque(maxlen=self.limit)
        self._load()

    def _load(self) -> None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return
        alerts = payload.get("alerts") if isinstance(payload, dict) else None
        if not isinstance(alerts, list):
            return
        for alert in alerts[-self.limit:]:
            if isinstance(alert, dict):
                self._alerts.append(alert)
                self._sequence = max(self._sequence, int(alert.get("sequence") or 0))

    def append(self, alert: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._sequence += 1
            stored = {**alert, "sequence": self._sequence}
            self._alerts.append(stored)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": 1,
                "updated_at": stored.get("detected_at"),
                "alerts": list(self._alerts),
            }
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            tmp.replace(self.path)
            return stored

    def list_after(self, sequence: int = 0, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = [item for item in self._alerts if int(item.get("sequence") or 0) > sequence]
            return rows[-max(1, min(limit, 100)):]

    @property
    def latest_sequence(self) -> int:
        with self._lock:
            return self._sequence


def load_stock_baselines(path: Path) -> dict[str, StockBaseline]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return {}
    output: dict[str, StockBaseline] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").upper()
        market = str(row.get("market") or "").upper()
        price = _finite(row.get("price"))
        avg_volume20 = _finite(row.get("avg_volume20"))
        if not symbol or market not in MARKETS or price is None or avg_volume20 is None:
            continue
        output[symbol] = StockBaseline(
            symbol=symbol,
            name=str(row.get("name") or symbol),
            market=market,
            price=price,
            avg_volume20=avg_volume20,
        )
    return output


def format_large_buy_telegram(alert: dict[str, Any]) -> str:
    currency = "TWD" if alert.get("market") == "TW" else "USD"
    value = float(alert.get("buy_value") or 0)
    return "\n".join([
        f"🚨 10秒大量主動買進｜{alert.get('trigger_label')}",
        f"{alert.get('name')} {alert.get('symbol')}｜{alert.get('market')}",
        f"成交價 {float(alert.get('price') or 0):,.2f}｜{int(alert.get('trade_count') or 0)} 筆",
        f"主動買進金額 {currency} {value:,.0f}｜買進占比 {float(alert.get('aggressive_buy_ratio_pct') or 0):.1f}%",
        f"偵測時間 {alert.get('detected_at')}｜僅為即時量價警示，不代表主力身分或買進建議",
    ])


class LargeBuyAlertService:
    def __init__(
        self,
        report_path: Path,
        state_path: Path,
        *,
        config: LargeBuyConfig | None = None,
        notifier: Callable[[str], Any] | None = None,
    ) -> None:
        self.config = config or LargeBuyConfig()
        self.baselines = load_stock_baselines(report_path)
        self.detector = LargeBuyDetector(self.baselines, config=self.config)
        self.store = JsonAlertStore(state_path, limit=self.config.alert_history_limit)
        self.notifier = notifier
        self._status: dict[str, dict[str, Any]] = {
            market: {"state": "waiting", "subscribed": 0, "error": None}
            for market in MARKETS
        }
        self._lock = threading.RLock()

    def symbols(self, market: str) -> list[str]:
        return [row.symbol for row in self.baselines.values() if row.market == market]

    def set_stream_status(self, market: str, state: str, *, subscribed: int = 0, error: str | None = None) -> None:
        with self._lock:
            self._status[market] = {
                "state": state,
                "subscribed": subscribed,
                "error": error,
                "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }

    def update_quote(self, symbol: str, *, bid: Any = None, ask: Any = None) -> None:
        self.detector.update_quote(symbol, bid=bid, ask=ask)

    def process_trade(self, symbol: str, **trade: Any) -> dict[str, Any] | None:
        alert = self.detector.process_trade(symbol, **trade)
        if alert is None:
            return None
        stored = self.store.append(alert)
        if self.notifier is not None:
            try:
                self.notifier(format_large_buy_telegram(stored))
            except Exception:
                # Stream processing must continue even when notification delivery fails.
                pass
        return stored

    def snapshot(self, *, after: int = 0, limit: int = 50) -> dict[str, Any]:
        with self._lock:
            status = json.loads(json.dumps(self._status))
        return {
            "version": 1,
            "policy": {
                "scope": "all_site_stocks",
                "window_seconds": self.config.window_seconds,
                "single_or_cluster": True,
                "cluster_trade_count": [self.config.cluster_min_trades, self.config.cluster_max_trades],
                "broker_orders": False,
            },
            "universe": {
                "TW": len(self.symbols("TW")),
                "US": len(self.symbols("US")),
            },
            "streams": status,
            "latest_sequence": self.store.latest_sequence,
            "alerts": self.store.list_after(after, limit=limit),
        }
