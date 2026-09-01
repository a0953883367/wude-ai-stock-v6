"""Ten-second aggressive buy/sell detector shared by Taiwan and US streams.

The detector never places orders and never changes ranking data.  A trade is
classified by the latest bid/ask; the tick rule is used only when the book is
unavailable. Alerts are emitted for a large print or a 3-5 print cluster in
either direction inside a rolling ten-second window.
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

from capital_flow_shadow import (
    MARKET_CLOCKS,
    CapitalFlowShadow,
    is_directional_trade_conditions,
    market_session_phase,
)
from flow_weight_shadow import FlowWeightShadow
from inverse_etf_shadow import build_live_overlay


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
    # US single prints only use the separate block threshold below.  Keeping
    # this as None prevents the former USD 100,000 general alert from firing.
    single_min_usd: float | None = None
    cluster_min_twd: float = 5_000_000.0
    cluster_min_usd: float = 250_000.0
    block_single_min_twd: float = 10_000_000.0
    block_single_min_usd: float = 1_000_000.0
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
    theme: str = "未分類"
    asset_type: str = "個股"

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
        self._last_alert_at: dict[tuple[str, bool], float] = {}
        self._lock = threading.RLock()

    def update_quote(self, symbol: str, *, bid: Any = None, ask: Any = None) -> None:
        with self._lock:
            self._quotes[symbol] = (_finite(bid), _finite(ask))

    def reset_market(self, market: str) -> None:
        """Drop raw short-window prints and quote state at a session boundary."""
        market = str(market or "").upper()
        symbols = {
            symbol for symbol, baseline in self.baselines.items()
            if baseline.market == market
        }
        with self._lock:
            for symbol in symbols:
                self._windows.pop(symbol, None)
                self._quotes.pop(symbol, None)
                self._last_price.pop(symbol, None)
                self._last_side.pop(symbol, None)
            for key in list(self._last_alert_at):
                if key[0] in symbols:
                    del self._last_alert_at[key]

    def _thresholds(self, baseline: StockBaseline) -> tuple[float | None, float]:
        daily_value = baseline.average_daily_value
        if baseline.market == "TW":
            single_floor = self.config.single_min_twd
            cluster_floor = self.config.cluster_min_twd
        else:
            single_floor = self.config.single_min_usd
            cluster_floor = self.config.cluster_min_usd
        single_threshold = (
            max(single_floor, daily_value * self.config.single_daily_value_ratio)
            if single_floor is not None else None
        )
        return (
            single_threshold,
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
        conditions: Any = None,
        **_metadata: Any,
    ) -> dict[str, Any] | None:
        trade_price = _finite(price)
        trade_size = _finite(size)
        if trade_price is None or trade_size is None or trade_price <= 0 or trade_size <= 0:
            return None
        if not is_directional_trade_conditions(conditions):
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

            cooldown_key = (symbol, is_buy)
            if at - self._last_alert_at.get(cooldown_key, float("-inf")) < self.config.cooldown_seconds:
                return None

            single_threshold, cluster_threshold = self._thresholds(baseline)
            block_single_threshold = (
                self.config.block_single_min_twd
                if baseline.market == "TW"
                else self.config.block_single_min_usd
            )
            trigger_type = ""
            selected: list[TradePrint] = []
            if (
                trade.value >= block_single_threshold
                or (single_threshold is not None and trade.value >= single_threshold)
            ):
                trigger_type = "single"
                selected = [trade]
            else:
                directional_trades = [
                    item for item in window if item.is_aggressive_buy is is_buy
                ]
                selected = directional_trades[-self.config.cluster_max_trades:]
                total_value = sum(item.value for item in window)
                selected_value = sum(item.value for item in selected)
                directional_ratio = selected_value / total_value if total_value > 0 else 0.0
                if (
                    self.config.cluster_min_trades <= len(selected) <= self.config.cluster_max_trades
                    and selected_value >= cluster_threshold
                    and directional_ratio >= self.config.cluster_buy_ratio_min
                ):
                    trigger_type = "cluster"
            if not trigger_type:
                return None

            total_window_value = sum(item.value for item in window)
            aggressive_buy_value = sum(item.value for item in window if item.is_aggressive_buy)
            aggressive_sell_value = sum(item.value for item in window if not item.is_aggressive_buy)
            alert_value = sum(item.value for item in selected)
            is_block_trade = trigger_type == "single" and trade.value >= block_single_threshold
            self._last_alert_at[cooldown_key] = at
            side = "buy" if is_buy else "sell"
            side_text = "大買" if is_buy else "大賣"
            return {
                "symbol": baseline.symbol,
                "name": baseline.name,
                "market": baseline.market,
                "alert_side": side,
                "trigger_type": trigger_type,
                "trigger_label": f"單筆{side_text}" if trigger_type == "single" else f"{len(selected)}筆連續{side_text}",
                "trade_count": len(selected),
                "window_seconds": self.config.window_seconds,
                "price": trade_price,
                "shares": sum(item.size for item in selected),
                "value": round(alert_value, 2),
                "buy_value": round(alert_value, 2) if is_buy else 0.0,
                "sell_value": round(alert_value, 2) if not is_buy else 0.0,
                "window_total_value": round(total_window_value, 2),
                "window_aggressive_buy_value": round(aggressive_buy_value, 2),
                "window_aggressive_sell_value": round(aggressive_sell_value, 2),
                "aggressive_buy_ratio_pct": round(
                    aggressive_buy_value / total_window_value * 100, 2
                ) if total_window_value else 0.0,
                "aggressive_sell_ratio_pct": round(
                    aggressive_sell_value / total_window_value * 100, 2
                ) if total_window_value else 0.0,
                "single_threshold": round(
                    min(single_threshold, block_single_threshold)
                    if single_threshold is not None else block_single_threshold,
                    2,
                ),
                "general_single_threshold": (
                    round(single_threshold, 2) if single_threshold is not None else None
                ),
                "cluster_threshold": round(cluster_threshold, 2),
                "is_block_trade": is_block_trade,
                "block_trade_threshold": round(block_single_threshold, 2),
                "block_trade_label": f"單筆巨額{side_text}" if is_block_trade else None,
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
            theme=str(row.get("theme") or row.get("industry") or "未分類"),
            asset_type=str(row.get("type") or "個股"),
        )
    return output


def format_large_buy_telegram(alert: dict[str, Any]) -> str:
    currency = "TWD" if alert.get("market") == "TW" else "USD"
    side = "sell" if alert.get("alert_side") == "sell" else "buy"
    side_text = "賣出" if side == "sell" else "買進"
    value_key = "sell_value" if side == "sell" else "buy_value"
    ratio_key = "aggressive_sell_ratio_pct" if side == "sell" else "aggressive_buy_ratio_pct"
    value = float(alert.get(value_key) or 0)
    ratio = float(alert.get(ratio_key) or 0)
    is_block_trade = bool(alert.get("is_block_trade"))
    label = alert.get("block_trade_label") if is_block_trade else alert.get("trigger_label")
    phase = str(alert.get("session_phase") or "")
    phase_text = "盤前" if phase == "premarket" else "正式盤" if phase == "regular" else ""
    return "\n".join([
        f"{'💥' if is_block_trade else ('🔻' if side == 'sell' else '🚨')} 10秒大量主動{side_text}｜{label}",
        *([f"交易時段｜{phase_text}"] if phase_text else []),
        f"{alert.get('name')} {alert.get('symbol')}｜{alert.get('market')}",
        f"成交價 {float(alert.get('price') or 0):,.2f}｜{int(alert.get('trade_count') or 0)} 筆",
        f"主動{side_text}金額 {currency} {value:,.0f}｜{side_text}占比 {ratio:.1f}%",
        f"偵測時間 {alert.get('detected_at')}｜僅為即時量價警示，不代表主力身分或交易建議",
    ])


class LargeBuyAlertService:
    def __init__(
        self,
        report_path: Path,
        state_path: Path,
        *,
        flow_state_path: Path | None = None,
        weight_shadow_state_path: Path | None = None,
        weight_shadow_validation_enabled: bool = True,
        config: LargeBuyConfig | None = None,
        notifier: Callable[[str], Any] | None = None,
        alert_notifier: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        self.report_path = report_path
        self.config = config or LargeBuyConfig()
        self.baselines = load_stock_baselines(report_path)
        self.detector = LargeBuyDetector(self.baselines, config=self.config)
        self.store = JsonAlertStore(state_path, limit=self.config.alert_history_limit)
        self.flow = CapitalFlowShadow(
            self.baselines,
            state_path=flow_state_path or state_path.with_name("capital_flow_shadow.json"),
        )
        self.weight_shadow = FlowWeightShadow(
            report_path,
            weight_shadow_state_path or state_path.with_name("flow_weight_shadow.json"),
            validation_enabled=weight_shadow_validation_enabled,
        )
        self.notifier = notifier
        self.alert_notifier = alert_notifier
        self._status: dict[str, dict[str, Any]] = {
            market: {"state": "waiting", "subscribed": 0, "error": None}
            for market in MARKETS
        }
        self._session_phases = {market: "closed" for market in MARKETS}
        self._lock = threading.RLock()

    def symbols(self, market: str) -> list[str]:
        return [row.symbol for row in self.baselines.values() if row.market == market]

    def market_phase(self, market: str, *, timestamp: float | None = None) -> str:
        """Use exchange-local time and the verified calendar when it is available."""
        at = time.time() if timestamp is None else float(timestamp)
        market = str(market or "").upper()
        phase = market_session_phase(market, at)
        if phase == "closed" or market not in MARKETS:
            return "closed"
        zone = MARKET_CLOCKS[market][0]
        local = datetime.fromtimestamp(at, zone)
        calendar = getattr(getattr(self, "weight_shadow", None), "calendar", None)
        if calendar is not None and hasattr(calendar, "session_status"):
            status = calendar.session_status(market, local.date().isoformat())
            if status.get("available") and not status.get("is_session"):
                return "closed"
            if market == "US" and phase == "regular" and status.get("available"):
                close_text = str(status.get("close") or "")
                if len(close_text) >= 5 and local.strftime("%H:%M") >= close_text[:5]:
                    return "closed"
        return phase

    def transition_market_phase(self, market: str, *, timestamp: float | None = None) -> str:
        at = time.time() if timestamp is None else float(timestamp)
        phase = self.market_phase(market, timestamp=at)
        phases = getattr(self, "_session_phases", None)
        if not isinstance(phases, dict):
            phases = {item: "closed" for item in MARKETS}
            self._session_phases = phases
        previous = phases.get(market, "closed")
        if previous != phase:
            self.flow.transition_market_phase(market, phase, now=at)
            if market == "US" and previous == "premarket" and phase == "regular":
                self.detector.reset_market(market)
            elif phase == "closed":
                self.detector.reset_market(market)
            phases[market] = phase
        return phase

    def set_stream_status(self, market: str, state: str, *, subscribed: int = 0, error: str | None = None) -> None:
        with self._lock:
            self._status[market] = {
                "state": state,
                "subscribed": subscribed,
                "error": error,
                "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }

    def update_quote(
        self,
        symbol: str,
        *,
        bid: Any = None,
        ask: Any = None,
        timestamp: float | None = None,
    ) -> None:
        self.detector.update_quote(symbol, bid=bid, ask=ask)
        self.flow.update_quote(symbol, bid=bid, ask=ask, timestamp=timestamp)

    def process_trade(self, symbol: str, **trade: Any) -> dict[str, Any] | None:
        baseline = self.baselines.get(symbol)
        parsed_at = _finite(trade.get("timestamp"))
        at = time.time() if parsed_at is None else parsed_at
        phase = self.transition_market_phase(
            baseline.market if baseline is not None else "", timestamp=at
        )
        self.flow.process_trade(symbol, **trade)
        if (
            phase == "regular" and baseline is not None
            and getattr(self, "weight_shadow", None) is not None
        ):
            self.weight_shadow.observe_trade(
                symbol,
                baseline.market,
                price=trade.get("price"),
                timestamp=trade.get("timestamp"),
            )
        alert = self.detector.process_trade(symbol, **trade)
        if alert is None:
            return None
        alert["session_phase"] = phase
        stored = self.store.append(alert)
        if phase == "regular" and getattr(self, "weight_shadow", None) is not None:
            self.weight_shadow.record_alert(stored)
        if self.notifier is not None:
            try:
                self.notifier(format_large_buy_telegram(stored))
            except Exception:
                # Stream processing must continue even when notification delivery fails.
                pass
        if getattr(self, "alert_notifier", None) is not None:
            try:
                self.alert_notifier(stored)
            except Exception:
                # A device notification failure must never stop market-data processing.
                pass
        return stored

    def cancel_trade(self, symbol: str, trade_id: Any, *, market: str = "US") -> bool:
        return self.flow.cancel_trade(symbol, trade_id, market=market)

    def correct_trade(self, symbol: str, **trade: Any) -> dict[str, Any] | None:
        return self.flow.correct_trade(symbol, **trade)

    def snapshot(self, *, after: int = 0, limit: int = 50) -> dict[str, Any]:
        with self._lock:
            status = json.loads(json.dumps(self._status))
        capital_flow = self.flow.snapshot()
        weight_shadow = (
            self.weight_shadow.snapshot(capital_flow)
            if getattr(self, "weight_shadow", None) is not None else None
        )
        inverse_live_shadow = None
        try:
            report_path = getattr(self, "report_path", None)
            if not isinstance(report_path, Path):
                raise OSError("inverse ETF report path unavailable")
            database = json.loads(
                report_path.with_name("inverse_etf_database.json").read_text(encoding="utf-8")
            )
            inverse_state = json.loads(
                report_path.with_name("inverse_etf_shadow.json").read_text(encoding="utf-8")
            )
            inverse_live_shadow = build_live_overlay(
                database,
                inverse_state,
                capital_flow,
                self.store.list_after(0, limit=100),
            )
        except (OSError, ValueError, TypeError):
            inverse_live_shadow = {
                "version": 1,
                "mode": "isolated_inverse_etf_live_overlay",
                "status": "data_unavailable",
                "policy": {
                    "formal_ranking_locked": True,
                    "flow_weight_shadow_unchanged": True,
                    "broker_orders": False,
                },
                "markets": {},
            }
        return {
            "version": 1,
            "policy": {
                "scope": "all_site_stocks",
                "notification_scope": "all_site_stocks",
                "selected_symbol_filter": False,
                "window_seconds": self.config.window_seconds,
                "single_or_cluster": True,
                "cluster_trade_count": [self.config.cluster_min_trades, self.config.cluster_max_trades],
                "general_single_thresholds": {
                    "TW": self.config.single_min_twd,
                    "US": self.config.single_min_usd,
                },
                "cluster_thresholds": {
                    "TW": self.config.cluster_min_twd,
                    "US": self.config.cluster_min_usd,
                },
                "block_single_thresholds": {
                    "TW": self.config.block_single_min_twd,
                    "US": self.config.block_single_min_usd,
                },
                "broker_orders": False,
                "us_session_policy": {
                    "premarket_open_new_york": "04:00",
                    "regular_open_new_york": "09:30",
                    "regular_close_new_york": "16:00",
                    "daylight_saving_taipei": ["16:00", "21:30", "04:00+1"],
                    "standard_time_taipei": ["17:00", "22:30", "05:00+1"],
                    "premarket_excluded_from_shadow_weight": True,
                },
                "resource_policy": {
                    "alpaca_shared_websocket": True,
                    "raw_trade_window_seconds": self.config.window_seconds,
                    "aggregate_retention_minutes": 60,
                    "alerts_keep_summary_only": True,
                    "same_symbol_direction_cooldown_seconds": self.config.cooldown_seconds,
                    "browser_background_polling": False,
                },
            },
            "universe": {
                "TW": len(self.symbols("TW")),
                "US": len(self.symbols("US")),
            },
            "streams": status,
            "latest_sequence": self.store.latest_sequence,
            "alerts": self.store.list_after(after, limit=limit),
            "capital_flow": capital_flow,
            "flow_weight_shadow": weight_shadow,
            "inverse_etf_live_shadow": inverse_live_shadow,
        }
