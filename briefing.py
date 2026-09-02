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