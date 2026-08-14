"""Generate the 06:00, 12:00 and 20:00 Wude AI stock briefings."""

from __future__ import annotations

import argparse
import logging
from datetime import datetime

from config import SETTINGS, TAIPEI
from data_fetcher import (
    download_history,
    download_intraday,
    fetch_core_market,
    fetch_institutional_flows,
    load_taiwan_universe,
)
from notifier import render_markdown, save_report, send_telegram
from strategy import build_features, score_candidates


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", choices=["morning", "noon", "evening"], required=True)
    parser.add_argument("--no-telegram", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    now = datetime.now(TAIPEI)
    universe = load_taiwan_universe()
    symbols = [item["symbol"] for item in universe]
    history = download_history(symbols)
    intraday = download_intraday(symbols)
    stock_ids = {symbol.split(".")[0] for symbol in symbols}
    institutions = fetch_institutional_flows(stock_ids)
    features = []
    for item in universe:
        symbol = item["symbol"]
        daily = history.get(symbol)
        if daily is None:
            continue
        stock_id = symbol.split(".")[0]
        row = build_features(item, daily, intraday.get(symbol), institutions.get(stock_id))
        if row:
            features.append(row)
    ranked = score_candidates(features)
    if not ranked:
        raise RuntimeError("本次沒有任何股票取得足夠資料，保留上一份報告")
    report = {
        "system": "武得 AI 股票助理 V6",
        "period": args.period,
        "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "timezone": "Asia/Taipei",
        "universe_count": len(universe),
        "analyzed_count": len(ranked),
        "market": fetch_core_market(),
        "top": ranked[: SETTINGS.top_n],
        "method": {
            "technical": 0.30,
            "volume_and_attack": 0.25,
            "institutional": 0.18,
            "theme_resonance": 0.17,
            "support_resistance": 0.10,
        },
        "disclaimer": "資料整理與風險輔助，不保證獲利，不是代客下單建議。",
    }
    markdown = render_markdown(report)
    latest_json, latest_md = save_report(report, markdown)
    delivered = False if args.no_telegram else send_telegram(markdown)
    print(markdown)
    print(f"\nSaved: {latest_json}, {latest_md}; Telegram={delivered}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

