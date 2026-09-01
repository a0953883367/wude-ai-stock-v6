"""Persistent Fubon and Alpaca WebSocket adapters for large-buy alerts."""

from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

from capital_flow_shadow import market_session_phase
from large_buy_monitor import LargeBuyAlertService


LOG = logging.getLogger(__name__)
TAIPEI = ZoneInfo("Asia/Taipei")
TW_BOARD_LOT_SHARES = 1_000


def fubon_board_lot_size_to_shares(value: Any) -> float | None:
    """Convert Fubon regular-lot trade size (lots) into shares."""
    try:
        lots = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(lots) or lots <= 0:
        return None
    return lots * TW_BOARD_LOT_SHARES


def market_live_window(market: str, now: datetime | None = None) -> bool:
    """Allow provider connections only during the owner's Taipei-time windows."""
    current = now or datetime.now(tz=TAIPEI)
    if current.tzinfo is None:
        current = current.replace(tzinfo=TAIPEI)
    return market_session_phase(market, current.timestamp()) in {"premarket", "regular"}


def _epoch(value: Any) -> float:
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 1e17:
            return number / 1e9
        if number > 1e14:
            return number / 1e6
        if number > 1e11:
            return number / 1e3
        return number
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return time.time()


class FubonLargeBuyStream:
    def __init__(
        self,
        service: LargeBuyAlertService,
        login_factory: Callable[[], Any],
        stop: threading.Event,
        *,
        chunk_size: int = 300,
    ) -> None:
        self.service = service
        self.login_factory = login_factory
        self.stop = stop
        self.chunk_size = max(1, min(chunk_size, 300))
        self._threads: list[threading.Thread] = []
        self._aliases = {symbol.split(".", 1)[0]: symbol for symbol in service.symbols("TW")}

    def start(self) -> None:
        symbols = sorted(self._aliases)
        if not symbols:
            self.service.set_stream_status("TW", "no_symbols")
            return
        chunks = [symbols[start:start + self.chunk_size] for start in range(0, len(symbols), self.chunk_size)]
        self.service.set_stream_status("TW", "connecting", subscribed=0)
        for index, chunk in enumerate(chunks):
            thread = threading.Thread(
                target=self._run_chunk,
                args=(index, chunk),
                name=f"large-buy-fubon-{index + 1}",
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)

    def _run_chunk(self, index: int, symbols: list[str]) -> None:
        delay = 3.0
        while not self.stop.is_set():
            if not market_live_window("TW"):
                self.service.set_stream_status("TW", "waiting_market_open", subscribed=0, error=None)
                self.stop.wait(30.0)
                continue
            disconnected = threading.Event()
            stock = None
            try:
                sdk = self.login_factory()
                stock = sdk.marketdata.websocket_client.stock

                def on_message(message: Any) -> None:
                    try:
                        payload = json.loads(message) if isinstance(message, str) else message
                        if not isinstance(payload, dict) or payload.get("event") != "data":
                            return
                        data = payload.get("data") or {}
                        if not isinstance(data, dict):
                            return
                        stock_id = str(data.get("symbol") or "")
                        canonical = self._aliases.get(stock_id)
                        if not canonical:
                            return
                        self.service.process_trade(
                            canonical,
                            price=data.get("price"),
                            size=fubon_board_lot_size_to_shares(data.get("size")),
                            bid=data.get("bid"),
                            ask=data.get("ask"),
                            timestamp=_epoch(data.get("time")),
                            trade_id=data.get("serial") or data.get("id"),
                            conditions=data.get("conditions"),
                            exchange=data.get("exchange"),
                            source_size=data.get("size"),
                            source_size_unit="board_lot",
                        )
                    except Exception:
                        LOG.exception("Fubon large-buy message handling failed")

                def on_disconnect(*_args: Any) -> None:
                    disconnected.set()

                stock.on("message", on_message)
                stock.on("disconnect", on_disconnect)
                stock.connect()
                stock.subscribe({"channel": "trades", "symbols": symbols})
                self.service.set_stream_status(
                    "TW", "connected", subscribed=len(self._aliases), error=None
                )
                delay = 3.0
                while (
                    not self.stop.wait(1.0)
                    and not disconnected.is_set()
                    and market_live_window("TW")
                ):
                    pass
            except Exception as exc:
                LOG.warning("Fubon large-buy stream %d failed: %s", index + 1, exc)
                self.service.set_stream_status("TW", "reconnecting", error=str(exc))
            finally:
                try:
                    if stock is not None and hasattr(stock, "disconnect"):
                        stock.disconnect()
                except Exception:
                    pass
            if not self.stop.wait(delay):
                delay = min(delay * 2, 60.0)


