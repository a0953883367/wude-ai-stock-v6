r"""Run Wude AI briefings with Fubon Neo as the primary Taiwan source.

On Windows, credentials come from Credential Manager targets
``FUBON_API_WUDE`` and ``FUBON_CERT_WUDE``. The certificate stays at
``%LOCALAPPDATA%\WudeAI\cert\fubon_cert.p12``. Nothing secret is committed.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from typing import Any

import pandas as pd

import briefing
from config import ROOT, TAIPEI
from data_fetcher import download_intraday as yahoo_download_intraday
from fubon_credentials import FubonCredentials, load_fubon_credentials

LOG = logging.getLogger("fubon_runner")


def _to_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    if hasattr(value, "__dict__"):
        return {k: v for k, v in vars(value).items() if not k.startswith("_")}
    raise TypeError(f"無法解析富邦回傳型別: {type(value)!r}")


def _login_fubon(credentials: FubonCredentials | None = None):
    try:
        from fubon_neo.sdk import FubonSDK
    except ImportError as exc:
        raise RuntimeError(
            "找不到 fubon_neo SDK。請先依富邦官方 SDK 下載頁安裝 Python SDK。"
        ) from exc

    creds = credentials or load_fubon_credentials()
    sdk = FubonSDK()
    result = sdk.apikey_login(
        creds.personal_id,
        creds.api_key,
        str(creds.cert_path),
        creds.cert_password,
    )
    if getattr(result, "is_success", None) is False:
        message = str(getattr(result, "message", "登入資料不正確"))
        raise RuntimeError(f"富邦登入失敗：{message}")

    sdk.init_realtime()
    LOG.info("Fubon API login OK; realtime initialized")
    return sdk


def _check_market_data(sdk) -> None:
    quote = sdk.marketdata.rest_client.stock.intraday.quote(symbol="2330")
    if not quote or getattr(quote, "is_success", True) is False:
        raise RuntimeError("富邦行情測試失敗")
    print("FUBON_AUTOMATION_OK")


def _candle_frame(payload: Any) -> pd.DataFrame:
    obj = _to_dict(payload)
    rows = obj.get("data") or []
    if not rows:
        return pd.DataFrame()

    normalized = []
    for row in rows:
        item = _to_dict(row)
        normalized.append({
            "time": item.get("time"),
            "open": item.get("open"),
            "high": item.get("high"),
            "low": item.get("low"),
            "close": item.get("close"),
            "volume": item.get("volume"),
        })
    frame = pd.DataFrame(normalized)
    if frame.empty:
        return frame

    if "time" in frame and frame["time"].notna().any():
        raw = pd.to_numeric(frame["time"], errors="coerce")
        median = raw.dropna().median() if raw.notna().any() else None
        unit = "ms"
        if median is not None:
            if median > 1e17:
                unit = "ns"
            elif median > 1e14:
                unit = "us"
            elif median > 1e11:
                unit = "ms"
            else:
                unit = "s"
        idx = pd.to_datetime(raw, unit=unit, utc=True, errors="coerce").dt.tz_convert(TAIPEI)
        frame.index = pd.DatetimeIndex(idx)
    else:
        frame.index = pd.RangeIndex(len(frame))

    for col in ("open", "high", "low", "close", "volume"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame.drop(columns=["time"], errors="ignore").dropna(how="all")


def _quote_data(payload: Any) -> dict[str, Any]:
    """Normalize both direct HTTP-style and SDK-wrapped quote responses."""
    obj = _to_dict(payload)
    nested = obj.get("data")
    if nested is not None and not any(key in obj for key in ("lastPrice", "bids", "asks")):
        obj = _to_dict(nested)
    return obj


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if pd.notna(result) else None


def _level_total(levels: Any) -> int | None:
    if not isinstance(levels, (list, tuple)) or not levels:
        return None
    total = 0.0
    found = False
    for level in levels:
        item = _to_dict(level)
        size = _number(item.get("size"))
        if size is not None:
            total += size
            found = True
    return int(total) if found else None


def _first_level_price(levels: Any) -> float | None:
    if not isinstance(levels, (list, tuple)) or not levels:
        return None
    try:
        return _number(_to_dict(levels[0]).get("price"))
    except (TypeError, ValueError):
        return None


def parse_fubon_quote(payload: Any) -> dict[str, Any]:
    obj = _quote_data(payload)
    last_price = _number(obj.get("lastPrice", obj.get("closePrice")))
    bid_total = _level_total(obj.get("bids"))
    ask_total = _level_total(obj.get("asks"))
    if last_price is None and bid_total is None and ask_total is None:
        return {}
    return {
        "lastPrice": last_price,
        "bidPrice": _first_level_price(obj.get("bids")),
        "askPrice": _first_level_price(obj.get("asks")),
        "bidTotal": bid_total,
        "askTotal": ask_total,
        "fetchedAt": datetime.now(TAIPEI).isoformat(timespec="seconds"),
    }


def _write_fubon_snapshot(quotes: dict[str, dict[str, Any]]) -> None:
    if not quotes:
        LOG.warning("No Fubon quotes succeeded; keeping the previous snapshot")
        return
    path = ROOT / "reports" / "fubon_live.json"
    existing: dict[str, Any] = {}
    try:
        existing_payload = json.loads(path.read_text(encoding="utf-8"))
        existing = existing_payload.get("data", {})
    except (OSError, ValueError, TypeError):
        pass
    existing.update(quotes)
    now = datetime.now(TAIPEI).isoformat(timespec="seconds")
    payload = {
        "version": 1,
        "updated_at": now,
        "source": "Fubon Neo intraday.quote",
        "data": existing,
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)
    LOG.info("Saved Fubon quote snapshot for %d symbols", len(quotes))


def build_fubon_intraday(
    sdk,
    symbols: list[str],
    quote_results: dict[str, dict[str, Any]] | None = None,
) -> dict[str, pd.DataFrame]:
    """Use Fubon for every Taiwan symbol and Yahoo as a resilient fallback."""
    result: dict[str, pd.DataFrame] = {}
    tw_symbols = [s for s in symbols if s.endswith(".TW") or s.endswith(".TWO")]
    non_tw = [s for s in symbols if s not in tw_symbols]

    if non_tw:
        result.update(yahoo_download_intraday(non_tw))

    reststock = sdk.marketdata.rest_client.stock
    for symbol in tw_symbols:
        stock_id = symbol.split(".")[0]
        if quote_results is not None:
            try:
                quote = parse_fubon_quote(reststock.intraday.quote(symbol=stock_id))
                if quote and quote_results is not None:
                    quote_results[symbol] = quote
            except Exception as exc:
                LOG.warning("Fubon quote failed for %s: %s", symbol, exc)
        try:
            payload = reststock.intraday.candles(symbol=stock_id, timeframe="5", sort="asc")
            frame = _candle_frame(payload)
            if not frame.empty:
                result[symbol] = frame
                continue
            LOG.warning("Fubon returned no intraday candles for %s", symbol)
        except Exception as exc:
            LOG.warning("Fubon candles failed for %s: %s", symbol, exc)

        fallback = yahoo_download_intraday([symbol])
        if symbol in fallback:
            result[symbol] = fallback[symbol]

    LOG.info("Intraday source complete: %d/%d symbols", len(result), len(symbols))
    return result


def _git_publish(period: str) -> None:
    if os.getenv("FUBON_AUTO_GIT", "0").strip().lower() not in {"1", "true", "yes"}:
        return
    try:
        subprocess.run(["git", "add", "reports"], cwd=ROOT, check=True)
        diff = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT)
        if diff.returncode == 0:
            LOG.info("No report changes to publish")
            return
        stamp = datetime.now(TAIPEI).strftime("%Y-%m-%d %H:%M")
        subprocess.run(
            ["git", "commit", "-m", f"Fubon auto refresh {period} {stamp}"],
            cwd=ROOT,
            check=True,
        )
        subprocess.run(["git", "push"], cwd=ROOT, check=True)
        LOG.info("Published refreshed reports to GitHub")
    except Exception as exc:
        LOG.error("Report generated but git publish failed: %s", exc)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", choices=["morning", "noon", "evening"])
    parser.add_argument("--check", action="store_true", help="verify login and read-only market data")
    parser.add_argument("--no-telegram", action="store_true")
    parser.add_argument("--auto-git", action="store_true", help="commit/push generated reports")
    args = parser.parse_args()
    if not args.check and not args.period:
        parser.error("--period is required unless --check is used")
    return args


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sdk = _login_fubon()
    if args.check:
        _check_market_data(sdk)
        return 0

    quote_results: dict[str, dict[str, Any]] = {}
    briefing.download_intraday = lambda symbols: build_fubon_intraday(
        sdk,
        symbols,
        quote_results=quote_results,
    )
    argv = [sys.argv[0], "--period", args.period]
    if args.no_telegram:
        argv.append("--no-telegram")
    old_argv = sys.argv
    try:
        sys.argv = argv
        rc = briefing.main()
    finally:
        sys.argv = old_argv

    if rc == 0:
        _write_fubon_snapshot(quote_results)
        if args.auto_git:
            os.environ["FUBON_AUTO_GIT"] = "1"
        _git_publish(args.period)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
