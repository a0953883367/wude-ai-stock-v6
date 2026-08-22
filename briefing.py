"""Generate the 06:00, 12:00 and 20:00 Wude AI stock briefings."""

from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import datetime

from config import SETTINGS, TAIPEI
from data_fetcher import (
    download_history,
    download_intraday,
    download_us_extended_hours,
    fetch_broker_branches,
    fetch_core_market,
    fetch_macro_history,
    fetch_credit_flows,
    fetch_fundamentals,
    fetch_financial_quality,
    fetch_etf_metadata,
    fetch_institutional_flows,
    fetch_us_short_volume,
    fetch_us_company_metadata,
    load_search_universe,
)
from macro_regime import update_macro_regime
from notifier import render_markdown, save_report, send_telegram
from news_risk import fetch_news_risks, merge_official_announcements
from performance import load_frozen_forecasts, load_performance_context, update_performance
from market_models import (
    assess_market_data_quality,
    enforce_market_contract,
    validate_taiwan_data,
)
from model_lab import track_predictions
from us_market_data import fetch_us_opra_signals, fetch_us_sip_snapshots
import strategy
from tw_official_data import (
    fetch_taiwan_official_data,
    merge_official_with_fallback,
    overlay_official_daily,
)
from tw_market_context import build_tw_market_context
from watchlist import load_watchlist

build_features = strategy.build_features
score_candidates = strategy.score_candidates


def _stage(label: str, action):
    """Log every external-data stage so a slow provider is immediately visible."""
    started = time.monotonic()
    logging.info("資料階段開始：%s", label)
    result = action()
    logging.info("資料階段完成：%s（%.1f 秒）", label, time.monotonic() - started)
    return result


_NEXT_SESSION_FIELDS = (
    "next_session_model_version", "next_session_market_model",
    "next_session_direction", "next_session_confidence",
    "next_session_up_votes", "next_session_down_votes",
    "next_session_abstain_votes", "next_session_tracks",
    "next_session_model_votes",
    "next_session_note", "next_session_source_session_date",
    "next_session_generated_at", "next_session_signal_level",
    "next_session_data_quality", "next_session_data_mode",
)


def _prediction_checkpoint_ready(row: dict, period: str | None, intraday: bool) -> bool:
    """Only freeze a forecast after that market's regular session completed."""
    if period is None:  # Unit/API callers that explicitly request a prediction.
        return True
    if intraday:
        return False
    market = str(row.get("market") or "").upper()
    return (market == "TW" and period == "evening") or (
        market == "US" and period == "morning"
    )


