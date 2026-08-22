"""Locked Fubon trading adapter.

The adapter is present so the proven paper state machine can later use the same
portfolio and order primitives.  It is fail-closed: real orders require two
server-side environment switches *and* a confirmation phrase stored only on
the server.  Importing this module or constructing the class cannot place an
order.
"""

from __future__ import annotations

from dataclasses import dataclass
import hmac
import os
from typing import Any

from fubon_credentials import load_fubon_credentials


HARD_CAP_TWD = 20_000


def _rows(value: Any) -> list[Any]:
    data = value.get("data", value) if isinstance(value, dict) else getattr(value, "data", value)
    if isinstance(data, (list, tuple)):
        return list(data)
    return [data] if data is not None else []


def _value(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def split_market_quantities(quantity: int) -> list[tuple[str, int]]:
    """Split shares into exchange-valid whole-lot and odd-lot orders."""
    quantity = int(quantity)
    if quantity <= 0:
        return []
    whole = quantity // 1000 * 1000
    odd = quantity % 1000
    result: list[tuple[str, int]] = []
    if whole:
        result.append(("Common", whole))
    if odd:
        result.append(("IntradayOdd", odd))
    return result


def live_order_unlock_reason(environ: dict[str, str] | None = None) -> str | None:
    env = os.environ if environ is None else environ
    if str(env.get("TRADING_MODE", "")).strip().lower() != "live":
        return "TRADING_MODE 尚未設為 live"
    if str(env.get("LIVE_TRADING_ENABLED", "")).strip().lower() not in {"1", "true", "yes", "on"}:
        return "LIVE_TRADING_ENABLED 尚未開啟"
    state_path = str(env.get("TRADE_STATE_PATH", "")).strip()
    if not state_path.startswith("/data/"):
        return "尚未掛載永久交易狀態磁碟"
    expected = str(env.get("LIVE_TRADING_CONFIRMATION", "")).strip()
    supplied = str(env.get("LIVE_TRADING_CONFIRMATION_INPUT", "")).strip()
    if not expected or not supplied or not hmac.compare_digest(expected, supplied):
        return "伺服器真單確認碼未完成"
    return None


@dataclass
class FubonTradingSession:
    sdk: Any
    account: Any

    @classmethod
    def login(cls) -> "FubonTradingSession":
        try:
            from fubon_neo.sdk import FubonSDK
        except ImportError as exc:
            raise RuntimeError("找不到富邦 Neo SDK") from exc
        creds = load_fubon_credentials()
        sdk = FubonSDK()
        response = sdk.apikey_login(
            creds.personal_id,
            creds.api_key,
            str(creds.cert_path),
            creds.cert_password,
        )
        if _value(response, "is_success", True) is False:
            raise RuntimeError(f"富邦登入失敗：{_value(response, 'message', 'unknown error')}")
        accounts = _rows(response)
        account = next((item for item in accounts if _value(item, "account")), None)
        if account is None:
            raise RuntimeError("富邦登入成功，但沒有取得可交易帳號")
        return cls(sdk=sdk, account=account)

    def available_cash(self) -> float:
        response = self.sdk.accounting.bank_remain(self.account)
        if _value(response, "is_success", True) is False:
            raise RuntimeError(f"無法讀取交割帳戶餘額：{_value(response, 'message', 'unknown error')}")
        rows = _rows(response)
        if not rows:
            raise RuntimeError("富邦沒有回傳交割帳戶餘額")
        balance = float(_value(rows[0], "available_balance", 0) or 0)
        return max(0.0, min(balance, float(HARD_CAP_TWD)))

    def inventories(self) -> list[dict[str, Any]]:
        response = self.sdk.accounting.inventories(self.account)
        if _value(response, "is_success", True) is False:
            raise RuntimeError(f"無法讀取庫存：{_value(response, 'message', 'unknown error')}")
        result: list[dict[str, Any]] = []
        for row in _rows(response):
            result.append({
                "symbol": str(_value(row, "stock_no", _value(row, "symbol", ""))),
                "quantity": int(_value(row, "today_qty", _value(row, "qty", 0)) or 0),
                "raw": row,
            })
        return result

    def place_limit_order(self, *, symbol: str, side: str, quantity: int, price: float) -> list[Any]:
        reason = live_order_unlock_reason()
        if reason:
            raise PermissionError(f"真實下單已鎖定：{reason}")
        if quantity <= 0 or price <= 0:
            raise ValueError("委託股數與價格必須大於0")
        if side.upper() not in {"BUY", "SELL"}:
            raise ValueError("side 必須是 BUY 或 SELL")
        stock_id = str(symbol).split(".", 1)[0]
        if not stock_id.isdigit():
            raise ValueError("富邦 Neo 真單適配器目前只允許台股代號")
        if side.upper() == "BUY" and price * quantity > HARD_CAP_TWD:
            raise ValueError("單筆買進金額超過20,000元硬上限")

        from fubon_neo.constant import (  # type: ignore[import-not-found]
            BSAction, MarketType, OrderType, PriceType, TimeInForce,
        )
        from fubon_neo.sdk import Order  # type: ignore[import-not-found]

        action = BSAction.Buy if side.upper() == "BUY" else BSAction.Sell
        results: list[Any] = []
        for market_name, part_quantity in split_market_quantities(quantity):
            market_type = MarketType.Common if market_name == "Common" else MarketType.IntradayOdd
            order = Order(
                buy_sell=action,
                symbol=stock_id,
                price=str(price),
                quantity=part_quantity,
                market_type=market_type,
                price_type=PriceType.Limit,
                time_in_force=TimeInForce.ROD,
                order_type=OrderType.Stock,
                user_def="WudeAI",
            )
            response = self.sdk.stock.place_order(self.account, order)
            if _value(response, "is_success", True) is False:
                raise RuntimeError(f"富邦拒絕委託：{_value(response, 'message', 'unknown error')}")
            results.append(response)
        return results

    def order_results(self) -> list[Any]:
        response = self.sdk.stock.get_order_results(self.account)
        if _value(response, "is_success", True) is False:
            raise RuntimeError(f"無法核對委託成交：{_value(response, 'message', 'unknown error')}")
        return _rows(response)
