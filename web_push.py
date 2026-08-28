"""Private Web Push subscriptions and large-buy notification delivery."""

from __future__ import annotations

import base64
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
import json
import logging
from pathlib import Path
import threading
from typing import Any
from urllib.parse import quote

from py_vapid import Vapid
from pywebpush import WebPushException, webpush


LOG = logging.getLogger(__name__)


class WebPushService:
    def __init__(
        self,
        state_path: Path,
        private_key_path: Path,
        *,
        subject: str = "mailto:a0953883367@gmail.com",
        max_subscriptions: int = 10,
    ) -> None:
        self.state_path = state_path
        self.private_key_path = private_key_path
        self.subject = subject
        self.max_subscriptions = max(1, max_subscriptions)
        self._lock = threading.RLock()
        self._subscriptions: dict[str, dict[str, Any]] = {}
        self._load()
        self._vapid = self._load_or_create_vapid()

    def _load_or_create_vapid(self) -> Vapid:
        self.private_key_path.parent.mkdir(parents=True, exist_ok=True)
        if self.private_key_path.exists():
            return Vapid.from_file(str(self.private_key_path))
        vapid = Vapid()
        vapid.generate_keys()
        vapid.save_key(str(self.private_key_path))
        try:
            self.private_key_path.chmod(0o600)
        except OSError:
            pass
        return vapid

    @property
    def public_key(self) -> str:
        raw = self._vapid.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    @property
    def subscription_count(self) -> int:
        with self._lock:
            return len(self._subscriptions)

    def _load(self) -> None:
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return
        rows = payload.get("subscriptions") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return
        for row in rows[-self.max_subscriptions:]:
            if isinstance(row, dict) and isinstance(row.get("endpoint"), str):
                self._subscriptions[row["endpoint"]] = row

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "version": 1,
            "subscriptions": list(self._subscriptions.values()),
        }, separators=(",", ":")), encoding="utf-8")
        tmp.replace(self.state_path)

    @staticmethod
    def _validated(subscription: Any) -> dict[str, Any]:
        if not isinstance(subscription, dict):
            raise ValueError("subscription must be an object")
        endpoint = str(subscription.get("endpoint") or "")
        keys = subscription.get("keys")
        if not endpoint.startswith("https://") or not isinstance(keys, dict):
            raise ValueError("invalid push subscription")
        p256dh = str(keys.get("p256dh") or "")
        auth = str(keys.get("auth") or "")
        if len(endpoint) > 4096 or not p256dh or not auth or len(p256dh) > 512 or len(auth) > 256:
            raise ValueError("invalid push subscription")
        return {"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}}

    def subscribe(self, subscription: Any) -> int:
        row = self._validated(subscription)
        with self._lock:
            self._subscriptions[row["endpoint"]] = row
            while len(self._subscriptions) > self.max_subscriptions:
                self._subscriptions.pop(next(iter(self._subscriptions)))
            self._save()
            return len(self._subscriptions)

    @staticmethod
    def payload(alert: dict[str, Any]) -> dict[str, Any]:
        market = str(alert.get("market") or "US")
        symbol = str(alert.get("symbol") or "")
        currency = "NT$" if market == "TW" else "US$"
        side = "sell" if alert.get("alert_side") == "sell" else "buy"
        side_text = "賣出" if side == "sell" else "買進"
        value_key = "sell_value" if side == "sell" else "buy_value"
        ratio_key = "aggressive_sell_ratio_pct" if side == "sell" else "aggressive_buy_ratio_pct"
        value = float(alert.get(value_key) or 0)
        ratio = float(alert.get(ratio_key) or 0)
        price = float(alert.get("price") or 0)
        return {
            "title": f"{'🔻' if side == 'sell' else '🚨'} {alert.get('name') or symbol}・{symbol}",
            "body": (
                f"{alert.get('trigger_label')}｜{currency}{value:,.0f}｜"
                f"成交 {price:,.2f}｜{side_text}占比 {ratio:.1f}%"
            ),
            "tag": f"large-{side}-{symbol}",
            "url": f"/live?symbol={quote(symbol)}&market={market}",
        }

    def send_alert(self, alert: dict[str, Any]) -> None:
        if not self.subscription_count:
            return
        threading.Thread(
            target=self._deliver,
            args=(self.payload(alert),),
            name="large-buy-web-push",
            daemon=True,
        ).start()

    def _deliver(self, payload: dict[str, Any]) -> None:
        with self._lock:
            subscriptions = list(self._subscriptions.values())
        expired: list[str] = []
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        for subscription in subscriptions:
            try:
                webpush(
                    subscription_info=subscription,
                    data=data,
                    vapid_private_key=self._vapid,
                    vapid_claims={"sub": self.subject},
                    timeout=8,
                )
            except WebPushException as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status in {404, 410}:
                    expired.append(subscription["endpoint"])
                else:
                    LOG.warning("Web Push delivery failed: %s", exc)
            except Exception:
                LOG.exception("Web Push delivery failed")
        if expired:
            with self._lock:
                for endpoint in expired:
                    self._subscriptions.pop(endpoint, None)
                self._save()