def _attach_next_session_predictions(
    rows: list[dict],
    period: str | None = None,
    intraday: bool = False,
    previous: dict[str, dict] | None = None,
    generated_at: str = "",
) -> None:
    """Attach fixed-close shadow predictions without changing rank/actions.

    TW forecasts are frozen by the evening report and US forecasts by the
    morning report. Noon/intraday refreshes carry that exact forecast forward
    rather than recomputing it with an unfinished candle.
    """
    labels = {"UP": "📈 看漲", "DOWN": "📉 看跌", "ABSTAIN": "⚪ 棄權"}
    previous = previous or {}
    for row in rows:
        source_date = str(row.get("official_session_date") or "")
        prior = previous.get(str(row.get("symbol") or ""), {})
        completed_direction = str(prior.get("next_session_direction") or "")
        same_close = bool(
            str(prior.get("next_session_model_version") or "").endswith("-shadow")
            and str(prior.get("next_session_source_session_date") or "") == source_date
            and str(prior.get("next_session_generated_at") or "").strip()
            and completed_direction in labels.values()
        )
        is_tw = str(row.get("market") or "").upper() == "TW"
        verified_close = bool(
            same_close
            # A mutable report with the same date is not evidence of fixation.
            and str(prior.get("next_session_data_mode") or "")
            == "固定快照（雜湊驗證通過）"
        )
        carry_close = verified_close if is_tw else same_close
        # Taiwan's immutable close snapshot is authoritative even when the
        # same evening report is re-run on a weekend or provider outage. US is
        # intentionally left on its existing behavior.
        if verified_close and is_tw:
            for field in _NEXT_SESSION_FIELDS:
                if field in prior:
                    row[field] = prior[field]
            row["next_session_note"] = (
                f"沿用 {source_date} 收盤後固定預測；重新整理不重新配分。"
            )
            continue
        if not _prediction_checkpoint_ready(row, period, intraday):
            if carry_close:
                for field in _NEXT_SESSION_FIELDS:
                    if field in prior:
                        row[field] = prior[field]
                row["next_session_note"] = (
                    f"沿用 {source_date} 收盤後固定預測；盤中與其他時段不重新配分。"
                )
                continue
            row.update({
                "next_session_model_version": (
                    "V7-shadow" if row.get("market") == "TW" else "V6-shadow"
                ),
                "next_session_market_model": (
                    "TW-NEXT-V7" if row.get("market") == "TW" else "US-NEXT-V6"
                ),
                "next_session_direction": "🕒 等待收盤",
                "next_session_confidence": 0.0,
                "next_session_up_votes": 0,
                "next_session_down_votes": 0,
                "next_session_abstain_votes": 10,
                "next_session_tracks": {},
                "next_session_note": (
                    "台股等待晚報使用完整收盤資料計算。"
                    if row.get("market") == "TW"
                    else "美股等待早報使用完整收盤、盤後與市場資料計算。"
                ),
                "next_session_source_session_date": source_date,
                "next_session_generated_at": "",
                "next_session_signal_level": "WAITING",
                "next_session_data_quality": 0.0,
                "next_session_data_mode": "尚未到正式收盤檢查點",
            })
            continue
        shadow = track_predictions(row)
        compact_tracks = {track: bundle["consensus"] for track, bundle in shadow.items()}
        full_day = compact_tracks["full_day"]
        row["next_session_model_version"] = (
            "V7-shadow" if row.get("market") == "TW" else "V6-shadow"
        )
        row["next_session_market_model"] = (
            "TW-NEXT-V7" if row.get("market") == "TW" else "US-NEXT-V6"
        )
        row["next_session_direction"] = labels[full_day["direction"]]
        row["next_session_confidence"] = full_day["confidence"]
        row["next_session_up_votes"] = full_day["up_votes"]
        row["next_session_down_votes"] = full_day["down_votes"]
        row["next_session_abstain_votes"] = full_day["abstain_votes"]
        row["next_session_signal_level"] = full_day["signal_level"]
        row["next_session_tracks"] = compact_tracks
        row["next_session_model_votes"] = (
            shadow["full_day"]["models"]
            if str(row.get("market") or "").upper() == "TW"
            else {}
        )
        row["next_session_source_session_date"] = source_date
        row["next_session_generated_at"] = generated_at
        row["next_session_data_quality"] = min(
            float(row.get("market_data_quality_score") or 0.0),
            min(
                float(bundle["models"]["balanced_next"].get("evidence_coverage_pct") or 0.0)
                for bundle in shadow.values()
            ),
        )
        if row.get("market") == "US" and not row.get("us_live_data_available"):
            row["next_session_data_mode"] = "FINRA／盤前盤後備援（SIP未取得）"
        elif row.get("market") == "US" and not row.get("us_option_data_available"):
            row["next_session_data_mode"] = "SIP行情；OPRA未取得"
        else:
            row["next_session_data_mode"] = "主要資料完整"
        if full_day["direction"] == "ABSTAIN":
            if row["next_session_data_quality"] < 75:
                abstain_note = (
                    f"棄權：隔日資料完整度 {row['next_session_data_quality']:.0f} 分，"
                    "未達 75 分；不以缺資料硬猜。"
                )
            elif max(full_day["up_votes"], full_day["down_votes"]) < 6:
                abstain_note = (
                    f"棄權：同方向最高 {max(full_day['up_votes'], full_day['down_votes'])}/10 票，"
                    "未達研究門檻 6 票。"
                )
            else:
                abstain_note = "棄權：多空票差或證據強度未達門檻，不代表預測平盤。"
        else:
            abstain_note = ""
        row["next_session_note"] = (
            abstain_note
            if full_day["direction"] == "ABSTAIN"
            else (
                "強訊號仍是影子預測，不代表保證獲利。"
                if full_day["signal_level"] == "STRONG"
                else "研究方向只供隔日驗證，不是交易訊號；命中率須由實際結果計算。"
            )
        )


def _previous_rows() -> dict[str, dict]:
    """Load the last atomic report as a safe intraday enrichment snapshot."""
    try:
        payload = json.loads(
            (SETTINGS.reports_dir / "all_analysis.json").read_text(encoding="utf-8")
        )
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return {
        str(row.get("symbol") or ""): row
        for row in payload.get("data", [])
        if row.get("symbol")
    }