class AlpacaLargeBuyStream:
    def __init__(self, service: LargeBuyAlertService, stop: threading.Event) -> None:
        self.service = service
        self.stop = stop
        self.symbols = sorted(service.symbols("US"))
        self._thread: threading.Thread | None = None
        self._socket: Any = None

    def start(self) -> None:
        if not self.symbols:
            self.service.set_stream_status("US", "no_symbols")
            return
        if not os.getenv("ALPACA_API_KEY_ID") or not os.getenv("ALPACA_API_SECRET_KEY"):
            self.service.set_stream_status("US", "blocked", error="Alpaca credentials are not configured")
            return
        self.service.set_stream_status("US", "connecting")
        self._thread = threading.Thread(target=self._run, name="large-buy-alpaca", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            import websocket
        except ImportError:
            self.service.set_stream_status("US", "blocked", error="websocket-client is not installed")
            return
        feed = os.getenv("ALPACA_STOCK_FEED", "sip").strip().lower() or "sip"
        url = f"wss://stream.data.alpaca.markets/v2/{feed}"
        delay = 3.0
        while not self.stop.is_set():
            phase = self.service.transition_market_phase("US")
            if phase == "closed":
                self.service.set_stream_status("US", "waiting_market_open", subscribed=0, error=None)
                self.stop.wait(30.0)
                continue
            authenticated = False

            def on_open(ws: Any) -> None:
                ws.send(json.dumps({
                    "action": "auth",
                    "key": os.environ["ALPACA_API_KEY_ID"],
                    "secret": os.environ["ALPACA_API_SECRET_KEY"],
                }))

            def on_message(ws: Any, message: str) -> None:
                nonlocal authenticated, delay
                try:
                    rows = json.loads(message)
                    if not isinstance(rows, list):
                        rows = [rows]
                    for row in rows:
                        if not isinstance(row, dict):
                            continue
                        kind = row.get("T")
                        if kind == "success" and row.get("msg") == "authenticated":
                            authenticated = True
                            ws.send(json.dumps({
                                "action": "subscribe",
                                "trades": self.symbols,
                                "quotes": self.symbols,
                            }))
                        elif kind == "subscription":
                            subscribed = len(row.get("trades") or [])
                            current_phase = self.service.transition_market_phase("US")
                            state = "premarket_connected" if current_phase == "premarket" else "connected"
                            self.service.set_stream_status("US", state, subscribed=subscribed)
                            delay = 3.0
                        elif kind == "q":
                            self.service.update_quote(
                                str(row.get("S") or "").upper(),
                                bid=row.get("bp"),
                                ask=row.get("ap"),
                                timestamp=_epoch(row.get("t")),
                            )
                        elif kind == "t":
                            self.service.process_trade(
                                str(row.get("S") or "").upper(),
                                price=row.get("p"),
                                size=row.get("s"),
                                timestamp=_epoch(row.get("t")),
                                trade_id=row.get("i"),
                                conditions=row.get("c"),
                                exchange=row.get("x"),
                                tape=row.get("z"),
                            )
                        elif kind == "c":
                            self.service.correct_trade(
                                str(row.get("S") or "").upper(),
                                original_trade_id=row.get("oi"),
                                corrected_trade_id=row.get("ci"),
                                price=row.get("cp"),
                                size=row.get("cs"),
                                conditions=row.get("cc"),
                                timestamp=_epoch(row.get("t")),
                                exchange=row.get("x"),
                                tape=row.get("z"),
                                market="US",
                            )
                        elif kind == "x":
                            self.service.cancel_trade(
                                str(row.get("S") or "").upper(),
                                row.get("i"),
                                market="US",
                            )
                        elif kind == "error":
                            message_text = str(row.get("msg") or row)
                            self.service.set_stream_status("US", "blocked", error=message_text)
                            LOG.warning("Alpaca large-buy stream error: %s", message_text)
                except Exception:
                    LOG.exception("Alpaca large-buy message handling failed")

            def on_error(_ws: Any, error: Any) -> None:
                self.service.set_stream_status("US", "reconnecting", error=str(error))

            def on_close(_ws: Any, _code: Any, reason: Any) -> None:
                if not self.stop.is_set():
                    self.service.set_stream_status("US", "reconnecting", error=str(reason or "disconnected"))

            self._socket = websocket.WebSocketApp(
                url,
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
            )
            closer = threading.Thread(
                target=self._guard_us_session,
                args=(self._socket,),
                name="large-buy-alpaca-session-guard",
                daemon=True,
            )
            closer.start()
            try:
                self._socket.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as exc:
                self.service.set_stream_status("US", "reconnecting", error=str(exc))
            if authenticated:
                delay = 3.0
            if self.stop.wait(delay):
                break
            delay = min(delay * 2, 60.0)

    def _guard_us_session(self, socket: Any) -> None:
        previous = self.service.transition_market_phase("US")
        while not self.stop.wait(1.0):
            phase = self.service.transition_market_phase("US")
            if socket is not self._socket or phase == "closed":
                try:
                    socket.close()
                except Exception:
                    pass
                return
            if phase != previous:
                state = "premarket_connected" if phase == "premarket" else "connected"
                self.service.set_stream_status("US", state, subscribed=len(self.symbols), error=None)
                previous = phase

    def close(self) -> None:
        try:
            if self._socket is not None:
                self._socket.close()
        except Exception:
            pass


class LargeBuyStreams:
    def __init__(self, service: LargeBuyAlertService, login_factory: Callable[[], Any]) -> None:
        self.stop_event = threading.Event()
        self.fubon = FubonLargeBuyStream(service, login_factory, self.stop_event)
        self.alpaca = AlpacaLargeBuyStream(service, self.stop_event)

    def start(self) -> None:
        self.fubon.start()
        self.alpaca.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.alpaca.close()
