"""Independent intraday capital-flow shadow ledger for TW and US trades.

The ledger estimates buyer/seller initiated turnover from quotes and the tick
rule.  It cannot identify the beneficial owner of a trade and never changes
rankings or places orders.  Taiwan and US data are always aggregated and
reported separately.
"""

from __future__ import annotations

from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from datetime import datetime, time as datetime_time, timedelta, timezone
import json
import math
from pathlib import Path
import re
import threading
import time
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo


WINDOWS = {"1m": 60, "5m": 300, "15m": 900, "60m": 3600}
MARKETS = ("TW", "US")
BUCKET_SECONDS = 30
NON_DIRECTIONAL_CONDITIONS = frozenset({"4", "7", "B", "M", "P", "Q", "U", "W", "Z"})
DAILY_RETENTION_DAYS = 10
MARKET_CLOCKS = {
    "TW": (ZoneInfo("Asia/Taipei"), datetime_time(9, 0), datetime_time(13, 30)),
    "US": (ZoneInfo("America/New_York"), datetime_time(9, 30), datetime_time(16, 0)),
}


def normalize_theme(value: Any) -> str:
    """Collapse cosmetic prefixes so one sector cannot occupy two ranks."""
    text = re.sub(r"\s+", " ", str(value or "未分類")).strip()
    text = re.sub(r"^[^0-9A-Za-z\u4e00-\u9fff]+\s*", "", text).strip()
    return text or "未分類"


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _conditions(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(part.strip() for part in value.split(",") if part.strip())
    if isinstance(value, (list, tuple, set)):
        return tuple(str(part).strip() for part in value if str(part).strip())
    return ()


def is_directional_trade_conditions(value: Any) -> bool:
    """Return False for prints unsuitable for current buy/sell pressure."""
    return not bool(set(_conditions(value)) & NON_DIRECTIONAL_CONDITIONS)


@dataclass
class FlowBucket:
    started_at: int
    ended_at: int | None = None
    buy_value: float = 0.0
    sell_value: float = 0.0
    neutral_value: float = 0.0
    buy_volume: float = 0.0
    sell_volume: float = 0.0
    neutral_volume: float = 0.0
    trade_count: int = 0
    eligible_trade_count: int = 0
    filtered_trade_count: int = 0
    buy_trade_count: int = 0
    sell_trade_count: int = 0
    neutral_trade_count: int = 0
    first_price: float | None = None
    last_price: float | None = None

    def add(self, *, side: int, value: float, volume: float, price: float, eligible: bool) -> None:
        self.trade_count += 1
        if self.first_price is None:
            self.first_price = price
        self.last_price = price
        if not eligible:
            self.filtered_trade_count += 1
            return
        self.eligible_trade_count += 1
        if side > 0:
            self.buy_value += value
            self.buy_volume += volume
            self.buy_trade_count += 1
        elif side < 0:
            self.sell_value += value
            self.sell_volume += volume
            self.sell_trade_count += 1
        else:
            self.neutral_value += value
            self.neutral_volume += volume
            self.neutral_trade_count += 1

    def subtract(self, *, side: int, value: float, volume: float, eligible: bool) -> None:
        self.trade_count = max(0, self.trade_count - 1)
        if not eligible:
            self.filtered_trade_count = max(0, self.filtered_trade_count - 1)
            return
        self.eligible_trade_count = max(0, self.eligible_trade_count - 1)
        if side > 0:
            self.buy_value = max(0.0, self.buy_value - value)
            self.buy_volume = max(0.0, self.buy_volume - volume)
            self.buy_trade_count = max(0, self.buy_trade_count - 1)
        elif side < 0:
            self.sell_value = max(0.0, self.sell_value - value)
            self.sell_volume = max(0.0, self.sell_volume - volume)
            self.sell_trade_count = max(0, self.sell_trade_count - 1)
        else:
            self.neutral_value = max(0.0, self.neutral_value - value)
            self.neutral_volume = max(0.0, self.neutral_volume - volume)
            self.neutral_trade_count = max(0, self.neutral_trade_count - 1)


@dataclass(frozen=True)
class FlowContribution:
    symbol: str
    bucket_at: int
    session_date: str | None
    side: int
    value: float
    volume: float
    eligible: bool


@dataclass(frozen=True)
class CapitalFlowConfig:
    retention_seconds: int = 3900
    quote_max_age_seconds: float = 3.0
    trade_id_limit: int = 20_000
    persist_interval_seconds: float = 60.0
    # These SIP prints are not reliable evidence of current directional demand:
    # average/derivative/contingent/official open-close/prior-reference/out-of-sequence.
    non_directional_conditions: frozenset[str] = NON_DIRECTIONAL_CONDITIONS


class CapitalFlowShadow:
    """Thread-safe, thirty-second-bucketed shadow ledger for two markets."""

    def __init__(
        self,
        baselines: dict[str, Any],
        *,
        config: CapitalFlowConfig | None = None,
        state_path: Path | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.baselines = baselines
        self.config = config or CapitalFlowConfig()
        self.state_path = state_path
        self.clock = clock
        self._buckets: dict[str, dict[int, FlowBucket]] = defaultdict(dict)
        self._daily: dict[str, dict[str, FlowBucket]] = defaultdict(dict)
        self._quotes: dict[str, tuple[float | None, float | None, float]] = {}
        self._last_price: dict[str, float] = {}
        self._last_side: dict[str, int] = {}
        self._trade_ids: OrderedDict[str, FlowContribution] = OrderedDict()
        self._market_counts = {market: 0 for market in MARKETS}
        self._market_last_at: dict[str, float | None] = {market: None for market in MARKETS}
        self._started_at = self.clock()
        self._last_persisted_at = 0.0
        self._last_pruned_at = 0.0
        self._dirty = False
        self._lock = threading.RLock()
        self._load()

    @staticmethod
    def _regular_session(market: str, at: float) -> tuple[str, bool]:
        zone, opened, closed = MARKET_CLOCKS[market]
        local = datetime.fromtimestamp(at, zone)
        in_regular_hours = (
            local.weekday() < 5 and opened <= local.time().replace(tzinfo=None) <= closed
        )
        return local.date().isoformat(), in_regular_hours

    def update_quote(
        self,
        symbol: str,
        *,
        bid: Any = None,
        ask: Any = None,
        timestamp: float | None = None,
    ) -> None:
        at = self.clock() if timestamp is None else float(timestamp)
        with self._lock:
            self._quotes[symbol] = (_finite(bid), _finite(ask), at)

    def _classify(
        self,
        symbol: str,
        price: float,
        at: float,
        bid: float | None,
        ask: float | None,
    ) -> tuple[int, str]:
        stored_bid, stored_ask, quote_at = self._quotes.get(symbol, (None, None, 0.0))
        if bid is None and at - quote_at <= self.config.quote_max_age_seconds:
            bid = stored_bid
        if ask is None and at - quote_at <= self.config.quote_max_age_seconds:
            ask = stored_ask
        tolerance = max(abs(price) * 1e-8, 1e-9)
        if ask is not None and ask > 0 and price >= ask - tolerance:
            return 1, "at_ask"
        if bid is not None and bid > 0 and price <= bid + tolerance:
            return -1, "at_bid"
        previous = self._last_price.get(symbol)
        if previous is not None and price != previous:
            return (1, "uptick") if price > previous else (-1, "downtick")
        previous_side = self._last_side.get(symbol)
        if previous_side:
            return previous_side, "tick_carry"
        return 0, "unclassified"

    def process_trade(
        self,
        symbol: str,
        *,
        price: Any,
        size: Any,
        timestamp: float | None = None,
        bid: Any = None,
        ask: Any = None,
        trade_id: Any = None,
        conditions: Any = None,
        **_metadata: Any,
    ) -> dict[str, Any] | None:
        trade_price = _finite(price)
        trade_size = _finite(size)
        baseline = self.baselines.get(symbol)
        if baseline is None or trade_price is None or trade_size is None:
            return None
        if trade_price <= 0 or trade_size <= 0:
            return None
        at = self.clock() if timestamp is None else float(timestamp)
        market = str(getattr(baseline, "market", ""))
        if market not in MARKETS:
            return None
        condition_codes = _conditions(conditions)
        eligible = not bool(set(condition_codes) & self.config.non_directional_conditions)
        incoming_bid, incoming_ask = _finite(bid), _finite(ask)
        key = f"{market}:{symbol}:{trade_id}" if trade_id not in (None, "") else ""
        with self._lock:
            if key and key in self._trade_ids:
                return None
            side, classification = self._classify(
                symbol, trade_price, at, incoming_bid, incoming_ask
            )
            self._last_price[symbol] = trade_price
            if side:
                self._last_side[symbol] = side
            if incoming_bid is not None or incoming_ask is not None:
                self._quotes[symbol] = (incoming_bid, incoming_ask, at)
            bucket_at = int(at // BUCKET_SECONDS * BUCKET_SECONDS)
            bucket = self._buckets[symbol].setdefault(bucket_at, FlowBucket(bucket_at))
            value = trade_price * trade_size
            bucket.add(side=side, value=value, volume=trade_size, price=trade_price, eligible=eligible)
            session_date, in_regular_hours = self._regular_session(market, at)
            if in_regular_hours:
                daily = self._daily[session_date].setdefault(
                    symbol, FlowBucket(int(at))
                )
                daily.started_at = min(daily.started_at, int(at))
                daily.ended_at = max(daily.ended_at or int(at), int(at))
                daily.add(
                    side=side, value=value, volume=trade_size,
                    price=trade_price, eligible=eligible,
                )
            self._market_counts[market] += 1
            self._market_last_at[market] = at
            if key:
                self._trade_ids[key] = FlowContribution(
                    symbol=symbol,
                    bucket_at=bucket_at,
                    session_date=session_date if in_regular_hours else None,
                    side=side,
                    value=value,
                    volume=trade_size,
                    eligible=eligible,
                )
                while len(self._trade_ids) > self.config.trade_id_limit:
                    self._trade_ids.popitem(last=False)
            if at - self._last_pruned_at >= BUCKET_SECONDS:
                self._prune(at)
                self._last_pruned_at = at
            self._dirty = True
            self._persist_if_due(at)
            return {
                "symbol": symbol,
                "market": market,
                "side": "buy" if side > 0 else "sell" if side < 0 else "neutral",
                "classification": classification,
                "eligible": eligible,
                "conditions": list(condition_codes),
                "value": round(value, 2),
            }

    def cancel_trade(self, symbol: str, trade_id: Any, *, market: str = "US") -> bool:
        key = f"{market}:{symbol}:{trade_id}"
        with self._lock:
            contribution = self._trade_ids.pop(key, None)
            if contribution is None:
                return False
            bucket = self._buckets.get(symbol, {}).get(contribution.bucket_at)
            if bucket is not None:
                bucket.subtract(
                    side=contribution.side,
                    value=contribution.value,
                    volume=contribution.volume,
                    eligible=contribution.eligible,
                )
            if contribution.session_date:
                daily = self._daily.get(contribution.session_date, {}).get(symbol)
                if daily is not None:
                    daily.subtract(
                        side=contribution.side,
                        value=contribution.value,
                        volume=contribution.volume,
                        eligible=contribution.eligible,
                    )
            self._dirty = True
            return True

    def correct_trade(
        self,
        symbol: str,
        *,
        original_trade_id: Any,
        corrected_trade_id: Any,
        price: Any,
        size: Any,
        timestamp: float | None = None,
        conditions: Any = None,
        market: str = "US",
        **metadata: Any,
    ) -> dict[str, Any] | None:
        self.cancel_trade(symbol, original_trade_id, market=market)
        return self.process_trade(
            symbol,
            price=price,
            size=size,
            timestamp=timestamp,
            trade_id=corrected_trade_id,
            conditions=conditions,
            **metadata,
        )

    def _prune(self, at: float) -> None:
        cutoff = at - self.config.retention_seconds
        for symbol in list(self._buckets):
            buckets = self._buckets[symbol]
            for started_at in list(buckets):
                if started_at + BUCKET_SECONDS < cutoff:
                    del buckets[started_at]
            if not buckets:
                del self._buckets[symbol]
        oldest = (datetime.fromtimestamp(at, timezone.utc).date() - timedelta(
            days=DAILY_RETENTION_DAYS + 2
        )).isoformat()
        for session_date in list(self._daily):
            if session_date < oldest:
                del self._daily[session_date]

    def _symbol_row(self, symbol: str, buckets: Iterable[FlowBucket]) -> dict[str, Any] | None:
        selected = list(buckets)
        if not selected:
            return None
        baseline = self.baselines[symbol]
        buy = sum(row.buy_value for row in selected)
        sell = sum(row.sell_value for row in selected)
        neutral = sum(row.neutral_value for row in selected)
        directional = buy + sell
        total = directional + neutral
        net = buy - sell
        trade_count = sum(row.trade_count for row in selected)
        eligible_count = sum(row.eligible_trade_count for row in selected)
        filtered_count = sum(row.filtered_trade_count for row in selected)
        first_price = next((row.first_price for row in selected if row.first_price is not None), None)
        last_price = next((row.last_price for row in reversed(selected) if row.last_price is not None), None)
        price_change = (
            (last_price / first_price - 1) * 100
            if first_price and last_price is not None else 0.0
        )
        direction = 1 if net > 0 else -1 if net < 0 else 0
        active = [row for row in selected if row.buy_value + row.sell_value > 0]
        persistent = sum(
            1 for row in active
            if (row.buy_value - row.sell_value) * direction > 0
        )
        persistence = persistent / len(active) * 100 if active and direction else 0.0
        buy_ratio = buy / directional * 100 if directional else 0.0
        daily_value = max(float(getattr(baseline, "average_daily_value", 0.0)), 0.0)
        daily_ratio = net / daily_value * 100 if daily_value else 0.0
        net_ratio = net / directional * 100 if directional else 0.0
        price_confirm = 100.0 if price_change * direction > 0 else 50.0 if not price_change else 0.0
        confidence = min(100.0, max(0.0,
            persistence * 0.35
            + min(100.0, abs(net_ratio)) * 0.30
            + price_confirm * 0.20
            + min(100.0, math.sqrt(max(eligible_count, 0)) * 8) * 0.15
        ))
        return {
            "symbol": symbol,
            "name": str(getattr(baseline, "name", symbol)),
            "market": str(getattr(baseline, "market", "")),
            "theme": normalize_theme(getattr(baseline, "theme", "未分類")),
            "asset_type": str(getattr(baseline, "asset_type", "個股") or "個股"),
            "buy_value": round(buy, 2),
            "sell_value": round(sell, 2),
            "neutral_value": round(neutral, 2),
            "net_flow": round(net, 2),
            "buy_ratio_pct": round(buy_ratio, 2),
            "net_directional_ratio_pct": round(net_ratio, 2),
            "net_to_average_daily_value_pct": round(daily_ratio, 4),
            "persistence_pct": round(persistence, 2),
            "price_change_pct": round(price_change, 4),
            "confidence": round(confidence, 1),
            "trade_count": trade_count,
            "eligible_trade_count": eligible_count,
            "filtered_trade_count": filtered_count,
            "first_price": first_price,
            "last_price": last_price,
            "estimated_only": True,
        }

    def _summarize_rows(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        buy = sum(row["buy_value"] for row in rows)
        sell = sum(row["sell_value"] for row in rows)
        neutral = sum(row["neutral_value"] for row in rows)
        directional = buy + sell
        positive = sum(1 for row in rows if row["net_flow"] > 0)
        negative = sum(1 for row in rows if row["net_flow"] < 0)
        active = positive + negative
        breadth = positive / active * 100 if active else 0.0
        buy_ratio = buy / directional * 100 if directional else 0.0
        if buy_ratio >= 58 and breadth >= 55:
            direction_label = "資金偏多"
        elif buy_ratio <= 42 and breadth <= 45:
            direction_label = "資金偏空"
        else:
            direction_label = "多空拉鋸"

        themes: list[dict[str, Any]] = []
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[row["theme"]].append(row)
        for theme, members in grouped.items():
            theme_buy = sum(row["buy_value"] for row in members)
            theme_sell = sum(row["sell_value"] for row in members)
            theme_directional = theme_buy + theme_sell
            positive_members = sum(1 for row in members if row["net_flow"] > 0)
            negative_members = sum(1 for row in members if row["net_flow"] < 0)
            theme_net = theme_buy - theme_sell
            directional_members = positive_members if theme_net >= 0 else negative_members
            themes.append({
                "theme": theme,
                "member_count": len(members),
                "positive_symbols": positive_members,
                "negative_symbols": negative_members,
                "net_flow": round(theme_net, 2),
                "buy_ratio_pct": round(theme_buy / theme_directional * 100, 2) if theme_directional else 0.0,
                "resonance": directional_members >= 3 and directional_members / len(members) >= 0.60,
                "symbols": [
                    row["symbol"] for row in sorted(
                        members,
                        key=lambda item: item["net_flow"],
                        reverse=theme_net >= 0,
                    )[:5]
                ],
            })
        inflows = sorted(
            (row for row in rows if row["net_flow"] > 0),
            key=lambda row: (
                row["confidence"], row["net_to_average_daily_value_pct"], row["net_flow"]
            ),
            reverse=True,
        )[:10]
        outflows = sorted(
            (row for row in rows if row["net_flow"] < 0),
            key=lambda row: (
                row["confidence"], abs(row["net_to_average_daily_value_pct"]), abs(row["net_flow"])
            ),
            reverse=True,
        )[:10]
        theme_inflows = sorted(
            (row for row in themes if row["net_flow"] > 0),
            key=lambda row: (row["resonance"], row["buy_ratio_pct"], row["net_flow"]),
            reverse=True,
        )[:10]
        theme_outflows = sorted(
            (row for row in themes if row["net_flow"] < 0),
            key=lambda row: (100 - row["buy_ratio_pct"], abs(row["net_flow"])),
            reverse=True,
        )[:10]
        asset_groups = {}
        for asset_label, predicate in {
            "ETF": lambda row: "ETF" in row["asset_type"].upper(),
            "STOCK": lambda row: "ETF" not in row["asset_type"].upper(),
        }.items():
            members = [row for row in rows if predicate(row)]
            group_buy = sum(row["buy_value"] for row in members)
            group_sell = sum(row["sell_value"] for row in members)
            asset_groups[asset_label] = {
                "active_symbols": len(members),
                "net_flow": round(group_buy - group_sell, 2),
                "buy_ratio_pct": round(group_buy / (group_buy + group_sell) * 100, 2)
                if group_buy + group_sell else 0.0,
            }
        return {
            "direction": direction_label,
            "buy_value": round(buy, 2),
            "sell_value": round(sell, 2),
            "neutral_value": round(neutral, 2),
            "net_flow": round(buy - sell, 2),
            "buy_ratio_pct": round(buy_ratio, 2),
            "positive_symbols": positive,
            "negative_symbols": negative,
            "active_symbols": len(rows),
            "positive_breadth_pct": round(breadth, 2),
            "trade_count": sum(row["trade_count"] for row in rows),
            "eligible_trade_count": sum(row["eligible_trade_count"] for row in rows),
            "filtered_trade_count": sum(row["filtered_trade_count"] for row in rows),
            "asset_groups": asset_groups,
            "themes": sorted(themes, key=lambda row: row["theme"]),
            "top_inflows": inflows,
            "top_outflows": outflows,
            "theme_inflows": theme_inflows,
            "theme_outflows": theme_outflows,
        }

    def _window_snapshot(self, market: str, seconds: int, at: float) -> dict[str, Any]:
        cutoff = at - seconds
        rows: list[dict[str, Any]] = []
        for symbol, baseline in self.baselines.items():
            if str(getattr(baseline, "market", "")) != market:
                continue
            buckets = [
                row for started_at, row in sorted(self._buckets.get(symbol, {}).items())
                if started_at + BUCKET_SECONDS >= cutoff and started_at <= at
            ]
            result = self._symbol_row(symbol, buckets)
            if result and result["trade_count"]:
                rows.append(result)
        return {"seconds": seconds, **self._summarize_rows(rows)}

    def _daily_snapshot(self, market: str, session_date: str, at: float) -> dict[str, Any] | None:
        zone, opened, closed = MARKET_CLOCKS[market]
        try:
            day = datetime.fromisoformat(session_date).date()
        except ValueError:
            return None
        close_at = datetime.combine(day, closed, zone).timestamp()
        if at < close_at:
            return None
        rows: list[dict[str, Any]] = []
        first_at: int | None = None
        last_at: int | None = None
        for symbol, bucket in self._daily.get(session_date, {}).items():
            baseline = self.baselines.get(symbol)
            if baseline is None or str(getattr(baseline, "market", "")) != market:
                continue
            result = self._symbol_row(symbol, [bucket])
            if result and result["trade_count"]:
                rows.append(result)
                first_at = bucket.started_at if first_at is None else min(first_at, bucket.started_at)
                ended_at = bucket.ended_at or bucket.started_at
                last_at = ended_at if last_at is None else max(last_at, ended_at)
        summary = self._summarize_rows(rows)
        opened_at = datetime.combine(day, opened, zone).timestamp()
        coverage_open = first_at is not None and first_at <= opened_at + 30 * 60
        coverage_close = last_at is not None and last_at >= close_at - 30 * 60
        complete = bool(
            coverage_open and coverage_close
            and summary["positive_symbols"] + summary["negative_symbols"] >= 10
            and summary["eligible_trade_count"] > 0
        )
        return {
            "market": market,
            "session_date": session_date,
            "source": "Fubon Neo" if market == "TW" else "Alpaca SIP",
            "session_scope": "regular_hours_only",
            "closed": True,
            "complete": complete,
            "quality": {
                "opening_coverage": coverage_open,
                "closing_coverage": coverage_close,
                "minimum_active_symbols": 10,
            },
            "first_trade_at": (
                datetime.fromtimestamp(first_at, timezone.utc).isoformat(timespec="seconds")
                if first_at is not None else None
            ),
            "last_trade_at": (
                datetime.fromtimestamp(last_at, timezone.utc).isoformat(timespec="seconds")
                if last_at is not None else None
            ),
            **summary,
        }

    def closed_daily_snapshots(self, *, now: float | None = None) -> dict[str, Any]:
        """Return only completed regular sessions; safe for daily report linkage."""
        at = self.clock() if now is None else float(now)
        with self._lock:
            sessions = {market: [] for market in MARKETS}
            for session_date in sorted(self._daily, reverse=True):
                for market in MARKETS:
                    result = self._daily_snapshot(market, session_date, at)
                    if result is not None and result["trade_count"]:
                        sessions[market].append(result)
                        sessions[market] = sessions[market][:DAILY_RETENTION_DAYS]
            return {
                "version": 1,
                "mode": "closed_session_shadow_only",
                "updated_at": datetime.fromtimestamp(at, timezone.utc).isoformat(timespec="seconds"),
                "policy": {
                    "intraday_exposed": False,
                    "regular_hours_only": True,
                    "markets_separate": True,
                    "formal_ranking_locked": True,
                    "places_orders": False,
                },
                "markets": sessions,
            }

    def snapshot(self, *, now: float | None = None) -> dict[str, Any]:
        at = self.clock() if now is None else float(now)
        with self._lock:
            self._prune(at)
            self._last_pruned_at = max(self._last_pruned_at, at)
            markets = {
                market: {
                    "market": market,
                    "currency": "TWD" if market == "TW" else "USD",
                    "source": "Fubon Neo" if market == "TW" else "Alpaca SIP",
                    "trades_processed": self._market_counts[market],
                    "last_trade_at": (
                        datetime.fromtimestamp(self._market_last_at[market], timezone.utc).isoformat(timespec="seconds")
                        if self._market_last_at[market] is not None else None
                    ),
                    "windows": {
                        label: self._window_snapshot(market, seconds, at)
                        for label, seconds in WINDOWS.items()
                    },
                }
                for market in MARKETS
            }
            self._persist_if_due(at)
            return {
                "version": 1,
                "mode": "shadow_only",
                "updated_at": datetime.fromtimestamp(at, timezone.utc).isoformat(timespec="seconds"),
                "started_at": datetime.fromtimestamp(self._started_at, timezone.utc).isoformat(timespec="seconds"),
                "markets": markets,
                "policy": {
                    "markets_separate": True,
                    "windows": list(WINDOWS),
                    "classification": "quote_then_tick_rule",
                    "identity_known": False,
                    "changes_rankings": False,
                    "places_orders": False,
                    "theme_resonance_min_symbols": 3,
                    "note": "僅推估成交方向與資金輪動；不能識別外資、法人、主力或真實帳戶。",
                },
            }

    def _persist_if_due(self, at: float) -> None:
        if self.state_path is None or not self._dirty:
            return
        if at - self._last_persisted_at < self.config.persist_interval_seconds:
            return
        payload = {
            "version": 1,
            "saved_at": at,
            "started_at": self._started_at,
            "market_counts": self._market_counts,
            "market_last_at": self._market_last_at,
            "last_price": self._last_price,
            "last_side": self._last_side,
            "buckets": {
                symbol: [[
                    started,
                    bucket.buy_value, bucket.sell_value, bucket.neutral_value,
                    bucket.buy_volume, bucket.sell_volume, bucket.neutral_volume,
                    bucket.trade_count, bucket.eligible_trade_count, bucket.filtered_trade_count,
                    bucket.buy_trade_count, bucket.sell_trade_count, bucket.neutral_trade_count,
                    bucket.first_price, bucket.last_price,
                ] for started, bucket in buckets.items()]
                for symbol, buckets in self._buckets.items()
            },
            "daily": {
                session_date: {
                    symbol: {
                        field: getattr(bucket, field)
                        for field in FlowBucket.__dataclass_fields__
                    }
                    for symbol, bucket in symbols.items()
                }
                for session_date, symbols in self._daily.items()
            },
        }
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.state_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            temporary.replace(self.state_path)
        except OSError:
            return
        self._last_persisted_at = at
        self._dirty = False

    def _load(self) -> None:
        if self.state_path is None:
            return
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return
        if not isinstance(payload, dict):
            return
        now = self.clock()
        cutoff = now - self.config.retention_seconds
        try:
            allowed = set(FlowBucket.__dataclass_fields__)
            for session_date, symbols in (payload.get("daily") or {}).items():
                if not isinstance(symbols, dict):
                    continue
                for symbol, values in symbols.items():
                    if symbol not in self.baselines or not isinstance(values, dict):
                        continue
                    clean = {key: value for key, value in values.items() if key in allowed}
                    self._daily[str(session_date)][symbol] = FlowBucket(**clean)
            for symbol, rows in (payload.get("buckets") or {}).items():
                if symbol not in self.baselines or not isinstance(rows, (dict, list)):
                    continue
                if isinstance(rows, dict):
                    iterator = rows.items()
                else:
                    iterator = ((row[0], row) for row in rows if isinstance(row, list) and len(row) == 15)
                for started_text, values in iterator:
                    started = int(started_text)
                    if started + BUCKET_SECONDS < cutoff:
                        continue
                    if isinstance(values, dict):
                        clean = {key: value for key, value in values.items() if key in allowed}
                        bucket = FlowBucket(**clean)
                    else:
                        bucket = FlowBucket(
                            started_at=started,
                            buy_value=values[1], sell_value=values[2], neutral_value=values[3],
                            buy_volume=values[4], sell_volume=values[5], neutral_volume=values[6],
                            trade_count=values[7], eligible_trade_count=values[8], filtered_trade_count=values[9],
                            buy_trade_count=values[10], sell_trade_count=values[11], neutral_trade_count=values[12],
                            first_price=values[13], last_price=values[14],
                        )
                    self._buckets[symbol][started] = bucket
            self._started_at = float(payload.get("started_at") or now)
            for market in MARKETS:
                self._market_counts[market] = int((payload.get("market_counts") or {}).get(market) or 0)
                last = _finite((payload.get("market_last_at") or {}).get(market))
                self._market_last_at[market] = last
            self._last_price.update({
                symbol: float(value) for symbol, value in (payload.get("last_price") or {}).items()
                if symbol in self.baselines and _finite(value) is not None
            })
            self._last_side.update({
                symbol: int(value) for symbol, value in (payload.get("last_side") or {}).items()
                if symbol in self.baselines and value in (-1, 1)
            })
        except (TypeError, ValueError):
            self._buckets.clear()
