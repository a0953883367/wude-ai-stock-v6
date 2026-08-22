"""Fail-closed live Taiwan order engine.

The public UI may select symbols, but orders are only possible after the owner
explicitly arms this engine and all server-side Fubon/volume switches pass.
Every broker acknowledgement is persisted and fills are reconciled before a
position is created or reduced.  The engine never manages unrelated holdings.
"""

from __future__ import annotations

from datetime import datetime
import math
import os
import threading
from typing import Any, Callable, Protocol

from fubon_broker import FubonTradingSession, live_order_unlock_reason
from trade_engine import (
    JsonTradingStateStore,
    PaperTradingEngine,
    TradingPolicy,
    _number,
    normalize_trade_symbol,
)


LIVE_ARM_PHRASE = "我確認使用真錢下單"


class TradingBroker(Protocol):
    def available_cash(self) -> float: ...
    def inventories(self) -> list[dict[str, Any]]: ...
    def place_limit_order(self, *, symbol: str, side: str, quantity: int, price: float) -> list[dict[str, Any]]: ...
    def order_results(self) -> list[dict[str, Any]]: ...
    def cancel_order(self, order_no: str) -> list[dict[str, Any]]: ...


def _pending(order: dict[str, Any]) -> bool:
    return order.get("mode") == "live" and not bool(order.get("terminal"))