def _tw_intraday_enrichment(previous: dict[str, dict]) -> tuple[dict, dict, dict, dict]:
    """Reuse non-intraday Taiwan fields; prices/technicals are still downloaded live."""
    institutions: dict[str, dict] = {}
    credit: dict[str, dict] = {}
    fundamentals: dict[str, dict] = {}
    brokers: dict[str, dict] = {}
    fundamental_keys = {
        "fundamental_available", "per", "pbr", "dividend_yield",
        "revenue_year", "revenue_month", "monthly_revenue",
        "valuation_date", "valuation_source", "valuation_official",
        "revenue_date", "revenue_source", "revenue_unit", "revenue_official",
    }
    for symbol, row in previous.items():
        if row.get("market") != "TW":
            continue
        sid = symbol.split(".")[0]
        institutions[sid] = {
            key: value for key, value in row.items()
            if key == "available" or key.startswith(("foreign", "trust", "dealer", "institution_"))
        }
        credit[sid] = {
            key: value for key, value in row.items()
            if key.startswith(("credit_", "margin_", "short_", "sbl_"))
        }
        fundamentals[sid] = {
            key: value for key, value in row.items()
            if key in fundamental_keys or key.startswith("revenue_")
        }
        broker_snapshot = {
            key: value for key, value in row.items()
            if key.startswith(("broker_", "top_brokers_"))
        }
        # Do not turn an empty/stale previous row into a seemingly available
        # broker-branch record.  The live status counter and scoring must only
        # see records that were actually fetched and dated.
        if broker_snapshot.get("broker_available") and broker_snapshot.get("broker_date"):
            brokers[sid] = broker_snapshot
    return institutions, credit, fundamentals, brokers


def sort_by_score(rows: list[dict]) -> list[dict]:
    """Keep every row and order valid AI scores from highest to lowest."""
    def score(row: dict) -> float:
        try:
            return float(row.get("score"))
        except (TypeError, ValueError):
            return float("-inf")

    return sorted(rows, key=score, reverse=True)


