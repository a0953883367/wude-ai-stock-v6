"""Generate the 06:00, 12:00 and 20:00 Wude AI stock briefings."""

from __future__ import annotations

import argparse
import logging
from datetime import datetime

from config import SETTINGS, TAIPEI
from data_fetcher import (
    download_history,
    download_intraday,
    fetch_broker_branches,
    fetch_core_market,
    fetch_credit_flows,
    fetch_fundamentals,
    fetch_institutional_flows,
    load_taiwan_universe,
)
from notifier import render_markdown, save_report, send_telegram
from strategy import build_features, score_candidates
from watchlist import load_watchlist


def sort_by_score(rows: list[dict]) -> list[dict]:
    """Keep every row and order valid AI scores from highest to lowest."""
    def score(row: dict) -> float:
        try:
            return float(row.get("score"))
        except (TypeError, ValueError):
            return float("-inf")

    return sorted(rows, key=score, reverse=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", choices=["morning", "noon", "evening"], required=True)
    parser.add_argument("--no-telegram", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    now = datetime.now(TAIPEI)

    market_universe = load_taiwan_universe()
    for item in market_universe:
        item.setdefault("market", "TW")
    watchlist = load_watchlist()

    combined = {item["symbol"]: item for item in market_universe}
    combined.update({item["symbol"]: item for item in watchlist})
    universe = list(combined.values())
    symbols = list(combined)

    history = download_history(symbols)
    intraday = download_intraday(symbols)
    watchlist_stock_ids = {
        item["symbol"].split(".")[0]
        for item in watchlist
        if item.get("market") == "TW"
    }
    # Free FinMind plans allow per-stock requests, so enrichment targets the
    # fixed list while the wider background scan safely remains neutral.
    institutions = fetch_institutional_flows(watchlist_stock_ids)
    credit_flows = fetch_credit_flows(watchlist_stock_ids)
    fundamentals = fetch_fundamentals(watchlist_stock_ids)
    broker_branches = fetch_broker_branches(watchlist_stock_ids)

    features = []
    for item in universe:
        symbol = item["symbol"]
        daily = history.get(symbol)
        if daily is None:
            continue
        stock_id = symbol.split(".")[0]
        institution = institutions.get(stock_id) if item.get("market") == "TW" else None
        row = build_features(item, daily, intraday.get(symbol), institution)
        if row:
            if item.get("market") == "TW":
                row.update(credit_flows.get(stock_id, {}))
                row.update(fundamentals.get(stock_id, {}))
                row.update(broker_branches.get(stock_id, {}))
            features.append(row)

    ranked = score_candidates(features)
    if not ranked:
        raise RuntimeError("本次沒有任何股票取得足夠資料，保留上一份報告")

    by_symbol = {row["symbol"]: row for row in ranked}
    watchlist_rows = sort_by_score([
        by_symbol[item["symbol"]]
        for item in watchlist
        if item["symbol"] in by_symbol
    ])
    unavailable = [item for item in watchlist if item["symbol"] not in by_symbol]
    market_top = [
        row for row in ranked
        if row.get("market") == "TW" and row.get("type") == "個股"
    ][:5]

    report = {
        "system": "武得 AI 股票助理 V6",
        "period": args.period,
        "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "timezone": "Asia/Taipei",
        "universe_count": len(market_universe),
        "analyzed_count": len(ranked),
        "watchlist_count": len(watchlist),
        "watchlist_analyzed_count": len(watchlist_rows),
        "market": fetch_core_market(),
        "data_status": {
            "finmind_configured": bool(SETTINGS.finmind_token),
            "institutional_count": len(institutions),
            "credit_count": len(credit_flows),
            "fundamental_count": len(fundamentals),
            "broker_count": len(broker_branches),
            "expected_tw_count": len(watchlist_stock_ids),
        },
        "watchlist": watchlist_rows,
        "unavailable": unavailable,
        "top": market_top,
        "method": {
            "technical": 0.28,
            "kline_and_volume_price": "included within technical weight",
            "volume_and_attack": 0.23,
            "institutional": 0.17,
            "credit_chips": "included within institutional weight when available",
            "fundamental_and_valuation": 0.10,
            "theme_resonance": 0.14,
            "support_resistance": 0.08,
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
