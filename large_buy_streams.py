"""Persistent Fubon and Alpaca WebSocket adapters for large-buy alerts."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime
from typing import Any, Callable

from large_buy_monitor import LargeBuyAlertService


LOG = logging.getLogger(__name__)


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
                            size=data.get("size"),
                            bid=data.get("bid"),
                            ask=data.get("ask"),
                            timestamp=_epoch(data.get("time")),
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
                while not self.stop.wait(1.0) and not disconnected.is_set():
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
                            self.service.set_stream_status("US", "connected", subscribed=subscribed)
                            delay = 3.0
                        elif kind == "q":
                            self.service.update_quote(
                                str(row.get("S") or "").upper(), bid=row.get("bp"), ask=row.get("ap")
                            )
                        elif kind == "t":
                            self.service.process_trade(
                                str(row.get("S") or "").upper(),
                                price=row.get("p"),
                                size=row.get("s"),
                                timestamp=_epoch(row.get("t")),
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
            try:
                self._socket.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as exc:
                self.service.set_stream_status("US", "reconnecting", error=str(exc))
            if authenticated:
                delay = 3.0
            if self.stop.wait(delay):
                break
            delay = min(delay * 2, 60.0)

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

