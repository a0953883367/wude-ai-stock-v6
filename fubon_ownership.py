"""Read-only Fubon v2.4 ownership supplement; never consumed by scoring."""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

from config import TAIPEI

KINDS = ("institutional_trades", "tdcc_distribution", "director_holdings")


def _object(value):
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "__dict__"):
        return vars(value)
    raise ValueError("invalid response")


def _normalize(payload, symbol, now):
    obj = _object(payload)
    if obj.get("symbol") != symbol or not isinstance(obj.get("data"), list):
        raise ValueError("symbol/schema mismatch")
    rows = [_object(row) for row in obj["data"]]
    for row in rows:
        date = row.get("date")
        if not isinstance(date, str) or len(date) not in (7, 10):
            raise ValueError("missing data date")
        datetime.strptime(date, "%Y-%m" if len(date) == 7 else "%Y-%m-%d")
        if date > now.date().isoformat()[:len(date)]:
            raise ValueError("future data date")
    rows.sort(key=lambda row: row["date"], reverse=True)
    # Keep public market data only; no SDK/session/account metadata.
    return rows


def collect_ownership(reststock, watchlist, path: Path, *, now=None, sleep=time.sleep):
    """Reuse an authenticated market-data client. No login or order calls.

    Cache by symbol/kind, throttle requests, stop the batch on HTTP 429.
    An unsuccessful refresh retains historical rows but marks them unavailable.
    fetched_at records when data became visible here, not its publication date.
    """
    now = now or datetime.now(TAIPEI)
    stamp = now.isoformat(timespec="seconds")
    try:
        previous = json.loads(path.read_text(encoding="utf-8")).get("data", {})
    except (OSError, ValueError, TypeError):
        previous = {}
    data = {}
    limited = False
    ownership = getattr(reststock, "ownership", None)
    for stock in watchlist:
        symbol = str(stock.get("symbol", "")).upper()
        if not symbol.endswith((".TW", ".TWO")) or symbol in data:
            continue
        code = symbol.split(".")[0]
        if not code.isalnum():
            continue
        data[symbol] = {}
        for kind in KINDS:
            old = previous.get(symbol, {}).get(kind, {})
            entry = dict(old) if isinstance(old, dict) else {}
            if kind == "director_holdings" and str(stock.get("type", "")).upper() == "ETF":
                data[symbol][kind] = {"status": "not_applicable", "rows": [], "attempted_at": stamp}
                continue
            ttl = 6 if kind == "institutional_trades" else 24
            try:
                age = now - datetime.fromisoformat(entry["fetched_at"])
                if timedelta(0) <= age < timedelta(hours=ttl) and entry.get("status") in ("available", "no_data"):
                    data[symbol][kind] = entry
                    continue
            except (KeyError, ValueError, TypeError):
                pass
            entry["attempted_at"] = stamp
            method = getattr(ownership, kind, None)
            if limited or not callable(method):
                entry["status"] = "rate_limited" if limited else "sdk_upgrade_required"
            else:
                params = {"symbol": code, "sort": "desc"}
                if kind != "director_holdings":
                    params.update({"from": (now.date() - timedelta(days=180)).isoformat(), "to": now.date().isoformat()})
                try:
                    rows = _normalize(method(**params), code, now)
                    entry = {"status": "available" if rows else "no_data", "rows": rows,
                             "data_date": rows[0]["date"] if rows else None,
                             "fetched_at": stamp, "attempted_at": stamp}
                except Exception as exc:
                    status = getattr(exc, "status_code", None)
                    limited = status == 429
                    entry["status"] = "rate_limited" if limited else "fetch_error"
                    # Do not publish exception messages, which can contain secrets.
                    entry["error_code"] = status if isinstance(status, int) else type(exc).__name__
                sleep(1)
            data[symbol][kind] = entry
    count = sum(entry.get("status") == "available" for kinds in data.values() for entry in kinds.values())
    report = {"version": 1, "source": "Fubon Neo ownership v2.4.0", "updated_at": stamp,
              "mode": "display_only", "available_count": count, "data": data}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    tmp.replace(path)
    return report
