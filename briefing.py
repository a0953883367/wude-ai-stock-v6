"""Generate the 06:00, 12:00 and 20:00 Wude AI stock briefings."""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from collections import Counter
from datetime import datetime
from urllib.request import Request, urlopen

from config import SETTINGS, TAIPEI
from data_fetcher import (
    download_history,
    download_intraday,
    download_us_extended_hours,
    fetch_broker_branches,
    fetch_core_market,
    fetch_macro_history,
    fetch_market_regime_history,
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
from holding_simulation import update_holding_simulation
from million_simulation import update_million_simulation
from weight_experiment import update_weight_experiment
from exit_horizon_experiment import update_exit_horizon_experiment
from inverse_experiment import (
    build_inverse_market_rows,
    load_inverse_experiment_watchlist,
    update_inverse_experiment,
)
from inverse_etf_shadow import (
    build_product_rows as build_inverse_etf_product_rows,
    load_catalog as load_inverse_etf_catalog,
    update_inverse_etf_shadow,
)
from missed_strength_validation import update_missed_strength_validation
from us_market_data import fetch_us_opra_signals, fetch_us_sip_snapshots
import strategy
from tw_official_data import (
    fetch_taiwan_official_data,
    merge_official_with_fallback,
    overlay_official_daily,
)
from tw_market_context import build_tw_market_context
from stockq_market_context import (
    apply_stockq_market_fallback,
    update_stockq_market_context,
)
from stockq_us_close import (
    apply_stockq_us_close_to_simulation_rows,
    load_us_shadow_symbols,
    update_stockq_us_close_fallback,
)
from tiingo_us_close import (
    apply_tiingo_us_close_to_simulation_rows,
    update_tiingo_us_close_fallback,
)
from tw_financial_official import fetch_tw_official_financials
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


def _tiingo_public_summary(payload: dict) -> dict:
    """Expose coverage diagnostics without leaking internal-use raw prices."""
    private_keys = {
        "rows", "covered_requested_symbols", "missing_requested_symbols"
    }
    return {key: value for key, value in payload.items() if key not in private_keys}


def _update_market_rotation_shadow_safely(
    reports_dir,
    rows,
    *,
    period: str,
    updated_at: str,
    intraday: bool,
    daily_flow: dict | None = None,
) -> bool:
    """Run the research-only module without risking the formal report pipeline."""
    health_path = reports_dir / "market_rotation_shadow_health.json"
    try:
        previous = json.loads(health_path.read_text(encoding="utf-8"))
        if not isinstance(previous, dict):
            previous = {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        previous = {}
    try:
        # Keep this import inside the isolation boundary.  Import or runtime
        # failures in the research module must not stop rankings or briefings.
        from market_rotation_shadow import update_market_rotation_shadow

        update_market_rotation_shadow(
            reports_dir,
            rows,
            period=period,
            updated_at=updated_at,
            intraday=intraday,
            daily_flow=daily_flow,
        )
    except Exception as exc:  # noqa: BLE001 - deliberate research isolation
        logging.exception("族群輪動影子模組失敗；正式報表繼續")
        health = {
            "status": "warning",
            "checked_at": updated_at,
            "last_success_at": previous.get("last_success_at"),
            "error_type": type(exc).__name__,
            "detail": str(exc)[:300] or "輪動模組發生未分類錯誤",
            "formal_pipeline_continues": True,
            "changes_rankings": False,
            "places_orders": False,
        }
        success = False
    else:
        if intraday:
            # Intraday mode intentionally does not recalculate rotation.  Do
            # not let that no-op erase a warning from the last full run.
            health = {
                **previous,
                "status": previous.get("status") or "pending",
                "checked_at": updated_at,
                "detail": previous.get("detail") or "等待下一次完成交易日輪動檢查",
                "formal_pipeline_continues": True,
                "changes_rankings": False,
                "places_orders": False,
            }
        else:
            health = {
                "status": "ok",
                "checked_at": updated_at,
                "last_success_at": updated_at,
                "detail": "市場規則與族群輪動影子模組正常",
                "formal_pipeline_continues": True,
                "changes_rankings": False,
                "places_orders": False,
            }
        success = True
    tmp = reports_dir / "market_rotation_shadow_health.tmp"
    tmp.write_text(json.dumps(health, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(health_path)
    return success


def _fetch_closed_daily_flow(*, intraday: bool) -> dict:
    """Read sanitized closed-session flow; never fetch it for an intraday run."""
    if intraday:
        return {}
    url = os.getenv(
        "CAPITAL_FLOW_DAILY_URL",
        "https://wude-ai-stock-v6-production.up.railway.app/api/capital-flow-daily",
    ).strip()
    if not url:
        return {}
    try:
        request = Request(url, headers={"User-Agent": "wude-daily-briefing/1"})
        with urlopen(request, timeout=6) as response:  # noqa: S310 - fixed HTTPS endpoint
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - optional shadow evidence only
        logging.warning("收盤資金流日結讀取失敗；輪動沿用原證據：%s", exc)
        return {}
    if not isinstance(payload, dict) or payload.get("mode") != "closed_session_shadow_only":
        return {}
    return payload


def _persist_closed_daily_flow(reports_dir, payload: dict) -> bool:
    """Atomically cache sanitized completed sessions for linked page evidence."""
    if not isinstance(payload, dict) or payload.get("mode") != "closed_session_shadow_only":
        return False
    if (payload.get("policy") or {}).get("intraday_exposed") is not False:
        return False
    path = reports_dir / "capital_flow_daily.json"
    temporary = reports_dir / "capital_flow_daily.tmp"
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(path)
    return True


def _update_valuation_risk_shadow_safely(
    reports_dir,
    rows,
    *,
    period: str,
    updated_at: str,
    intraday: bool,
) -> bool:
    """Quarantine valuation research failures from every formal output."""
    health_path = reports_dir / "valuation_risk_shadow_health.json"
    try:
        previous = json.loads(health_path.read_text(encoding="utf-8"))
        if not isinstance(previous, dict):
            previous = {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        previous = {}
    try:
        from valuation_risk_shadow import update_valuation_risk_shadow

        update_valuation_risk_shadow(
            reports_dir,
            rows,
            period=period,
            updated_at=updated_at,
            intraday=intraday,
        )
    except Exception as exc:  # noqa: BLE001 - deliberate shadow isolation
        logging.exception("估值風險影子模組失敗；正式報表繼續")
        health = {
            "status": "warning",
            "checked_at": updated_at,
            "last_success_at": previous.get("last_success_at"),
            "error_type": type(exc).__name__,
            "detail": str(exc)[:300] or "估值影子發生未分類錯誤",
            "formal_pipeline_continues": True,
            "changes_rankings": False,
            "changes_weights": False,
            "places_orders": False,
        }
        success = False
    else:
        health = {
            "status": "ok",
            "checked_at": updated_at,
            "last_success_at": updated_at,
            "detail": "估值風險雷達正常；僅累積影子資料",
            "formal_pipeline_continues": True,
            "changes_rankings": False,
            "changes_weights": False,
            "places_orders": False,
        }
        success = True
    tmp = reports_dir / "valuation_risk_shadow_health.tmp"
    tmp.write_text(json.dumps(health, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(health_path)
    return success


def _update_decision_hub_safely(
    reports_dir,
    rows,
    *,
    period: str,
    updated_at: str,
    intraday: bool,
    institution_status: dict | None = None,
) -> bool:
    """Keep the downstream decision hub isolated from formal rankings."""
    health_path = reports_dir / "decision_hub_health.json"
    try:
        previous = json.loads(health_path.read_text(encoding="utf-8"))
        if not isinstance(previous, dict):
            previous = {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        previous = {}
    try:
        from decision_hub import update_decision_hub

        update_decision_hub(
            reports_dir,
            rows,
            period=period,
            updated_at=updated_at,
            intraday=intraday,
            institution_status=institution_status,
        )
    except Exception as exc:  # noqa: BLE001 - deliberate downstream isolation
        logging.exception("中央決策中樞失敗；正式排名與報表繼續")
        health = {
            "status": "warning",
            "checked_at": updated_at,
            "last_success_at": previous.get("last_success_at"),
            "error_type": type(exc).__name__,
            "detail": str(exc)[:300] or "中央決策中樞發生未分類錯誤",
            "formal_pipeline_continues": True,
            "changes_rankings": False,
            "changes_weights": False,
            "places_orders": False,
        }
        success = False
    else:
        health = {
            "status": "ok",
            "checked_at": updated_at,
            "last_success_at": updated_at,
            "detail": "中央決策中樞正常；只讀取證據並等待人工決定",
            "formal_pipeline_continues": True,
            "changes_rankings": False,
            "changes_weights": False,
            "places_orders": False,
        }
        success = True
    tmp = reports_dir / "decision_hub_health.tmp"
    tmp.write_text(json.dumps(health, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(health_path)
    return success


def _update_prediction_engine_safely(
    reports_dir,
    rows,
    *,
    period: str,
    updated_at: str,
    intraday: bool,
) -> bool:
    """Run the private prediction store before Central AI reads its contract."""
    health_path = reports_dir / "prediction_engine_health.json"
    try:
        previous = json.loads(health_path.read_text(encoding="utf-8"))
        if not isinstance(previous, dict):
            previous = {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        previous = {}
    try:
        from prediction_engine import run_prediction_engine

        run_prediction_engine(
            reports_dir,
            rows,
            period=period,
            updated_at=updated_at,
            intraday=intraday,
        )
    except Exception as exc:  # noqa: BLE001 - independent engine isolation
        logging.exception("獨立 AI 預判引擎失敗；正式 V6 與既有紀錄繼續")
        health = {
            "status": "warning",
            "checked_at": updated_at,
            "last_success_at": previous.get("last_success_at"),
            "error_type": type(exc).__name__,
            "detail": str(exc)[:300] or "獨立預判引擎發生未分類錯誤",
            "formal_pipeline_continues": True,
            "formal_v6_unchanged": True,
            "existing_ledgers_unchanged": True,
            "automatic_orders": False,
        }
        temporary = reports_dir / "prediction_engine_health.tmp"
        temporary.write_text(json.dumps(health, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(health_path)
        return False
    return True


def _update_central_controls_safely(reports_dir, *, updated_at: str) -> bool:
    """Refresh validation/graduation controls without touching formal outputs."""
    try:
        from model_graduation import update_model_graduation
        from validation_60d import update_validation_60d

        update_validation_60d(reports_dir, updated_at=updated_at)
        update_model_graduation(reports_dir, updated_at=updated_at)
    except Exception:  # noqa: BLE001 - downstream research layer is isolated
        logging.exception("中央驗證／畢業控制器失敗；正式排名與報表繼續")
        return False
    return True


def _update_validation_progress_monitor_safely(
    reports_dir,
    rows,
    *,
    period: str,
    updated_at: str,
    intraday: bool,
) -> dict:
    """Observe completed-session progress without affecting formal outputs."""
    try:
        from validation_progress_monitor import update_validation_progress_monitor

        return update_validation_progress_monitor(
            reports_dir,
            rows,
            period=period,
            updated_at=updated_at,
            intraday=intraday,
        )
    except Exception:  # noqa: BLE001 - monitoring must never stop a report
        logging.exception("60日驗證進度監控失敗；正式排名與報表繼續")
        return {}


def _update_model_learning_safely(reports_dir, *, updated_at: str) -> dict:
    """Turn frozen errors into research candidates without touching V6."""
    try:
        from model_learning import update_model_learning

        return update_model_learning(reports_dir, updated_at=updated_at)
    except Exception:  # noqa: BLE001 - learning research must never stop a report
        logging.exception("錯題學習／影子候選整理失敗；正式V6與報表繼續")
        return {}


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


def _freeze_tw_prices_until_close(period: str, intraday: bool = False) -> bool:
    """Never request or publish Taiwan prices during its regular session."""
    return intraday or period == "noon"


def _tw_watchlist_enrichment_ids(
    watchlist: list[dict],
) -> tuple[set[str], set[str]]:
    """Separate Taiwan market-flow symbols from company-financial symbols.

    ETFs can have institution, credit and broker-flow records, but they do not
    publish company income statements.  Counting them in the financial-quality
    denominator creates a permanent false missing-data warning.
    """
    market_ids = {
        str(item.get("symbol") or "").split(".")[0]
        for item in watchlist
        if item.get("market") == "TW" and item.get("symbol")
    }
    company_ids = {
        str(item.get("symbol") or "").split(".")[0]
        for item in watchlist
        if item.get("market") == "TW"
        and item.get("symbol")
        and "ETF" not in str(item.get("type") or "").upper()
    }
    return market_ids, company_ids


def _is_tw_price_symbol(symbol: str) -> bool:
    return str(symbol).upper().endswith((".TW", ".TWO"))


def _carry_completed_tw_rows(
    universe: list[dict], previous: dict[str, dict],
) -> list[dict]:
    """Reuse the latest verified Taiwan close while the market is still open."""
    output: list[dict] = []
    for item in universe:
        if item.get("market") != "TW":
            continue
        symbol = str(item.get("symbol") or "")
        prior = previous.get(symbol)
        if not prior or not prior.get("official_session_date"):
            continue
        row = dict(prior)
        row["after_close_price_policy"] = "carried_completed_session"
        row["price_fetch_deferred_until_close"] = True
        output.append(row)
    return output


def _cohort_name(row: dict) -> str:
    market = str(row.get("market") or "").upper()
    asset = "ETF" if "ETF" in str(row.get("type") or "").upper() else "STOCK"
    return f"{market}_{asset}" if market in {"TW", "US"} else ""


def _report_session_issues(
    current_rows: list[dict],
    previous_payload: dict,
    *,
    minimum_dominant_coverage: float = 0.9,
) -> list[str]:
    """Reject mixed or regressed market sessions before any formal state write."""
    previous_rows = (
        previous_payload.get("data", []) if isinstance(previous_payload, dict) else []
    )
    previous_by_symbol = {
        str(row.get("symbol") or ""): row
        for row in previous_rows
        if isinstance(row, dict) and row.get("symbol")
    }
    issues: list[str] = []

    current_cohorts: dict[str, list[dict]] = {}
    previous_cohorts: dict[str, list[dict]] = {}
    for row in current_rows:
        cohort = _cohort_name(row)
        if cohort:
            current_cohorts.setdefault(cohort, []).append(row)
    for row in previous_rows:
        if not isinstance(row, dict):
            continue
        cohort = _cohort_name(row)
        if cohort:
            previous_cohorts.setdefault(cohort, []).append(row)

    for cohort, rows in current_cohorts.items():
        sessions = [
            str(row.get("official_session_date") or "")
            for row in rows
            if row.get("official_session_date")
        ]
        if not sessions:
            issues.append(f"{cohort} 全部缺少 official_session_date")
            continue
        dominant_session, dominant_count = Counter(sessions).most_common(1)[0]
        coverage = dominant_count / len(rows)
        if coverage < minimum_dominant_coverage:
            issues.append(
                f"{cohort} 交易日混雜：主交易日 {dominant_session} "
                f"僅 {dominant_count}/{len(rows)}（{coverage:.1%}）"
            )

        previous_sessions = [
            str(row.get("official_session_date") or "")
            for row in previous_cohorts.get(cohort, [])
            if row.get("official_session_date")
        ]
        if previous_sessions:
            previous_dominant = Counter(previous_sessions).most_common(1)[0][0]
            if dominant_session < previous_dominant:
                issues.append(
                    f"{cohort} 主交易日倒退：{previous_dominant} -> {dominant_session}"
                )

    regressions: list[str] = []
    for row in current_rows:
        symbol = str(row.get("symbol") or "")
        current_session = str(row.get("official_session_date") or "")
        previous_session = str(
            previous_by_symbol.get(symbol, {}).get("official_session_date") or ""
        )
        if symbol and current_session and previous_session and current_session < previous_session:
            regressions.append(f"{symbol} {previous_session}->{current_session}")
    if regressions:
        preview = "、".join(regressions[:8])
        suffix = f" 等 {len(regressions)} 檔" if len(regressions) > 8 else ""
        issues.append(f"個股交易日倒退：{preview}{suffix}")
    return issues


def _carry_forward_symbol_session_regressions(
    current_rows: list[dict],
    previous_payload: dict,
    *,
    minimum_dominant_coverage: float = 0.9,
    allow_closed_cohort_regression: bool = False,
) -> tuple[list[dict], list[str]]:
    """Reuse the last verified row when one source briefly moves backwards.

    A broad cohort regression or a mixed-session batch must still fail the
    normal report guard.  Carry-forward is allowed only when the current
    cohort has a coherent, non-regressed dominant session and the previous
    symbol row already belongs to that same dominant session.
    """
    previous_rows = (
        previous_payload.get("data", []) if isinstance(previous_payload, dict) else []
    )
    previous_by_symbol = {
        str(row.get("symbol") or ""): row
        for row in previous_rows
        if isinstance(row, dict) and row.get("symbol")
    }
    current_cohorts: dict[str, list[dict]] = {}
    previous_cohorts: dict[str, list[dict]] = {}
    for row in current_rows:
        cohort = _cohort_name(row)
        if cohort:
            current_cohorts.setdefault(cohort, []).append(row)
    for row in previous_rows:
        if not isinstance(row, dict):
            continue
        cohort = _cohort_name(row)
        if cohort:
            previous_cohorts.setdefault(cohort, []).append(row)

    safe_dominant_sessions: dict[str, str] = {}
    closed_cohort_regressions: set[str] = set()
    for cohort, rows in current_cohorts.items():
        sessions = [
            str(row.get("official_session_date") or "")
            for row in rows
            if row.get("official_session_date")
        ]
        previous_sessions = [
            str(row.get("official_session_date") or "")
            for row in previous_cohorts.get(cohort, [])
            if row.get("official_session_date")
        ]
        if not sessions or not previous_sessions:
            continue
        dominant_session, dominant_count = Counter(sessions).most_common(1)[0]
        previous_dominant, previous_count = Counter(previous_sessions).most_common(1)[0]
        current_coherent = dominant_count / len(rows) >= minimum_dominant_coverage
        previous_coherent = (
            previous_count / len(previous_cohorts.get(cohort, []))
            >= minimum_dominant_coverage
        )
        if (
            current_coherent
            and dominant_session >= previous_dominant
        ):
            safe_dominant_sessions[cohort] = dominant_session
        elif (
            allow_closed_cohort_regression
            and cohort.startswith("TW_")
            and current_coherent
            and previous_coherent
            and dominant_session < previous_dominant
        ):
            # At a morning/weekend refresh there is no newer TW session to
            # discover.  If an upstream source temporarily falls back several
            # days, retain the already verified closed-session cohort instead
            # of replacing it with older prices.
            safe_dominant_sessions[cohort] = previous_dominant
            closed_cohort_regressions.add(cohort)

    repaired: list[dict] = []
    carried_symbols: list[str] = []
    for row in current_rows:
        symbol = str(row.get("symbol") or "")
        current_session = str(row.get("official_session_date") or "")
        previous_row = previous_by_symbol.get(symbol)
        previous_session = str(
            (previous_row or {}).get("official_session_date") or ""
        )
        dominant_session = safe_dominant_sessions.get(_cohort_name(row), "")
        if (
            previous_row
            and current_session
            and previous_session
            and current_session < previous_session
            and previous_session == dominant_session
        ):
            replacement = dict(previous_row)
            cohort = _cohort_name(row)
            replacement.update({
                "session_carry_forward": True,
                "session_carry_forward_reason": (
                    "closed_period_cohort_source_regression"
                    if cohort in closed_cohort_regressions
                    else "source_session_regression"
                ),
                "session_carry_forward_observed_date": current_session,
            })
            repaired.append(replacement)
            carried_symbols.append(symbol)
        else:
            repaired.append(row)
    return repaired, carried_symbols


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
        institution_snapshot = {
            key: value for key, value in row.items()
            if key == "available" or key.startswith(("foreign", "trust", "dealer", "institution_"))
        }
        # Serialized analysis rows use *_net and institution_available while
        # build_features consumes the provider-shaped keys. Reconstruct that
        # contract explicitly so a morning/noon refresh cannot silently turn
        # yesterday's verified institutional snapshot into missing/zero data.
        institution_snapshot.update({
            "available": 1.0 if row.get(
                "institution_available", row.get("available")
            ) else 0.0,
            "foreign": row.get("foreign_net", row.get("foreign")),
            "trust": row.get("trust_net", row.get("trust")),
            "dealer": row.get("dealer_net", row.get("dealer")),
        })
        institutions[sid] = institution_snapshot
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


def _institution_coverage_status(
    universe: list[dict],
    institutions: dict[str, dict],
    official_status: dict | None = None,
) -> dict:
    """Build a completed-session coverage gate for rankings and AI inputs."""
    tw_ids = {
        str(item.get("symbol") or "").split(".")[0]
        for item in universe
        if item.get("market") == "TW"
    }
    dated = [
        item for sid, item in institutions.items()
        if sid in tw_ids and item.get("available") and item.get("institution_date")
    ]
    date_counts = Counter(str(item.get("institution_date")) for item in dated)
    session_date = date_counts.most_common(1)[0][0] if date_counts else None
    covered = sum(
        1 for item in dated if str(item.get("institution_date")) == session_date
    )
    expected = len(tw_ids)
    coverage = covered / expected * 100 if expected else 0.0
    minimum = float((official_status or {}).get("minimum_coverage_pct") or 95.0)
    eligible = bool(session_date and coverage >= minimum)
    if eligible:
        reason = "verified_completed_session_snapshot"
    elif not session_date:
        reason = "institution_source_unavailable"
    else:
        reason = "institution_coverage_insufficient"
    return {
        "available": eligible,
        "ranking_eligible": eligible,
        "ai_eligible": eligible,
        "reason": reason,
        "session_date": session_date,
        "expected_count": expected,
        "returned_count": covered,
        "coverage_pct": round(coverage, 1),
        "minimum_coverage_pct": minimum,
        "official": dict(official_status or {}),
    }


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


def _simulation_input_rows(
    ranking_rows: list[dict], all_analysis_rows: list[dict]
) -> list[dict]:
    """Keep TOP order while exposing price rows for older frozen picks.

    A forward experiment can need a symbol frozen yesterday that has since
    moved outside today's TOP20. Passing only the current ranking makes that
    valid price look missing even though it exists in the full analysis.
    """
    output = list(ranking_rows)
    seen = {
        (str(row.get("market") or ""), str(row.get("symbol") or ""))
        for row in output
    }
    for row in all_analysis_rows:
        key = (str(row.get("market") or ""), str(row.get("symbol") or ""))
        if key in seen:
            continue
        seen.add(key)
        output.append(row)
    return output


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
    previous_rows = _previous_rows()
    for symbol, forecast in load_frozen_forecasts(
        SETTINGS.reports_dir, market="TW"
    ).items():
        previous_rows[symbol] = {**previous_rows.get(symbol, {}), **forecast}
    freeze_tw_prices = _freeze_tw_prices_until_close(args.period, args.intraday)

    # The inverse ETFs are price inputs for one isolated shadow experiment.
    # They deliberately never enter `combined`, `universe`, ALL or ranking.
    inverse_watchlist = load_inverse_experiment_watchlist()
    inverse_symbols = [item["symbol"] for item in inverse_watchlist]
    inverse_etf_catalog = load_inverse_etf_catalog()
    leveraged_inverse_symbols = [item["symbol"] for item in inverse_etf_catalog["products"]]
    data_symbols = list(dict.fromkeys([*symbols, *inverse_symbols, *leveraged_inverse_symbols]))

    us_history_cohorts = {
        str(item["symbol"]).upper(): (
            "US_ETF"
            if "ETF" in str(item.get("type", "")).upper()
            else "US_STOCK"
        )
        for item in universe
        if item.get("market") == "US"
    }
    us_history_cohorts.update({
        symbol: "US_ETF"
        for symbol in leveraged_inverse_symbols
        if not symbol.endswith((".TW", ".TWO"))
    })
    price_data_symbols = [
        symbol for symbol in data_symbols
        if not (freeze_tw_prices and _is_tw_price_symbol(symbol))
    ]
    history = _stage(
        "日線價格",
        lambda: download_history(
            price_data_symbols, us_cohorts=us_history_cohorts
        ),
    )
    intraday_symbols = [
        symbol for symbol in symbols
        if not (freeze_tw_prices and _is_tw_price_symbol(symbol))
    ]
    intraday = _stage("盤中量價", lambda: download_intraday(intraday_symbols))
    watchlist_stock_ids, watchlist_company_ids = _tw_watchlist_enrichment_ids(
        watchlist
    )
    all_tw_stock_ids = {
        item["symbol"].split(".")[0]
        for item in universe
        if item.get("market") == "TW"
        and "ETF" not in str(item.get("type", "")).upper()
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
    # StockQ covers only a limited popular-US-stock list and does not provide
    # complete OHLCV.  Keep it outside feature construction and formal ranking;
    # its completed-session close may later repair an existing shadow holding.
    stockq_us_close_fallback = _stage(
        "StockQ美股收盤價備援層",
        lambda: update_stockq_us_close_fallback(
            SETTINGS.reports_dir,
            updated_at=now.strftime("%Y-%m-%d %H:%M:%S"),
            requested_symbols=us_symbols,
            timeout=SETTINGS.request_timeout,
        ),
    )
    # Tiingo's free individual tier is quota-bounded and private/internal-use
    # only. It rotates through at most 47 symbols per report and promotes only
    # a completed collection cycle. The rows never enter feature construction.
    tiingo_us_close_fallback = _stage(
        "Tiingo免費美股收盤價分批備援層",
        lambda: update_tiingo_us_close_fallback(
            SETTINGS.reports_dir,
            updated_at=now.strftime("%Y-%m-%d %H:%M:%S"),
            requested_symbols=us_symbols,
            timeout=SETTINGS.request_timeout,
        ),
    )
    # Free FinMind plans allow per-stock requests, so enrichment targets the
    # fixed list while the wider background scan safely remains neutral.
    previous = previous_rows if freeze_tw_prices else {}
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
    tw_official = (
        {
            "prices": {}, "institutions": {}, "credit": {},
            "fundamentals": {}, "announcements": {},
        }
        if freeze_tw_prices
        else _stage("台股官方資料", lambda: fetch_taiwan_official_data(universe))
    )
    institutions = merge_official_with_fallback(
        institutions, tw_official.get("institutions", {}), kind="institution"
    )
    institution_status = _institution_coverage_status(
        universe, institutions, tw_official.get("institution_status")
    )
    institutions_for_scoring = (
        institutions if institution_status.get("ai_eligible") else {}
    )
    if institutions and not institution_status.get("ai_eligible"):
        logging.warning(
            "台股法人覆蓋不足（%s/%s）；本次不產出法人排行且不送入 AI 評分",
            institution_status.get("returned_count"),
            institution_status.get("expected_count"),
        )
    credit_flows = merge_official_with_fallback(
        credit_flows, tw_official.get("credit", {}), kind="credit"
    )
    fundamentals = merge_official_with_fallback(
        fundamentals, tw_official.get("fundamentals", {}), kind="fundamental"
    )
    financial_quality = _stage(
        "財務品質快取", lambda: fetch_financial_quality(watchlist_company_ids)
    )
    # Populate a separate, quota-bounded cache for the full Taiwan equity
    # universe.  These rows are never merged into production features: only a
    # copied input reaches the isolated valuation radar below.
    valuation_tw_financial = _stage(
        "估值影子財報快取",
        lambda: fetch_financial_quality(all_tw_stock_ids, batch_size=30),
    )
    official_tw_financial = _stage(
        "台股官方季報",
        lambda: fetch_tw_official_financials(
            all_tw_stock_ids,
            cache_path=SETTINGS.reports_dir / "tw_financial_official_cache.json",
            timeout=SETTINGS.request_timeout,
        ),
    )
    # Official income/balance statements fill the full listed/OTC universe.
    # FinMind cash-flow fields remain useful where licensed and are preserved;
    # official overlapping fields take precedence and keep their provenance.
    for stock_id, official_row in official_tw_financial.items():
        valuation_tw_financial[stock_id] = {
            **valuation_tw_financial.get(stock_id, {}), **official_row,
        }
        if stock_id in watchlist_company_ids:
            financial_quality[stock_id] = {
                **financial_quality.get(stock_id, {}), **official_row,
            }
    us_short_volume = _stage("美股放空量", lambda: fetch_us_short_volume(us_symbols))
    us_company_metadata = _stage(
        "美股公司資料快取", lambda: fetch_us_company_metadata(universe)
    )
    etf_metadata = _stage("ETF 資料快取", lambda: fetch_etf_metadata(universe))

    features = _carry_completed_tw_rows(universe, previous_rows) if freeze_tw_prices else []
    for item in universe:
        symbol = item["symbol"]
        if freeze_tw_prices and item.get("market") == "TW":
            continue
        daily = history.get(symbol)
        official_price = None
        if item.get("market") == "TW":
            official_price = tw_official.get("prices", {}).get(symbol.split(".")[0])
            daily = overlay_official_daily(daily, official_price)
        if daily is None:
            continue
        stock_id = symbol.split(".")[0]
        institution = (
            institutions_for_scoring.get(stock_id)
            if item.get("market") == "TW" else None
        )
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

    try:
        previous_analysis = json.loads(
            (SETTINGS.reports_dir / "all_analysis.json").read_text(encoding="utf-8")
        )
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        previous_analysis = {}
    features, carried_symbols = _carry_forward_symbol_session_regressions(
        features,
        previous_analysis,
        allow_closed_cohort_regression=(
            args.period == "morning" or now.weekday() >= 5
        ),
    )
    if carried_symbols:
        print(
            "[行情交易日] 來源交易日倒退，沿用上一版已驗證資料："
            + "、".join(carried_symbols)
        )
    source_session_issues = _report_session_issues(features, previous_analysis)
    if source_session_issues:
        raise RuntimeError(
            "行情交易日完整性檢查失敗；禁止更新任何正式狀態："
            + "；".join(source_session_issues)
        )

    market = _stage(
        "核心市場",
        lambda: fetch_core_market(
            {"加權指數", "櫃買指數"} if freeze_tw_prices else None
        ),
    )
    # StockQ is an after-close secondary fallback for the market indicators it
    # publishes. It never overwrites complete primary values or individual rows.
    stockq_market_context = _stage(
        "StockQ收盤後市場備援層",
        lambda: update_stockq_market_context(
            SETTINGS.reports_dir,
            updated_at=now.strftime("%Y-%m-%d %H:%M:%S"),
            timeout=SETTINGS.request_timeout,
            allow_network=not freeze_tw_prices,
        ),
    )
    market = apply_stockq_market_fallback(market, stockq_market_context)
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
    market_regime_history = _stage("多空盤整歷史", fetch_market_regime_history)
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
    # First pass identifies the symbols that require every-run refresh.  The
    # news layer now covers the full universe and uses an attributable cache;
    # priority rows remain fresh without dropping lower-ranked stocks.
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
        "全市場新聞",
        lambda: fetch_news_risks(
            preliminary,
            workers=12,
            cache_path=SETTINGS.reports_dir / "news_risk_cache.json",
            priority_symbols=set(news_targets_by_symbol),
            max_background_refresh=100,
        ),
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
    ranked = strategy.apply_tw_buy_candidate_ranking(ranked)
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
    session_issues = _report_session_issues(
        [row for rows in backtest_groups.values() for row in rows],
        previous_analysis,
    )
    if session_issues:
        raise RuntimeError(
            "報表交易日完整性檢查失敗；禁止覆寫正式報表：" + "；".join(session_issues)
        )
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
            market_regimes=market_regime_history,
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
        "stockq_market_context": stockq_market_context,
        "stockq_us_close_fallback": stockq_us_close_fallback,
        # Never serialize Tiingo's internal-use raw rows into GitHub Pages.
        # Only non-price coverage diagnostics may leave the private CI cache.
        "tiingo_us_close_fallback": _tiingo_public_summary(
            tiingo_us_close_fallback
        ),
        "macro_regime": macro_regime,
        "tw_market_context": tw_market_context,
        "data_status": {
            "finmind_configured": bool(SETTINGS.finmind_token),
            "institutional_count": len(institutions),
            "credit_count": len(credit_flows),
            "fundamental_count": len(fundamentals),
            "tw_official_price_count": len(tw_official.get("prices", {})),
            "tw_official_institution_count": len(tw_official.get("institutions", {})),
            "tw_institution_status": institution_status,
            "tw_official_credit_count": len(tw_official.get("credit", {})),
            "tw_official_fundamental_count": len(tw_official.get("fundamentals", {})),
            "tw_official_announcement_count": len(tw_official.get("announcements", {})),
            "financial_quality_count": len(financial_quality),
            "expected_financial_quality_count": len(watchlist_company_ids),
            "financial_quality_not_applicable_count": len(
                watchlist_stock_ids - watchlist_company_ids
            ),
            "tw_official_financial_count": len(official_tw_financial),
            "broker_count": sum(
                1 for item in broker_branches.values()
                if item.get("broker_available") and item.get("broker_date")
            ),
            "broker_source": "FinMind Sponsor broker-branch dataset",
            "broker_optional": True,
            "us_short_volume_count": len(us_short_volume),
            "us_extended_hours_count": len(us_extended_hours),
            "us_sip_count": len(us_live),
            "us_opra_count": len(us_options),
            "us_sec_company_count": sum(
                1 for item in us_company_metadata.values()
                if item.get("us_sec_data_available")
            ),
            "us_sec_fallback_count": sum(
                1 for item in us_company_metadata.values()
                if item.get("us_sec_fallback_used")
            ),
            "news_scanned_count": len(news_risks),
            "news_verified_risk_count": sum(
                1 for item in news_risks.values() if item.get("news_penalty", 0) > 0
            ),
            "stockq_status": stockq_market_context.get("status"),
            "stockq_indicator_count": stockq_market_context.get("indicator_count", 0),
            "stockq_us_close_status": stockq_us_close_fallback.get("status"),
            "stockq_us_close_count": stockq_us_close_fallback.get("symbol_count", 0),
            "stockq_us_close_covered_count": stockq_us_close_fallback.get(
                "covered_requested_count", 0
            ),
            "tiingo_us_close_configured": bool(
                tiingo_us_close_fallback.get("configured")
            ),
            "tiingo_us_close_status": tiingo_us_close_fallback.get("status"),
            "tiingo_us_close_count": tiingo_us_close_fallback.get("symbol_count", 0),
            "tiingo_us_close_covered_count": tiingo_us_close_fallback.get(
                "covered_requested_count", 0
            ),
            "tiingo_us_close_staging_count": tiingo_us_close_fallback.get(
                "staging_attempted_count", 0
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
            "overall_ranking": "台股採三階段：先依完整買進計畫、趨勢、量能、資料品質與風險產生候選排名；價格進入買進區且隔日方向確認後才成為觸發候選；真正可買仍須盤中富邦7/7確認。美股維持既有綜合風控排名",
            "short_term": "1至5個交易日；先依合格與安全條件分層。台股個股在法人蓄力資料完整時，以原短線模型80%＋法人蓄力20%計算排名；缺資料沿用原分數，美股與ETF不變",
            "next_session": "V6市場分流隔日影子預測；台股晚報、美股早報才以完整收盤資料固定預測。研究方向供向前驗證，強訊號另行標示；兩市場採不同權重且不影響原排名",
            "next_session_ranking_shadow": "下一交易日隔離主排名使用完整固定候選池，不先篩當日上漲股；十個隔日模型歸併為五個證據家族各計一次。當日漲幅不直接推高或壓低明日機率；若趨勢、量價與資金流獨立支持續漲仍可入榜，追高／落刀風險只調整明日可買性。不改正式V6、5日或60日紀錄",
            "mid_long_term": "3至12個月；先依合格與重大風險分層，再以財務品質、成長、估值、中期趨勢、法人籌碼及新聞風險排序",
            "etf": "台灣與美國ETF分開計分；使用流動性、折溢價、風險、成本、追蹤與組合品質，不套用個股財報模型",
            "missing_data": "缺少的維度不以中性50分補入排名；降低資料信心並依門檻限制資格",
            "market_isolation": "台股個股TW-STOCK-V5、台灣ETF TW-ETF-V5與美股US-V3使用獨立資料契約；台股採個股／同族群／台股市場分層校正，跨市場欄位會在計分前清除",
            "tw_layered_context": "台股個股以個股70%、同族群20%、台股市場10%計分；台灣ETF提高市場層比重。缺少背景資料時不以中性值冒充，且不影響US-V3",
            "extended_hours": "美股盤前／盤後僅作跳空與風險提示，不直接增加AI分數",
            "us_live_data": "美股以SIP全市場報價為主、OPRA選擇權為風險層；未設定授權時保留Yahoo/StockQ/FINRA備援，Tiingo免費版僅私密測試涵蓋率",
            "us_fundamental_fallback": "美股個股財務資料不足時以免費官方SEC EDGAR companyfacts補空值；不覆蓋既有值、ETF不適用、非美元申報不混入美元絕對金額",
            "macro_risk": "historical sessions are backfilled; adjustment is capped at +/-4 points",
            "after_close_prices": "自動報告盤中不抓、不補、不結算台股價格；中午報沿用上一個完整交易日，晚報收盤後才更新",
            "stockq_context": "StockQ作收盤後備援：全球市場頁只補缺少的市場指標；熱門美股頁只補已完成交易日收盤價。兩者皆不覆蓋完整主來源；個股備援不補開盤、最高、最低、成交量，不建立新持倉、不結算開收盤試驗、不重算正式排名",
            "tiingo_context": "Tiingo免費個人方案每次最多47檔、每日4次可完成186檔完整輪巡；完整快取只補本人版既有美股中長期持倉的缺失收盤估值，原始價格不進公開網站、五日結算、排名或朋友版",
            "verified_outcome_feedback": "V6 market-specific close-to-open, open-to-close, and close-to-close shadow outcomes; four market/asset cohorts; no automatic score effect",
            "regime_validation": "台股以加權指數、美股以S&P 500，只用預測當日以前的MA20、MA60與20日報酬固定多頭／空頭／盤整標籤；分段結果不自動改分",
            "institutional_accumulation": "台股法人蓄力分數＝成交量正規化法人強度35%＋連買20%＋K線穩定20%＋吸收15%＋量縮10%；資料完整時占台股個股短線排名20%，另設獨立法人蓄力榜並持續累積扣成本驗證",
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
    # Keep the public ranking payload compact, but let experiments see the
    # full price universe. Otherwise yesterday's frozen pick is falsely marked
    # missing as soon as it falls outside today's TOP20.
    simulation_rows = _simulation_input_rows(ranking_rows, ranked)
    required_us_shadow_symbols = load_us_shadow_symbols(SETTINGS.reports_dir)
    # Public shadow valuation remains Yahoo > StockQ. Tiingo is internal-use
    # only and is applied to a separate owner-only state below.
    holding_simulation_rows, stockq_close_applied = apply_stockq_us_close_to_simulation_rows(
        simulation_rows,
        stockq_us_close_fallback,
        universe,
        required_symbols=required_us_shadow_symbols,
    )
    if stockq_close_applied:
        logging.info(
            "StockQ僅補影子持倉收盤價（不影響排名／新進場）：%s",
            "、".join(stockq_close_applied),
        )
    update_million_simulation(
        SETTINGS.reports_dir,
        simulation_rows,
        period=args.period,
        updated_at=report["updated_at"],
        intraday=args.intraday,
        price_history=history,
    )
    update_weight_experiment(
        SETTINGS.reports_dir,
        simulation_rows,
        period=args.period,
        updated_at=report["updated_at"],
        intraday=args.intraday,
        price_history=history,
    )
    update_holding_simulation(
        SETTINGS.reports_dir,
        holding_simulation_rows,
        period=args.period,
        updated_at=report["updated_at"],
        intraday=args.intraday,
    )
    owner_private_rows, tiingo_close_applied = apply_tiingo_us_close_to_simulation_rows(
        simulation_rows,
        tiingo_us_close_fallback,
        universe,
        required_symbols=required_us_shadow_symbols,
    )
    owner_private_rows, owner_stockq_applied = apply_stockq_us_close_to_simulation_rows(
        owner_private_rows,
        stockq_us_close_fallback,
        universe,
        required_symbols=required_us_shadow_symbols,
    )
    if tiingo_close_applied:
        logging.info(
            "Tiingo僅補本人版既有中長期持倉收盤估值：%s",
            "、".join(tiingo_close_applied),
        )
    if owner_stockq_applied:
        logging.info(
            "本人版Tiingo仍缺價後由StockQ補影子持倉：%s",
            "、".join(owner_stockq_applied),
        )
    update_holding_simulation(
        SETTINGS.reports_dir,
        owner_private_rows,
        period=args.period,
        updated_at=report["updated_at"],
        intraday=args.intraday,
        filename="owner_private_holding_simulation.json",
        seed_filename="holding_simulation.json",
    )
    update_exit_horizon_experiment(
        SETTINGS.reports_dir,
        simulation_rows,
        period=args.period,
        updated_at=report["updated_at"],
        intraday=args.intraday,
    )
    inverse_session_dates = {
        market_name: next((
            str(row.get("official_session_date") or "")
            for row in ranking_rows
            if row.get("market") == market_name
            and "ETF" not in str(row.get("type") or "").upper()
            and row.get("official_session_date")
        ), "")
        for market_name in ("TW", "US")
    }
    inverse_market_rows = build_inverse_market_rows(
        inverse_watchlist, history, inverse_session_dates
    )
    update_inverse_experiment(
        SETTINGS.reports_dir,
        simulation_rows,
        inverse_market_rows,
        market_regime_history,
        period=args.period,
        updated_at=report["updated_at"],
        intraday=args.intraday,
        watchlist=inverse_watchlist,
    )
    inverse_etf_product_rows = build_inverse_etf_product_rows(
        inverse_etf_catalog, history, inverse_session_dates
    )
    # A second, fully isolated research ledger for sector/index inverse ETFs.
    # It writes only its own database/state files and cannot modify rankings.
    update_inverse_etf_shadow(
        SETTINGS.reports_dir,
        simulation_rows,
        inverse_etf_product_rows,
        period=args.period,
        updated_at=report["updated_at"],
        intraday=args.intraday,
        catalog=inverse_etf_catalog,
        histories=history,
    )
    # Research-only forward audit.  It reads the already-final ranking but can
    # neither change that ranking nor use the same session as its outcome.
    update_missed_strength_validation(
        SETTINGS.reports_dir,
        ranked,
        market_regime_history,
        period=args.period,
        updated_at=report["updated_at"],
        intraday=args.intraday,
    )
    # Research-only market-rule and sector-rotation A/B.  It reads the frozen
    # V6 order but cannot write scores, ranks or broker instructions.
    closed_daily_flow = _fetch_closed_daily_flow(intraday=args.intraday)
    if closed_daily_flow:
        _persist_closed_daily_flow(SETTINGS.reports_dir, closed_daily_flow)
    _update_market_rotation_shadow_safely(
        SETTINGS.reports_dir,
        simulation_rows,
        period=args.period,
        updated_at=report["updated_at"],
        intraday=args.intraday,
        daily_flow=closed_daily_flow,
    )
    valuation_rows = []
    for source_row in ranked:
        valuation_row = dict(source_row)
        if (
            valuation_row.get("market") == "TW"
            and "ETF" not in str(valuation_row.get("type", "")).upper()
        ):
            valuation_row.update(
                valuation_tw_financial.get(
                    str(valuation_row.get("symbol") or "").split(".")[0], {}
                )
            )
        valuation_rows.append(valuation_row)
    _update_valuation_risk_shadow_safely(
        SETTINGS.reports_dir,
        valuation_rows,
        period=args.period,
        updated_at=report["updated_at"],
        intraday=args.intraday,
    )
    _update_central_controls_safely(
        SETTINGS.reports_dir,
        updated_at=report["updated_at"],
    )
    _update_model_learning_safely(
        SETTINGS.reports_dir,
        updated_at=report["updated_at"],
    )
    validation_monitor = _update_validation_progress_monitor_safely(
        SETTINGS.reports_dir,
        simulation_rows,
        period=args.period,
        updated_at=report["updated_at"],
        intraday=args.intraday,
    )
    # The new multi-horizon engine owns a separate private SQLite database.
    # Central AI may read only its compact result contract after this succeeds.
    _update_prediction_engine_safely(
        SETTINGS.reports_dir,
        ranked,
        period=args.period,
        updated_at=report["updated_at"],
        intraday=args.intraday,
    )
    # The owner-facing decision layer consumes only already-final outputs. It
    # can explain conflicts, but cannot modify ranks, weights, or orders.
    _update_decision_hub_safely(
        SETTINGS.reports_dir,
        ranked,
        period=args.period,
        updated_at=report["updated_at"],
        intraday=args.intraday,
        institution_status=institution_status,
    )
    ranking_payload = {
        "updated_at": report["updated_at"],
        "period": args.period,
        "run_mode": report["run_mode"],
        "ranking_basis": "TW_three_stage_setup_trigger_live_confirmation; US_existing_overall_ranking",
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
        "institution_status": institution_status,
        "data": ranked,
    }
    all_analysis_path = SETTINGS.reports_dir / "all_analysis.json"
    all_analysis_tmp = SETTINGS.reports_dir / "all_analysis.tmp"
    all_analysis_tmp.write_text(
        json.dumps(all_analysis_payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    all_analysis_tmp.replace(all_analysis_path)

    pending_notices = [
        item
        for item in validation_monitor.get("pending_notifications", [])
        if isinstance(item, dict) and item.get("message")
    ]
    if pending_notices:
        markdown = f"{markdown}\n\n" + "\n".join(
            str(item["message"]) for item in pending_notices
        )
        # Keep the saved human-readable report identical to the Telegram copy.
        latest_md.write_text(markdown, encoding="utf-8")
    delivered = False if args.no_telegram else send_telegram(markdown)
    if delivered and pending_notices:
        try:
            from validation_progress_monitor import acknowledge_notifications

            acknowledge_notifications(
                SETTINGS.reports_dir,
                [str(item.get("id") or "") for item in pending_notices],
                delivered_at=report["updated_at"],
            )
        except Exception:  # noqa: BLE001 - delivery already succeeded
            logging.exception("60日驗證通知已送達，但確認狀態寫入失敗")
    print(markdown)
    print(f"\nSaved: {latest_json}, {latest_md}; Telegram={delivered}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
