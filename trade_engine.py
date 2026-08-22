"""Conservative Taiwan paper-trading engine.

This module deliberately cannot submit real broker orders.  It implements the
state machine that must be proven first: owner-selected symbols, dynamic cash
allocation, duplicate-order prevention, simulated fills, stop loss, trailing
profit protection, next-session exits and a five-session holding cap.

The state path is configurable so Railway can mount a persistent volume before
the same state machine is connected to a live broker adapter.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
import json
import math
import os
from pathlib import Path
import threading
from typing import Any, Callable
from zoneinfo import ZoneInfo


TAIPEI = ZoneInfo("Asia/Taipei")


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def normalize_trade_symbol(value: str) -> str:
    symbol = str(value or "").strip().upper()
    if symbol.endswith((".TW", ".TWO")):
        stock_id = symbol.split(".", 1)[0]
        suffix = symbol[len(stock_id):]
    elif symbol.isdigit():
        stock_id = symbol
        suffix = ".TW"
    else:
        if not symbol or len(symbol) > 10 or not all(ch.isalnum() or ch in ".-" for ch in symbol):
            raise ValueError("股票代號格式錯誤")
        return symbol
    if not stock_id.isdigit() or not 4 <= len(stock_id) <= 6:
        raise ValueError("台股代號格式錯誤")
    return stock_id + suffix


def normalize_tw_symbol(value: str) -> str:
    """Backward-compatible strict Taiwan normalizer."""
    symbol = normalize_trade_symbol(value)
    if not symbol.endswith((".TW", ".TWO")):
        raise ValueError("只支援台股或台灣ETF代號")
    return symbol


def _iso_now() -> str:
    return datetime.now(TAIPEI).isoformat(timespec="seconds")


@dataclass(frozen=True)
class TradingPolicy:
    max_positions: int = 3
    reserve_ratio: float = 0.10
    minimum_reserve: int = 500
    max_holding_sessions: int = 5
    buy_fee_rate: float = 0.001425
    sell_fee_rate: float = 0.001425
    stock_sell_tax_rate: float = 0.003
    etf_sell_tax_rate: float = 0.001
    us_buy_fee_rate: float = 0.002
    us_sell_fee_rate: float = 0.002
    us_fx_buffer_rate: float = 0.003
    trailing_activation_pct: float = 0.02
    trailing_drawdown_pct: float = 0.008
    default_stop_pct: float = 0.03
    daily_loss_limit_ratio: float = 0.02
    # This project is intentionally capped at the owner's stated test capital.
    # Raising it requires a code change and another review; an API request alone
    # can never make the engine spend more.
    max_cash_limit: int = 20_000


def default_state(cash_limit: int = 20_000, *, mode: str = "paper") -> dict[str, Any]:
    if mode not in {"paper", "live"}:
        raise ValueError("交易模式只允許 paper 或 live")
    return {
        "version": 2,
        "mode": mode,
        "enabled": False,
        "emergency_stop": False,
        "cash_limit": int(cash_limit),
        "paper_cash": float(cash_limit),
        "selected": [],
        "positions": {},
        "orders": [],
        "events": [],
        "realized_pnl": 0.0,
        "daily_realized_pnl": 0.0,
        "daily_pnl_date": None,
        "real_orders_sent": 0,
        "updated_at": _iso_now(),
    }


class JsonTradingStateStore:
    """Atomic JSON state store suitable for a single Railway replica."""

    def __init__(
        self,
        path: str | Path,
        *,
        initial_cash: int = 20_000,
        mode: str = "paper",
    ) -> None:
        if mode not in {"paper", "live"}:
            raise ValueError("交易模式只允許 paper 或 live")
        self.path = Path(path)
        self.initial_cash = initial_cash
        self.mode = mode
        self.lock = threading.RLock()

    def load(self) -> dict[str, Any]:
        with self.lock:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("mode") == self.mode:
                    return data
            except (OSError, ValueError, TypeError):
                pass
            return default_state(self.initial_cash, mode=self.mode)

    def save(self, state: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            state = dict(state)
            state["updated_at"] = _iso_now()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(self.path.suffix + ".tmp")
            temp.write_text(
                json.dumps(state, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            temp.replace(self.path)
            return state


def _candidate_map(payload: Any) -> dict[str, dict[str, Any]]:
    rows = payload.get("data", []) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or str(row.get("market", "")).upper() not in {"TW", "US"}:
            continue
        try:
            symbol = normalize_trade_symbol(str(row.get("symbol", "")))
        except ValueError:
            continue
        result[symbol] = row
    return result


def candidate_eligibility(row: dict[str, Any] | None, price: float | None) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if not row:
        return False, ["找不到最新分析資料"]
    if row.get("market_contract_valid") is False:
        reasons.append("市場資料契約未通過")
    if _truthy(row.get("trade_guard_blocked")):
        reasons.append(str(row.get("trade_guard_reason") or "安全條件阻擋"))
    if not _truthy(row.get("short_term_eligible")):
        reasons.append("短線條件尚未合格")
    if not _truthy(row.get("overall_eligible")):
        reasons.append("綜合風控條件尚未合格")

    direction = str(row.get("next_session_direction") or "")
    confidence = _number(row.get("next_session_confidence"), 0.0) or 0.0
    if "看漲" not in direction and direction.upper() != "UP":
        reasons.append("隔日模型尚未確認看漲")
    if confidence < 65:
        reasons.append("隔日模型信心未達65%")

    low = _number(row.get("short_term_entry_low"))
    high = _number(row.get("short_term_entry_high"))
    if price is None or price <= 0:
        reasons.append("即時價格無效")
    elif low is None or high is None:
        reasons.append("缺少短線進場區")
    elif not low * 0.99 <= price <= high * 1.01:
        reasons.append("價格不在短線進場區附近")
    return not reasons, reasons


class PaperTradingEngine:
    def __init__(
        self,
        store: JsonTradingStateStore,
        *,
        report_path: str | Path,
        quote_fetcher: Callable[[str, str], dict[str, Any]] | None = None,
        fx_path: str | Path | None = None,
        policy: TradingPolicy | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.report_path = Path(report_path)
        self.quote_fetcher = quote_fetcher
        self.fx_path = Path(fx_path) if fx_path else self.report_path.parent / "latest.json"
        self.policy = policy or TradingPolicy()
        self.clock = clock or (lambda: datetime.now(TAIPEI))
        self.lock = threading.RLock()

    def _load_candidates(self) -> dict[str, dict[str, Any]]:
        try:
            return _candidate_map(json.loads(self.report_path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            return {}

    def _price(self, symbol: str, row: dict[str, Any] | None) -> tuple[float | None, str]:
        if self.quote_fetcher is not None:
            try:
                market = "TW" if symbol.endswith((".TW", ".TWO")) else "US"
                payload = self.quote_fetcher(symbol, market)
                quote = payload.get("quote", {}) if isinstance(payload, dict) else {}
                value = _number(quote.get("lastPrice", quote.get("us_live_price")))
                if value and value > 0:
                    return value, str(payload.get("source") or "Fubon Neo")
            except Exception:
                pass
        value = _number((row or {}).get("price"))
        return (value, "latest completed report") if value and value > 0 else (None, "unavailable")

    def _usd_twd(self) -> tuple[float, str]:
        try:
            payload = json.loads(self.fx_path.read_text(encoding="utf-8"))
            rate = _number((((payload.get("market") or {}).get("美元台幣") or {}).get("price")))
            if rate and 20 <= rate <= 50:
                return rate, "latest market report"
        except (OSError, ValueError, TypeError):
            pass
        return 32.0, "conservative fallback"

    def _event(self, state: dict[str, Any], message: str) -> None:
        events = list(state.get("events") or [])
        events.append({"time": self.clock().isoformat(timespec="seconds"), "message": message})
        state["events"] = events[-100:]

    def status(self) -> dict[str, Any]:
        state = self.store.load()
        result = dict(state)
        result["policy"] = asdict(self.policy)
        result["state_path"] = str(self.store.path)
        result["persistent_volume_ready"] = str(self.store.path).startswith("/data/") and self.store.path.parent.exists()
        result["notice"] = "目前為模擬模式，不會送出富邦真實委託"
        return result

    def configure(
        self,
        *,
        selected: list[str],
        cash_limit: int,
        enabled: bool | None = None,
        emergency_stop: bool | None = None,
    ) -> dict[str, Any]:
        normalized = list(dict.fromkeys(normalize_trade_symbol(item) for item in selected))
        if len(normalized) > self.policy.max_positions:
            raise ValueError(f"最多只能勾選{self.policy.max_positions}檔")
        cash_limit = int(cash_limit)
        if cash_limit < 1_000 or cash_limit > self.policy.max_cash_limit:
            raise ValueError("測試資金須介於1,000元與20,000元")

        with self.lock:
            state = self.store.load()
            positions = state.get("positions") or {}
            if positions and cash_limit != int(state.get("cash_limit") or 0):
                raise ValueError("仍有模擬持倉，不能變更測試資金")
            if not positions:
                state["cash_limit"] = cash_limit
                state["paper_cash"] = float(cash_limit)
                state["daily_realized_pnl"] = 0.0
                state["daily_pnl_date"] = None
            state["selected"] = normalized
            if enabled is not None:
                state["enabled"] = bool(enabled)
            if emergency_stop is not None:
                state["emergency_stop"] = bool(emergency_stop)
                if emergency_stop:
                    state["enabled"] = False
            self._event(state, f"更新模擬設定：{len(normalized)}檔，資金上限{cash_limit:,}元")
            return self.store.save(state)

    def _allocation(self, cash_limit: int, count: int) -> tuple[float, float]:
        reserve = max(float(self.policy.minimum_reserve), cash_limit * self.policy.reserve_ratio)
        investable = max(0.0, cash_limit - reserve)
        return reserve, investable / count if count else 0.0

    def _preview_for_state(self, state: dict[str, Any]) -> dict[str, Any]:
        selected = list(state.get("selected") or [])
        candidates = self._load_candidates()
        usd_twd, fx_source = self._usd_twd()
        reserve, per_symbol = self._allocation(int(state.get("cash_limit") or 0), len(selected))
        plans: list[dict[str, Any]] = []
        for symbol in selected:
            row = candidates.get(symbol)
            market = str((row or {}).get("market") or ("TW" if symbol.endswith((".TW", ".TWO")) else "US")).upper()
            price, source = self._price(symbol, row)
            eligible, reasons = candidate_eligibility(row, price)
            quantity = 0
            estimated_cost = 0.0
            currency_rate = usd_twd if market == "US" else 1.0
            buy_cost_rate = (
                self.policy.us_buy_fee_rate + self.policy.us_fx_buffer_rate
                if market == "US" else self.policy.buy_fee_rate
            )
            if price and per_symbol > 0:
                quantity = max(0, math.floor(per_symbol / (price * currency_rate * (1 + buy_cost_rate))))
                estimated_cost = quantity * price * currency_rate * (1 + buy_cost_rate)
            if quantity < 1:
                eligible = False
                reasons.append("分配金額不足以買進1股")
            plans.append({
                "symbol": symbol,
                "market": market,
                "currency": "USD" if market == "US" else "TWD",
                "fx_rate": round(currency_rate, 4),
                "name": (row or {}).get("name") or symbol,
                "eligible": eligible,
                "reasons": list(dict.fromkeys(reasons)),
                "price": price,
                "price_source": source,
                "budget": round(per_symbol, 2),
                "quantity": quantity,
                "estimated_cost": round(estimated_cost, 2),
                "order_market": ("美股整股（模擬）" if market == "US" else ("盤中零股" if 0 < quantity < 1000 else "整股")),
            })
        return {
            "mode": "paper",
            "cash_limit": state.get("cash_limit"),
            "reserve": round(reserve, 2),
            "per_symbol_budget": round(per_symbol, 2),
            "selected_count": len(selected),
            "plans": plans,
            "usd_twd": round(usd_twd, 4),
            "fx_source": fx_source,
            "notice": "不合格標的的預算保留現金，不會轉加碼其他股票",
        }

    def preview(self) -> dict[str, Any]:
        return self._preview_for_state(self.store.load())

    def _reset_daily_pnl(self, state: dict[str, Any], session_date: str) -> None:
        if state.get("daily_pnl_date") != session_date:
            state["daily_pnl_date"] = session_date
            state["daily_realized_pnl"] = 0.0

    def _append_order(self, state: dict[str, Any], order: dict[str, Any]) -> None:
        orders = list(state.get("orders") or [])
        orders.append(order)
        state["orders"] = orders[-200:]

    def _buy(self, state: dict[str, Any], plan: dict[str, Any], row: dict[str, Any], session_date: str) -> None:
        symbol = plan["symbol"]
        quantity = int(plan["quantity"])
        price = float(plan["price"])
        fx_rate = float(plan.get("fx_rate") or 1)
        market = str(plan.get("market") or row.get("market") or "TW").upper()
        buy_cost_rate = (
            self.policy.us_buy_fee_rate + self.policy.us_fx_buffer_rate
            if market == "US" else self.policy.buy_fee_rate
        )
        fee = price * quantity * fx_rate * buy_cost_rate
        total = price * quantity * fx_rate + fee
        if total > float(state.get("paper_cash") or 0):
            return
        stop = _number(row.get("short_term_stop")) or price * (1 - self.policy.default_stop_pct)
        state["paper_cash"] = round(float(state.get("paper_cash") or 0) - total, 2)
        state.setdefault("positions", {})[symbol] = {
            "symbol": symbol,
            "name": row.get("name") or symbol,
            "type": row.get("type") or "個股",
            "market": market,
            "currency": plan.get("currency") or "TWD",
            "fx_rate": fx_rate,
            "quantity": quantity,
            "entry_price": price,
            "entry_cost": round(total, 2),
            "entry_date": session_date,
            "last_session_date": session_date,
            "held_sessions": 1,
            "stop_price": round(stop, 2),
            "peak_price": price,
            "status": "OPEN",
        }
        order = {
            "id": f"PAPER-{len(state.get('orders') or []) + 1:06d}",
            "time": self.clock().isoformat(timespec="seconds"),
            "side": "BUY",
            "symbol": symbol,
            "quantity": quantity,
            "price": price,
            "status": "FILLED",
            "mode": "paper",
            "reason": "勾選標的通過所有進場與資金條件",
        }
        self._append_order(state, order)
        self._event(state, f"模擬買進 {symbol} {quantity}股，成交價{price:g}")

    def _exit_reason(self, position: dict[str, Any], row: dict[str, Any] | None, price: float) -> str | None:
        if int(position.get("held_sessions") or 0) >= self.policy.max_holding_sessions:
            return "達到最長5個交易日"
        if price <= float(position.get("stop_price") or 0):
            return "跌破停損價"
        peak = max(float(position.get("peak_price") or price), price)
        entry = float(position.get("entry_price") or price)
        if peak >= entry * (1 + self.policy.trailing_activation_pct):
            if price <= peak * (1 - self.policy.trailing_drawdown_pct):
                return "獲利後由高點回落，觸發移動停利"

        direction = str((row or {}).get("next_session_direction") or "")
        outlook = str((row or {}).get("outlook_direction") or "")
        resistance = _number((row or {}).get("resistance1"))
        tomorrow_down = "看跌" in direction or direction.upper() == "DOWN"
        five_day_up = "看漲" in outlook or "偏多" in outlook
        near_resistance = resistance is not None and price >= resistance * 0.99
        if tomorrow_down and not five_day_up and (price >= entry or near_resistance):
            return "隔日轉弱且1～5日趨勢未維持看漲"
        return None

    def _sell(self, state: dict[str, Any], symbol: str, position: dict[str, Any], price: float, reason: str) -> None:
        quantity = int(position["quantity"])
        row_type = str(position.get("type") or "")
        fx_rate = self._usd_twd()[0] if position.get("market") == "US" else 1.0
        gross = price * quantity * fx_rate
        if position.get("market") == "US":
            fees = gross * (self.policy.us_sell_fee_rate + self.policy.us_fx_buffer_rate)
        else:
            tax_rate = self.policy.etf_sell_tax_rate if "ETF" in row_type.upper() else self.policy.stock_sell_tax_rate
            fees = gross * (self.policy.sell_fee_rate + tax_rate)
        proceeds = gross - fees
        pnl = proceeds - float(position.get("entry_cost") or 0)
        state["paper_cash"] = round(float(state.get("paper_cash") or 0) + proceeds, 2)
        state["realized_pnl"] = round(float(state.get("realized_pnl") or 0) + pnl, 2)
        state["daily_realized_pnl"] = round(float(state.get("daily_realized_pnl") or 0) + pnl, 2)
        state.setdefault("positions", {}).pop(symbol, None)
        self._append_order(state, {
            "id": f"PAPER-{len(state.get('orders') or []) + 1:06d}",
            "time": self.clock().isoformat(timespec="seconds"),
            "side": "SELL",
            "symbol": symbol,
            "quantity": quantity,
            "price": price,
            "status": "FILLED",
            "mode": "paper",
            "reason": reason,
            "realized_pnl": round(pnl, 2),
        })
        self._event(state, f"模擬賣出 {symbol} {quantity}股，{reason}，損益{pnl:,.0f}元")

    def run_cycle(self, *, session_date: str | None = None, markets: set[str] | None = None) -> dict[str, Any]:
        with self.lock:
            state = self.store.load()
            today = session_date or self.clock().date().isoformat()
            self._reset_daily_pnl(state, today)
            candidates = self._load_candidates()
            exited_symbols: set[str] = set()

            for symbol, position in list((state.get("positions") or {}).items()):
                position_market = str(position.get("market") or ("TW" if symbol.endswith((".TW", ".TWO")) else "US")).upper()
                if markets is not None and position_market not in markets:
                    continue
                row = candidates.get(symbol)
                price, _ = self._price(symbol, row)
                if price is None:
                    continue
                if position.get("last_session_date") != today:
                    position["held_sessions"] = int(position.get("held_sessions") or 0) + 1
                    position["last_session_date"] = today
                position["peak_price"] = max(float(position.get("peak_price") or price), price)
                reason = self._exit_reason(position, row, price)
                if reason:
                    self._sell(state, symbol, position, price, reason)
                    exited_symbols.add(symbol)

            daily_limit = float(state.get("cash_limit") or 0) * self.policy.daily_loss_limit_ratio
            if float(state.get("daily_realized_pnl") or 0) <= -daily_limit:
                state["enabled"] = False
                self._event(state, "達到每日損失上限，模擬交易已自動停止")

            if state.get("enabled") and not state.get("emergency_stop"):
                open_symbols = set((state.get("positions") or {}).keys())
                plans = self._preview_for_state(state)["plans"]
                slots = max(0, self.policy.max_positions - len(open_symbols))
                for plan in plans:
                    if slots <= 0:
                        break
                    symbol = plan["symbol"]
                    if markets is not None and str(plan.get("market") or "").upper() not in markets:
                        continue
                    if symbol in open_symbols or symbol in exited_symbols or not plan["eligible"]:
                        continue
                    row = candidates.get(symbol)
                    if row:
                        self._buy(state, plan, row, today)
                        open_symbols.add(symbol)
                        slots -= 1

            return self.store.save(state)
