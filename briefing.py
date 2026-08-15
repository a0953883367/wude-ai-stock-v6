"""Generate the 06:00, 12:00 and 20:00 Wude AI stock briefings."""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime

from config import SETTINGS, TAIPEI
from data_fetcher import (
    download_history,
    download_intraday,
    fetch_broker_branches,
    fetch_core_market,
    fetch_macro_history,
    fetch_credit_flows,
    fetch_fundamentals,
    fetch_financial_quality,
    fetch_institutional_flows,
    fetch_us_short_volume,
    load_taiwan_universe,
)
from macro_regime import update_macro_regime
from notifier import render_markdown, save_report, send_telegram
from news_risk import fetch_news_risks
from performance import load_performance_context, update_performance
import strategy
from sitecustomize import _tick_size
from watchlist import load_watchlist

# V6.29 trading plans call this helper from strategy.py. Bind it explicitly
# instead of relying on Python's optional sitecustomize auto-import behavior.
strategy._tick_size = _tick_size
build_features = strategy.build_features
score_candidates = strategy.score_candidates


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
    parser.add_argument(
        "--intraday",
        action="store_true",
        help="Silent background refresh; do not add a new performance prediction snapshot.",
    )
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
    us_symbols = {
        item["symbol"].upper()
        for item in universe
        if item.get("market") == "US"
    }
    # Free FinMind plans allow per-stock requests, so enrichment targets the
    # fixed list while the wider background scan safely remains neutral.
    institutions = fetch_institutional_flows(watchlist_stock_ids)
    credit_flows = fetch_credit_flows(watchlist_stock_ids)
    fundamentals = fetch_fundamentals(watchlist_stock_ids)
    financial_quality = fetch_financial_quality(watchlist_stock_ids)
    broker_branches = fetch_broker_branches(watchlist_stock_ids)
    us_short_volume = fetch_us_short_volume(us_symbols)

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
                row.update(financial_quality.get(stock_id, {}))
                row.update(broker_branches.get(stock_id, {}))
            elif item.get("market") == "US":
                row.update(us_short_volume.get(symbol.upper(), {}))
            features.append(row)

    market = fetch_core_market()
    macro_regime = update_macro_regime(
        SETTINGS.reports_dir,
        market,
        now.strftime("%Y-%m-%d %H:%M:%S"),
        args.period,
        fetch_macro_history(),
    )
    performance_context = load_performance_context(SETTINGS.reports_dir)
    # First pass selects a bounded set for the news scan. This keeps network
    # usage predictable while covering every displayed TOP20 plus a buffer and
    # every fixed-watchlist item.
    preliminary = score_candidates(features, macro_regime, performance_context)
    if not preliminary:
        raise RuntimeError("本次沒有任何股票取得足夠資料，保留上一份報告")
    news_targets_by_symbol = {
        row["symbol"]: row
        for group in (
            [row for row in preliminary if row.get("market") == "TW" and "ETF" not in str(row.get("type", ""))][:25],
            [row for row in preliminary if row.get("market") == "US" and "ETF" not in str(row.get("type", ""))][:25],
            [row for row in preliminary if "ETF" in str(row.get("type", "")).upper()][:25],
        )
        for row in group
    }
    preliminary_by_symbol = {row["symbol"]: row for row in preliminary}
    for item in watchlist:
        row = preliminary_by_symbol.get(item["symbol"])
        if row:
            news_targets_by_symbol[row["symbol"]] = row
    news_risks = fetch_news_risks(list(news_targets_by_symbol.values()))
    for row in features:
        risk = news_risks.get(row.get("symbol"))
        if risk:
            row.update(risk)

    ranked = score_candidates(features, macro_regime, performance_context)
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

    # Save the same high-to-low TOP20 groups shown by the product. Historical
    # outcomes never change today's score; they only measure later accuracy.
    backtest_groups = {
        "TW": [row for row in ranked if row.get("market") == "TW" and "ETF" not in str(row.get("type", ""))][:20],
        "US": [row for row in ranked if row.get("market") == "US" and "ETF" not in str(row.get("type", ""))][:20],
        "ETF": [row for row in ranked if "ETF" in str(row.get("type", ""))][:20],
    }
    predictions = []
    for group, rows in backtest_groups.items():
        for rank, row in enumerate(rows, 1):
            item = dict(row)
            item["backtest_group"] = group
            item["backtest_rank"] = rank
            predictions.append(item)
    if args.intraday:
        # Background refreshes may run many times per trading day. Reuse the
        # latest verified context so they do not distort the backtest sample.
        performance = performance_context
    else:
        performance = update_performance(
            SETTINGS.reports_dir,
            predictions,
            ranked,
            now.strftime("%Y-%m-%d %H:%M:%S"),
            args.period,
        )

    report = {
        "system": "武得 AI 股票助理 V6",
        "period": args.period,
        "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "timezone": "Asia/Taipei",
        "universe_count": len(market_universe),
        "analyzed_count": len(ranked),
        "watchlist_count": len(watchlist),
        "watchlist_analyzed_count": len(watchlist_rows),
        "market": market,
        "macro_regime": macro_regime,
        "data_status": {
            "finmind_configured": bool(SETTINGS.finmind_token),
            "institutional_count": len(institutions),
            "credit_count": len(credit_flows),
            "fundamental_count": len(fundamentals),
            "financial_quality_count": len(financial_quality),
            "broker_count": len(broker_branches),
            "us_short_volume_count": len(us_short_volume),
            "news_scanned_count": len(news_risks),
            "news_verified_risk_count": sum(
                1 for item in news_risks.values() if item.get("news_penalty", 0) > 0
            ),
            "expected_tw_count": len(watchlist_stock_ids),
        },
        "watchlist": watchlist_rows,
        "unavailable": unavailable,
        "top": market_top,
        "performance": performance,
        "method": {
            "technical": 0.28,
            "kline_and_volume_price": "included within technical weight",
            "volume_and_attack": 0.23,
            "institutional": 0.17,
            "credit_chips": "included within institutional weight when available",
            "fundamental_and_valuation": 0.10,
            "financial_quality": "included within fundamental weight when available",
            "theme_resonance": 0.14,
            "support_resistance": 0.08,
            "macro_risk": "historical sessions are backfilled; adjustment is capped at +/-4 points",
            "verified_outcome_feedback": "actual saved 1/5-day returns only; statistically shrunk and capped at +/-2 points",
        },
        "disclaimer": "資料整理與風險輔助，不保證獲利，不是代客下單建議。",
    }
    markdown = render_markdown(report)
    latest_json, latest_md = save_report(report, markdown)

    # The homepage needs current opportunity rankings, not the legacy static
    # stock_data.json scores. Keep only TOP20 per group to control repository
    # growth during the two-hour background refresh.
    ranking_rows = []
    for group in ("TW", "US", "ETF"):
        ranking_rows.extend(backtest_groups[group])
    ranking_payload = {
        "updated_at": report["updated_at"],
        "period": args.period,
        "ranking_basis": "entry_score",
        "data": ranking_rows,
    }
    ranking_path = SETTINGS.reports_dir / "rankings.json"
    ranking_tmp = SETTINGS.reports_dir / "rankings.tmp"
    ranking_tmp.write_text(
        json.dumps(ranking_payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    ranking_tmp.replace(ranking_path)

    delivered = False if args.no_telegram else send_telegram(markdown)
    print(markdown)
    print(f"\nSaved: {latest_json}, {latest_md}; Telegram={delivered}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
