"""Run Wude AI briefings with Fubon Neo as the primary Taiwan intraday source.

This file is intentionally designed for the user's own Windows PC because the
Fubon certificate stays on that PC. Credentials are read from environment
variables and are never written to the repository.

Required local environment variables:
  FUBON_ID
  FUBON_PASSWORD                 (normal login) OR FUBON_API_KEY (API-key login)
  FUBON_CERT_PATH
  FUBON_CERT_PASSWORD            (may be empty if the certificate uses default)

Example:
  python fubon_runner.py --period noon
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

import briefing
from config import ROOT, TAIPEI
from data_fetcher import download_intraday as yahoo_download_intraday

LOG = logging.getLogger("fubon_runner")


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"缺少環境變數 {name}")
    return value


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


def _login_fubon():
    try:
        from fubon_neo.sdk import FubonSDK
    except ImportError as exc:
        raise RuntimeError(
            "找不到 fubon_neo SDK。請先依富邦官方 SDK 下載頁安裝 Python SDK。"
        ) from exc

    personal_id = _required("FUBON_ID")
    cert_path = _required("FUBON_CERT_PATH")
    cert_password = os.getenv("FUBON_CERT_PASSWORD", "")
    api_key = os.getenv("FUBON_API_KEY", "").strip()
    password = os.getenv("FUBON_PASSWORD", "").strip()

    sdk = FubonSDK()
    if api_key:
        result = sdk.apikey_login(personal_id, api_key, cert_path, cert_password)
        login_mode = "API_KEY"
    else:
        if not password:
            raise RuntimeError("請設定 FUBON_PASSWORD 或 FUBON_API_KEY 其中一種登入方式")
        result = sdk.login(personal_id, password, cert_path, cert_password)
        login_mode = "PASSWORD"

    success = getattr(result, "is_success", None)
    if success is False:
        raise RuntimeError(f"富邦登入失敗: {getattr(result, 'message', 'unknown error')}")

    sdk.init_realtime()
    LOG.info("Fubon login OK (%s), realtime initialized", login_mode)
    return sdk


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
        # Fubon timestamps are epoch based; accept seconds/ms/us/ns defensively.
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
    frame = frame.drop(columns=["time"], errors="ignore").dropna(how="all")
    return frame


def build_fubon_intraday(sdk, symbols: list[str]) -> dict[str, pd.DataFrame]:
    """Use Fubon for TW/TWO names and Yahoo only as fallback/for US names."""
    result: dict[str, pd.DataFrame] = {}
    tw_symbols = [s for s in symbols if s.endswith(".TW") or s.endswith(".TWO")]
    non_tw = [s for s in symbols if s not in tw_symbols]

    if non_tw:
        result.update(yahoo_download_intraday(non_tw))

    reststock = sdk.marketdata.rest_client.stock
    for symbol in tw_symbols:
        stock_id = symbol.split(".")[0]
        try:
            payload = reststock.intraday.candles(symbol=stock_id, timeframe="5", sort="asc")
            frame = _candle_frame(payload)
            if not frame.empty:
                result[symbol] = frame
                continue
            LOG.warning("Fubon returned no intraday candles for %s", symbol)
        except Exception as exc:
            LOG.warning("Fubon candles failed for %s: %s", symbol, exc)

        # One-stock Yahoo fallback keeps the whole briefing from failing.
        fallback = yahoo_download_intraday([symbol])
        if symbol in fallback:
            result[symbol] = fallback[symbol]

    LOG.info("Intraday source complete: %d/%d symbols", len(result), len(symbols))
    return result


def _git_publish(period: str) -> None:
    if os.getenv("FUBON_AUTO_GIT", "0").strip() not in {"1", "true", "TRUE", "yes", "YES"}:
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
    parser.add_argument("--period", choices=["morning", "noon", "evening"], required=True)
    parser.add_argument("--no-telegram", action="store_true")
    parser.add_argument("--auto-git", action="store_true", help="commit/push generated reports")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sdk = _login_fubon()

    # Patch only this process. Existing GitHub Actions remain a safe Yahoo fallback,
    # while local scheduled runs use Fubon first for Taiwan intraday data.
    briefing.download_intraday = lambda symbols: build_fubon_intraday(sdk, symbols)

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
        if args.auto_git:
            os.environ["FUBON_AUTO_GIT"] = "1"
        _git_publish(args.period)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