def _qualified_tw_market_top(ranked: list[dict], limit: int = 5) -> list[dict]:
    """Return only Taiwan companies that passed every overall safety gate."""
    return [
        row
        for row in ranked
        if row.get("market") == "TW"
        and row.get("type") == "個股"
        and row.get("overall_rank_tier") == 2
    ][:limit]


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

    search_universe = load_search_universe()
    watchlist = load_watchlist()

    combined = {item["symbol"]: item for item in search_universe}
    combined.update({item["symbol"]: item for item in watchlist})
    universe = list(combined.values())
    symbols = list(combined)

    history = _stage("日線價格", lambda: download_history(symbols))
    intraday = _stage("盤中量價", lambda: download_intraday(symbols))
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
    us_live = _stage(
        "美股即時行情",
        lambda: fetch_us_sip_snapshots(us_symbols, timeout=SETTINGS.request_timeout),
    )
    us_extended_hours = _stage(
        "美股盤前盤後", lambda: download_us_extended_hours(list(us_symbols))
    )
    # Free FinMind plans allow per-stock requests, so enrichment targets the
    # fixed list while the wider background scan safely remains neutral.
    previous_rows = _previous_rows()
    # Do not trust a mutable report over the fixed TW forecast. This overlay
    # repairs earlier reports that were recomputed after the close.
    previous_rows.update(load_frozen_forecasts(SETTINGS.reports_dir, market="TW"))
    previous = previous_rows if args.intraday else {}
    if previous:
        logging.info("盤中更新沿用上一份法人、融資、基本面與分點快照")
        institutions, credit_flows, fundamentals, broker_branches = (
            _tw_intraday_enrichment(previous)
        )
    else:
        institutions = _stage(
            "台股三大法人", lambda: fetch_institutional_flows(watchlist_stock_ids)
        )
        credit_flows = _stage(
            "台股融資券", lambda: fetch_credit_flows(watchlist_stock_ids)
        )
        fundamentals = _stage(
            "台股估值營收", lambda: fetch_fundamentals(watchlist_stock_ids)
        )
        broker_branches = _stage(
            "台股券商分點", lambda: fetch_broker_branches(watchlist_stock_ids)
        )
    # Official exchange data is fetched once for the entire Taiwan universe.
    # It replaces Taiwan fields only; US inputs and scoring stay untouched.
    # FinMind/previous snapshots remain fallbacks for multi-session history.
    tw_official = _stage(
        "台股官方資料", lambda: fetch_taiwan_official_data(universe)
    )
    institutions = merge_official_with_fallback(
        institutions, tw_official.get("institutions", {}), kind="institution"
    )
    credit_flows = merge_official_with_fallback(
        credit_flows, tw_official.get("credit", {}), kind="credit"
    )
    fundamentals = merge_official_with_fallback(
        fundamentals, tw_official.get("fundamentals", {}), kind="fundamental"
    )
    financial_quality = _stage(
        "財務品質快取", lambda: fetch_financial_quality(watchlist_stock_ids)
    )
    us_short_volume = _stage("美股放空量", lambda: fetch_us_short_volume(us_symbols))
    us_company_metadata = _stage(
        "美股公司資料快取", lambda: fetch_us_company_metadata(universe)
    )
    etf_metadata = _stage("ETF 資料快取", lambda: fetch_etf_metadata(universe))

    features = []
    for item in universe:
        symbol = item["symbol"]
        daily = history.get(symbol)
        official_price = None
        if item.get("market") == "TW":
            official_price = tw_official.get("prices", {}).get(symbol.split(".")[0])
            daily = overlay_official_daily(daily, official_price)
        if daily is None:
            continue
        stock_id = symbol.split(".")[0]
        institution = institutions.get(stock_id) if item.get("market") == "TW" else None
        row = build_features(item, daily, intraday.get(symbol), institution)
        if row:
            row.update(etf_metadata.get(symbol, {}))
            if item.get("market") == "TW":
                if official_price:
                    row.update({
                        "tw_official_price_available": bool(
                            official_price.get("tw_official_price_available")
                        ),
                        "tw_official_session_date": official_price.get("date"),
                        "tw_price_source": official_price.get("tw_price_source"),
                        "tw_price_unit": official_price.get("tw_price_unit"),
                    })
                if institution:
                    row.update({
                        key: value for key, value in institution.items()
                        if key in {
                            "institution_date", "institution_source",
                            "institution_unit", "institution_official",
                            "institution_multiday_available",
                        }
                    })
                row.update(credit_flows.get(stock_id, {}))
                row.update(fundamentals.get(stock_id, {}))
                row.update(financial_quality.get(stock_id, {}))
                row.update(broker_branches.get(stock_id, {}))
            elif item.get("market") == "US":
                row.update(us_live.get(symbol.upper(), {}))
                row.update(us_extended_hours.get(symbol.upper(), {}))
                row.update(us_short_volume.get(symbol.upper(), {}))
                if "ETF" not in str(item.get("type", "")).upper():
                    row.update(us_company_metadata.get(symbol.upper(), {}))
            if item.get("market") == "TW":
                row = validate_taiwan_data(row)
            features.append(enforce_market_contract(row))

    market = _stage("核心市場", fetch_core_market)
    tw_market_context = build_tw_market_context(features, market)
    nasdaq_raw = (market.get("Nasdaq") or {}).get("change_pct")
    nasdaq_change = float(nasdaq_raw) if nasdaq_raw is not None else None
    vix = float((market.get("VIX") or {}).get("price") or 0)
    market_risk_score = (
        75.0 if 0 < vix < 18 else
        60.0 if 18 <= vix < 25 else
        40.0 if 25 <= vix < 35 else
        20.0 if vix >= 35 else None
    )
    for row in features:
        if row.get("market") == "US":
            row["us_market_relative_strength_pct"] = (
                round(float(row.get("change_pct") or 0) - nasdaq_change, 2)
                if nasdaq_change is not None else None
            )
            row["us_market_risk_score"] = market_risk_score
            row["us_market_vix"] = vix or None
    macro_history = _stage("總經歷史", fetch_macro_history)
    macro_regime = _stage(
        "總經判斷",
        lambda: update_macro_regime(
            SETTINGS.reports_dir,
            market,
            now.strftime("%Y-%m-%d %H:%M:%S"),
            args.period,
            macro_history,
            persist=not args.intraday,
        ),
    )
    performance_context = load_performance_context(SETTINGS.reports_dir)
    # First pass selects a bounded set for the news scan. This keeps network
    # usage predictable while covering every displayed TOP20 plus a buffer and
    # every fixed-watchlist item.
    preliminary = _stage(
        "第一次排名計算",
        lambda: score_candidates(
            features, macro_regime, performance_context, tw_market_context
        ),
    )
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
    option_targets = [
        row for row in news_targets_by_symbol.values()
        if row.get("market") == "US" and "ETF" not in str(row.get("type", "")).upper()
    ]
    us_options = _stage(
        "美股選擇權",
        lambda: fetch_us_opra_signals(option_targets, timeout=SETTINGS.request_timeout),
    )
    if us_options:
        for row in features:
            row.update(us_options.get(str(row.get("symbol") or "").upper(), {}))
        preliminary = _stage(
            "選擇權後排名重算",
            lambda: score_candidates(
                features, macro_regime, performance_context, tw_market_context
            ),
        )
        preliminary_by_symbol = {row["symbol"]: row for row in preliminary}
        news_targets_by_symbol = {
            symbol: preliminary_by_symbol.get(symbol, row)
            for symbol, row in news_targets_by_symbol.items()
        }
    news_risks = _stage(
        "候選股新聞", lambda: fetch_news_risks(list(news_targets_by_symbol.values()))
    )
    for row in features:
        risk = news_risks.get(row.get("symbol"))
        if risk:
            row.update(risk)
        if row.get("market") == "TW":
            sid = str(row.get("symbol") or "").split(".")[0]
            announcements = tw_official.get("announcements", {}).get(sid, [])
            if announcements:
                row.update(merge_official_announcements(
                    risk or {}, announcements,
                    symbol=str(row.get("symbol") or ""),
                    name=str(row.get("name") or ""),
                ))
        row.update(assess_market_data_quality(row))

    ranked = _stage(
        "最終排名計算",
        lambda: score_candidates(
            features, macro_regime, performance_context, tw_market_context
        ),
    )
    _attach_next_session_predictions(
        ranked,
        period=args.period,
        intraday=args.intraday,
        previous=previous_rows,
        generated_at=now.strftime("%Y-%m-%d %H:%M:%S"),
    )
    by_symbol = {row["symbol"]: row for row in ranked}
    watchlist_rows = sort_by_score([
        by_symbol[item["symbol"]]
        for item in watchlist
        if item["symbol"] in by_symbol
    ])
    unavailable = [item for item in watchlist if item["symbol"] not in by_symbol]
    market_top = _qualified_tw_market_top(ranked)

    # Save the same qualification-first groups shown by the product.  Unsafe
    # or incomplete rows may remain visible as observations, but they are not
    # recorded as TOP signals for performance statistics.
    backtest_groups = {
        "TW_STOCK": [row for row in ranked if row.get("market") == "TW" and "ETF" not in str(row.get("type", ""))],
        "TW_ETF": [row for row in ranked if row.get("market") == "TW" and "ETF" in str(row.get("type", ""))],
        "US_STOCK": [row for row in ranked if row.get("market") == "US" and "ETF" not in str(row.get("type", ""))][:20],
        "US_ETF": [row for row in ranked if row.get("market") == "US" and "ETF" in str(row.get("type", ""))][:20],
    }
    # The ten candidate models must see the same fixed shadow universe.  Rows
    # that are not actual buy triggers remain useful for model comparison but
    # are never counted as executed trade signals.
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
        "run_mode": "intraday_refresh" if args.intraday else "scheduled_report",
        "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "timezone": "Asia/Taipei",
        "universe_count": len(search_universe),
        "analyzed_count": len(ranked),
        "watchlist_count": len(watchlist),
        "watchlist_analyzed_count": len(watchlist_rows),
        "market": market,
        "macro_regime": macro_regime,
        "tw_market_context": tw_market_context,
        "data_status": {
            "finmind_configured": bool(SETTINGS.finmind_token),
            "institutional_count": len(institutions),
            "credit_count": len(credit_flows),
            "fundamental_count": len(fundamentals),
            "tw_official_price_count": len(tw_official.get("prices", {})),
            "tw_official_institution_count": len(tw_official.get("institutions", {})),
            "tw_official_credit_count": len(tw_official.get("credit", {})),
            "tw_official_fundamental_count": len(tw_official.get("fundamentals", {})),
            "tw_official_announcement_count": len(tw_official.get("announcements", {})),
            "financial_quality_count": len(financial_quality),
            "broker_count": sum(
                1 for item in broker_branches.values()
                if item.get("broker_available") and item.get("broker_date")
            ),
            "us_short_volume_count": len(us_short_volume),
            "us_extended_hours_count": len(us_extended_hours),
            "us_sip_count": len(us_live),
            "us_opra_count": len(us_options),
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
            "overall_company": {
                "technical_trend": 0.20,
                "volume_momentum": 0.15,
                "institutional_flow": 0.15,
                "financial_quality": 0.15,
                "growth": 0.15,
                "valuation": 0.10,
                "news_risk": 0.10,
            },
            "overall_ranking": "先依合格／觀察／阻擋分層，再綜合AI總分45%、進場分35%、短線10%、中長線10%與資料信心排序",
            "short_term": "1至5個交易日；先依合格與安全條件分層，再以量價、短均線與K線、籌碼、開盤攻擊量及風報比排序",
            "next_session": "V6市場分流隔日影子預測；台股晚報、美股早報才以完整收盤資料固定預測。研究方向供向前驗證，強訊號另行標示；兩市場採不同權重且不影響原排名",
            "mid_long_term": "3至12個月；先依合格與重大風險分層，再以財務品質、成長、估值、中期趨勢、法人籌碼及新聞風險排序",
            "etf": "台灣與美國ETF分開計分；使用流動性、折溢價、風險、成本、追蹤與組合品質，不套用個股財報模型",
            "missing_data": "缺少的維度不以中性50分補入排名；降低資料信心並依門檻限制資格",
            "market_isolation": "台股個股TW-STOCK-V5、台灣ETF TW-ETF-V5與美股US-V3使用獨立資料契約；台股採個股／同族群／台股市場分層校正，跨市場欄位會在計分前清除",
            "tw_layered_context": "台股個股以個股70%、同族群20%、台股市場10%計分；台灣ETF提高市場層比重。缺少背景資料時不以中性值冒充，且不影響US-V3",
            "extended_hours": "美股盤前／盤後僅作跳空與風險提示，不直接增加AI分數",
            "us_live_data": "美股以SIP全市場報價為主、OPRA選擇權為風險層；未設定授權時保留Yahoo/SEC/FINRA備援且明確降低資料涵蓋",
            "macro_risk": "historical sessions are backfilled; adjustment is capped at +/-4 points",
            "verified_outcome_feedback": "V6 market-specific close-to-open, open-to-close, and close-to-close shadow outcomes; four market/asset cohorts; no automatic score effect",
        },
        "disclaimer": "資料整理與風險輔助，不保證獲利，不是代客下單建議。",
    }
    markdown = render_markdown(report)
    latest_json, latest_md = save_report(
        report,
        markdown,
        save_snapshot=not args.intraday,
    )

    # The homepage needs current opportunity rankings, not the legacy static
    # stock_data.json scores. Keep only TOP20 per group to control repository
    # growth during the two-hour background refresh.
    ranking_rows = []
    for group in ("TW_STOCK", "TW_ETF", "US_STOCK", "US_ETF"):
        ranking_rows.extend(backtest_groups[group])
    ranking_payload = {
        "updated_at": report["updated_at"],
        "period": args.period,
        "run_mode": report["run_mode"],
        "ranking_basis": "overall_rank_tier_then_overall_ranking_score",
        "data": ranking_rows,
    }
    ranking_path = SETTINGS.reports_dir / "rankings.json"
    ranking_tmp = SETTINGS.reports_dir / "rankings.tmp"
    ranking_tmp.write_text(
        json.dumps(ranking_payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    ranking_tmp.replace(ranking_path)

    all_analysis_payload = {
        "updated_at": report["updated_at"],
        "period": args.period,
        "run_mode": report["run_mode"],
        "candidate_count": len(universe),
        "analyzed_count": len(ranked),
        "unavailable_count": max(0, len(universe) - len(ranked)),
        "data": ranked,
    }
    all_analysis_path = SETTINGS.reports_dir / "all_analysis.json"
    all_analysis_tmp = SETTINGS.reports_dir / "all_analysis.tmp"
    all_analysis_tmp.write_text(
        json.dumps(all_analysis_payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    all_analysis_tmp.replace(all_analysis_path)

    delivered = False if args.no_telegram else send_telegram(markdown)
    print(markdown)
    print(f"\nSaved: {latest_json}, {latest_md}; Telegram={delivered}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