def _parse_time(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


class LiveTradingEngine(PaperTradingEngine):
    """Uses the paper engine's qualification/exit rules with real TW fills."""

    def __init__(
        self,
        store: JsonTradingStateStore,
        *,
        report_path: str,
        quote_fetcher: Callable[[str, str], dict[str, Any]],
        broker_factory: Callable[[], TradingBroker] = FubonTradingSession.login,
        policy: TradingPolicy | None = None,
        clock: Callable[[], datetime] | None = None,
        stale_order_seconds: int = 180,
    ) -> None:
        if store.mode != "live":
            raise ValueError("真單引擎必須使用獨立 live 狀態檔")
        super().__init__(
            store,
            report_path=report_path,
            quote_fetcher=quote_fetcher,
            policy=policy,
            clock=clock,
        )
        self.broker_factory = broker_factory
        self.stale_order_seconds = max(60, int(stale_order_seconds))
        self._broker: TradingBroker | None = None
        self._broker_lock = threading.RLock()

    def _get_broker(self) -> TradingBroker:
        with self._broker_lock:
            if self._broker is None:
                self._broker = self.broker_factory()
            return self._broker

    def status(self) -> dict[str, Any]:
        state = self.store.load()
        result = dict(state)
        reason = live_order_unlock_reason()
        result["state_path"] = str(self.store.path)
        result["persistent_volume_ready"] = str(self.store.path).startswith("/data/") and self.store.path.parent.exists()
        result["broker_unlocked"] = reason is None
        result["broker_lock_reason"] = reason
        result["notice"] = "真實台股模式；只有本人開啟總開關且條件合格才會送單"
        return result

    def configure(
        self,
        *,
        selected: list[str],
        cash_limit: int,
        enabled: bool | None = None,
        emergency_stop: bool | None = None,
        live_confirmation: str | None = None,
    ) -> dict[str, Any]:
        normalized = list(dict.fromkeys(normalize_trade_symbol(item) for item in selected))
        if len(normalized) > self.policy.max_positions:
            raise ValueError(f"最多只能勾選{self.policy.max_positions}檔")
        cash_limit = int(cash_limit)
        if cash_limit < 1_000 or cash_limit > self.policy.max_cash_limit:
            raise ValueError("真單資金須介於1,000元與20,000元")
        if enabled:
            reason = live_order_unlock_reason()
            if reason:
                raise PermissionError(f"真實下單仍鎖定：{reason}")
            if live_confirmation != LIVE_ARM_PHRASE:
                raise PermissionError("請先在本人網頁確認開啟真實下單")

        with self.lock:
            state = self.store.load()
            if state.get("positions") and cash_limit != int(state.get("cash_limit") or 0):
                raise ValueError("仍有真實持倉，不能變更資金上限")
            if any(_pending(order) for order in state.get("orders") or []) and cash_limit != int(state.get("cash_limit") or 0):
                raise ValueError("仍有待成交委託，不能變更資金上限")
            state["cash_limit"] = cash_limit
            state["selected"] = normalized
            if enabled is not None:
                state["enabled"] = bool(enabled)
                if enabled:
                    state["emergency_stop"] = False
                    state["armed_at"] = self.clock().isoformat(timespec="seconds")
                    self._event(state, "本人已開啟真實台股下單總開關")
            if emergency_stop is not None:
                state["emergency_stop"] = bool(emergency_stop)
                if emergency_stop:
                    state["enabled"] = False
                    self._event(state, "真實下單緊急停止已開啟")
            return self.store.save(state)

    def preview(self) -> dict[str, Any]:
        state = self.store.load()
        preview = self._preview_for_state(state)
        preview["mode"] = "live"
        preview["notice"] = "這是富邦真單試算；未開啟總開關前不會送單"
        for plan in preview["plans"]:
            if plan.get("market") != "TW":
                plan["eligible"] = False
                plan.setdefault("reasons", []).append("美股尚未接上真單券商")
        return preview

    def _active_order(self, state: dict[str, Any], symbol: str, side: str | None = None) -> bool:
        return any(
            _pending(order)
            and order.get("symbol") == symbol
            and (side is None or order.get("side") == side)
            for order in state.get("orders") or []
        )

    def _order_price(
        self,
        symbol: str,
        row: dict[str, Any] | None,
        *,
        side: str,
        fallback: float,
    ) -> float:
        try:
            payload = self.quote_fetcher(symbol, "TW") if self.quote_fetcher else {}
            quote = payload.get("quote", {}) if isinstance(payload, dict) else {}
            key = "askPrice" if side == "BUY" else "bidPrice"
            value = _number(quote.get(key))
            if value and value > 0:
                return value
        except Exception:
            pass
        return fallback

    def _record_submission(
        self,
        state: dict[str, Any],
        snapshots: list[dict[str, Any]],
        *,
        symbol: str,
        side: str,
        reason: str,
        row: dict[str, Any] | None,
        session_date: str,
    ) -> None:
        if not snapshots:
            raise RuntimeError("券商沒有回傳委託編號")
        for snapshot in snapshots:
            broker_id = str(snapshot.get("broker_id") or "")
            if not broker_id:
                raise RuntimeError("券商委託缺少可核對編號")
            order = {
                "id": f"LIVE-{broker_id}",
                "broker_id": broker_id,
                "order_no": str(snapshot.get("order_no") or ""),
                "time": self.clock().isoformat(timespec="seconds"),
                "session_date": session_date,
                "side": side,
                "symbol": symbol,
                "quantity": int(snapshot.get("quantity") or 0),
                "price": float(snapshot.get("price") or 0),
                "status_code": int(snapshot.get("status_code") or 0),
                "status": "SUBMITTED",
                "terminal": bool(snapshot.get("terminal")),
                "filled_qty": 0,
                "filled_money": 0.0,
                "accounted_filled_qty": 0,
                "accounted_filled_money": 0.0,
                "mode": "live",
                "reason": reason,
                "name": (row or {}).get("name") or symbol,
                "type": (row or {}).get("type") or "個股",
                "stop_price": _number((row or {}).get("short_term_stop")),
            }
            self._append_order(state, order)
            state["real_orders_sent"] = int(state.get("real_orders_sent") or 0) + 1
            self._event(state, f"已送富邦{('買單' if side == 'BUY' else '賣單')} {symbol}，等待成交核對")

    def _apply_fill(self, state: dict[str, Any], order: dict[str, Any], update: dict[str, Any]) -> None:
        filled_qty = max(0, int(update.get("filled_qty") or 0))
        filled_money = max(0.0, float(update.get("filled_money") or 0))
        old_qty = max(0, int(order.get("accounted_filled_qty") or 0))
        old_money = max(0.0, float(order.get("accounted_filled_money") or 0))
        delta_qty = filled_qty - old_qty
        delta_money = filled_money - old_money
        if delta_qty <= 0:
            return
        if delta_money <= 0:
            delta_money = delta_qty * float(update.get("after_price") or update.get("price") or order.get("price") or 0)
        symbol = str(order["symbol"])
        positions = state.setdefault("positions", {})

        if order.get("side") == "BUY":
            fee = delta_money * self.policy.buy_fee_rate
            position = positions.get(symbol)
            if position is None:
                position = {
                    "symbol": symbol,
                    "name": order.get("name") or symbol,
                    "type": order.get("type") or "個股",
                    "market": "TW",
                    "currency": "TWD",
                    "quantity": 0,
                    "entry_price": 0.0,
                    "entry_cost": 0.0,
                    "entry_date": order.get("session_date"),
                    "last_session_date": order.get("session_date"),
                    "held_sessions": 1,
                    "stop_price": float(order.get("stop_price") or 0),
                    "peak_price": 0.0,
                    "status": "OPEN",
                    "managed_by": "WudeAI",
                }
                positions[symbol] = position
            position["quantity"] = int(position.get("quantity") or 0) + delta_qty
            position["entry_cost"] = round(float(position.get("entry_cost") or 0) + delta_money + fee, 2)
            position["entry_price"] = round(position["entry_cost"] / position["quantity"], 4)
            position["peak_price"] = max(float(position.get("peak_price") or 0), delta_money / delta_qty)
            if not position.get("stop_price"):
                position["stop_price"] = round(position["entry_price"] * (1 - self.policy.default_stop_pct), 2)
            self._event(state, f"富邦買單成交 {symbol} {delta_qty}股")
        else:
            position = positions.get(symbol)
            if position is None:
                state["enabled"] = False
                state["emergency_stop"] = True
                self._event(state, f"賣單成交但找不到系統持倉 {symbol}，已緊急停止")
                return
            before_qty = int(position.get("quantity") or 0)
            sold_qty = min(delta_qty, before_qty)
            allocated_cost = float(position.get("entry_cost") or 0) * sold_qty / before_qty if before_qty else 0.0
            tax_rate = self.policy.etf_sell_tax_rate if "ETF" in str(position.get("type") or "").upper() else self.policy.stock_sell_tax_rate
            proceeds = delta_money * (1 - self.policy.sell_fee_rate - tax_rate)
            pnl = proceeds - allocated_cost
            position["quantity"] = before_qty - sold_qty
            position["entry_cost"] = round(max(0.0, float(position.get("entry_cost") or 0) - allocated_cost), 2)
            state["realized_pnl"] = round(float(state.get("realized_pnl") or 0) + pnl, 2)
            state["daily_realized_pnl"] = round(float(state.get("daily_realized_pnl") or 0) + pnl, 2)
            if position["quantity"] <= 0:
                positions.pop(symbol, None)
            self._event(state, f"富邦賣單成交 {symbol} {sold_qty}股，估算損益{pnl:,.0f}元")

        order["accounted_filled_qty"] = filled_qty
        order["accounted_filled_money"] = filled_money

    def _reconcile(self, state: dict[str, Any], broker: TradingBroker) -> set[str]:
        updates = {str(row.get("broker_id") or ""): row for row in broker.order_results() if row.get("broker_id")}
        touched: set[str] = set()
        for order in state.get("orders") or []:
            if order.get("mode") != "live" or not order.get("broker_id"):
                continue
            update = updates.get(str(order["broker_id"]))
            if update is None:
                continue
            before = (
                int(order.get("filled_qty") or 0),
                float(order.get("filled_money") or 0),
                int(order.get("status_code") or 0),
                bool(order.get("terminal")),
            )
            self._apply_fill(state, order, update)
            order["filled_qty"] = int(update.get("filled_qty") or 0)
            order["filled_money"] = float(update.get("filled_money") or 0)
            order["status_code"] = int(update.get("status_code") or 0)
            order["terminal"] = bool(update.get("terminal"))
            order["status"] = "FILLED" if order["status_code"] == 50 else "FAILED" if update.get("failed") else "CLOSED" if order["terminal"] else "PENDING"
            order["last_broker_time"] = update.get("last_time")
            order["error"] = update.get("error") or ""
            after = (
                order["filled_qty"], order["filled_money"],
                order["status_code"], order["terminal"],
            )
            if before != after:
                touched.add(str(order.get("symbol") or ""))
        return touched

    def _cancel_stale(self, state: dict[str, Any], broker: TradingBroker) -> set[str]:
        touched: set[str] = set()
        now = self.clock()
        for order in state.get("orders") or []:
            if not _pending(order) or not order.get("order_no") or order.get("cancel_requested_at"):
                continue
            created = _parse_time(order.get("time"))
            if created is None or (now - created).total_seconds() < self.stale_order_seconds:
                continue
            try:
                broker.cancel_order(str(order["order_no"]))
                order["cancel_requested_at"] = now.isoformat(timespec="seconds")
                touched.add(str(order.get("symbol") or ""))
                self._event(state, f"委託逾時，已要求富邦取消 {order.get('symbol')}")
            except Exception as exc:
                order["cancel_error"] = str(exc)[:200]
                state["enabled"] = False
                self._event(state, "委託逾時但刪單失敗，已停止新增買單")
        return touched

    def _cancel_pending(
        self,
        state: dict[str, Any],
        broker: TradingBroker,
        *,
        unselected_only: bool,
    ) -> set[str]:
        selected = set(state.get("selected") or [])
        touched: set[str] = set()
        for order in state.get("orders") or []:
            if not _pending(order) or not order.get("order_no") or order.get("cancel_requested_at"):
                continue
            if unselected_only and not (order.get("side") == "BUY" and order.get("symbol") not in selected):
                continue
            try:
                broker.cancel_order(str(order["order_no"]))
                order["cancel_requested_at"] = self.clock().isoformat(timespec="seconds")
                touched.add(str(order.get("symbol") or ""))
                label = "已取消未勾選標的買單" if unselected_only else "緊急停止已要求取消委託"
                self._event(state, f"{label} {order.get('symbol')}")
            except Exception as exc:
                order["cancel_error"] = str(exc)[:200]
                state["enabled"] = False
                self._event(state, "取消富邦委託失敗，已停止新增買單")
        return touched

    def _inventory_map(self, broker: TradingBroker) -> dict[str, int]:
        result: dict[str, int] = {}
        for row in broker.inventories():
            symbol = str(row.get("symbol") or "").split(".", 1)[0]
            if symbol:
                result[symbol] = result.get(symbol, 0) + max(0, int(row.get("quantity") or 0))
        return result

    def run_cycle(self, *, session_date: str | None = None, markets: set[str] | None = None) -> dict[str, Any]:
        if markets is not None and "TW" not in markets:
            return self.store.load()
        reason = live_order_unlock_reason()
        if reason:
            raise PermissionError(f"真實下單仍鎖定：{reason}")
        with self.lock:
            state = self.store.load()
            today = session_date or self.clock().date().isoformat()
            self._reset_daily_pnl(state, today)
            broker = self._get_broker()
            touched = self._reconcile(state, broker)
            if state.get("emergency_stop"):
                touched.update(self._cancel_pending(state, broker, unselected_only=False))
            else:
                touched.update(self._cancel_pending(state, broker, unselected_only=True))
            touched.update(self._cancel_stale(state, broker))
            inventory = self._inventory_map(broker)
            candidates = self._load_candidates()

            for symbol, position in list((state.get("positions") or {}).items()):
                stock_id = symbol.split(".", 1)[0]
                system_qty = int(position.get("quantity") or 0)
                if inventory.get(stock_id, 0) < system_qty:
                    state["enabled"] = False
                    state["emergency_stop"] = True
                    self._event(state, f"富邦庫存少於系統持倉 {symbol}，已緊急停止")
                    continue
                row = candidates.get(symbol)
                price, _ = self._price(symbol, row)
                if price is None:
                    continue
                if position.get("last_session_date") != today:
                    position["held_sessions"] = int(position.get("held_sessions") or 0) + 1
                    position["last_session_date"] = today
                position["peak_price"] = max(float(position.get("peak_price") or price), price)
                exit_reason = self._exit_reason(position, row, price)
                if (
                    exit_reason
                    and not state.get("emergency_stop")
                    and not self._active_order(state, symbol)
                    and symbol not in touched
                ):
                    sell_price = self._order_price(symbol, row, side="SELL", fallback=price)
                    snapshots = broker.place_limit_order(symbol=symbol, side="SELL", quantity=system_qty, price=sell_price)
                    self._record_submission(
                        state, snapshots, symbol=symbol, side="SELL", reason=exit_reason,
                        row=row, session_date=today,
                    )
                    touched.add(symbol)

            daily_limit = float(state.get("cash_limit") or 0) * self.policy.daily_loss_limit_ratio
            if float(state.get("daily_realized_pnl") or 0) <= -daily_limit:
                state["enabled"] = False
                self._event(state, "達到每日損失上限，真實下單已停止")

            if state.get("enabled") and not state.get("emergency_stop"):
                actual_cash = min(float(state.get("cash_limit") or 0), float(broker.available_cash()))
                state["broker_available_cash"] = round(actual_cash, 2)
                preview_state = dict(state)
                preview_state["cash_limit"] = max(1_000, math.floor(actual_cash))
                plans = self._preview_for_state(preview_state)["plans"] if actual_cash >= 1_000 else []
                open_symbols = set((state.get("positions") or {}).keys())
                pending_buys = {str(order.get("symbol")) for order in state.get("orders") or [] if _pending(order) and order.get("side") == "BUY"}
                slots = max(0, self.policy.max_positions - len(open_symbols) - len(pending_buys))
                for plan in plans:
                    symbol = str(plan["symbol"])
                    if slots <= 0:
                        break
                    if plan.get("market") != "TW" or not plan.get("eligible"):
                        continue
                    if symbol in open_symbols or symbol in pending_buys or symbol in touched or self._active_order(state, symbol):
                        continue
                    row = candidates.get(symbol)
                    buy_price = self._order_price(
                        symbol, row, side="BUY", fallback=float(plan["price"]),
                    )
                    budget = float(plan.get("budget") or 0)
                    quantity = max(0, math.floor(budget / (buy_price * (1 + self.policy.buy_fee_rate))))
                    if quantity < 1:
                        continue
                    snapshots = broker.place_limit_order(
                        symbol=symbol,
                        side="BUY",
                        quantity=quantity,
                        price=buy_price,
                    )
                    self._record_submission(
                        state, snapshots, symbol=symbol, side="BUY",
                        reason="本人勾選且通過進場、資金及風控條件",
                        row=row, session_date=today,
                    )
                    pending_buys.add(symbol)
                    slots -= 1

            return self.store.save(state)
