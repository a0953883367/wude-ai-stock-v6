"""Five-session capital-flow weight experiment, isolated from production ranks.

The experiment consumes only already-detected large buy/sell alerts and the
independent 15-minute flow ledger.  It reads the frozen V6 report but never
writes to it.  Taiwan and US books, signals and outcomes are always separate.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import threading
import time
from typing import Any, Callable
from zoneinfo import ZoneInfo


VERSION = 1
MARKETS = ("TW", "US")
MARKET_ZONE = {"TW": ZoneInfo("Asia/Taipei"), "US": ZoneInfo("America/New_York")}
CLOSED_PERIOD = {"TW": "evening", "US": "morning"}
PICKS = 10
CAPITAL = 1_000_000.0
ROUND_TRIP_COST_PCT = {"TW": 0.685, "US": 0.20}
MAX_POINT_ADJUSTMENT = 3.0
MAX_SESSIONS = 90
MIN_REVIEW_DAYS = 60
MIN_REVIEW_SIGNALS = 200


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _number(value: Any, fallback: float = 0.0) -> float:
    number = _finite(value)
    return number if number is not None else fallback


def _is_stock(row: dict[str, Any], market: str) -> bool:
    return (
        str(row.get("market") or "").upper() == market
        and "ETF" not in str(row.get("type") or "").upper()
    )


def _session_date(epoch: float, market: str) -> str:
    return datetime.fromtimestamp(epoch, MARKET_ZONE[market]).date().isoformat()


def _rank_tier(row: dict[str, Any]) -> int:
    if row.get("trade_guard_blocked") or row.get("market_contract_valid") is False:
        return 0
    explicit = _finite(row.get("short_term_rank_tier"))
    if explicit is not None:
        return max(0, min(2, int(explicit)))
    return 2 if row.get("short_term_eligible") is True else 1


def _base_score(row: dict[str, Any]) -> float:
    return _number(row.get("short_term_ranking_score"), _number(row.get("short_term_score")))


class FlowWeightShadow:
    """Persistent, forward-only A/B book for a capped short-term overlay."""

    def __init__(
        self,
        report_path: Path,
        state_path: Path,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.report_path = report_path
        self.state_path = state_path
        self.clock = clock
        self._lock = threading.RLock()
        self._report_mtime: float | None = None
        self._report: dict[str, Any] = {}
        self._rows: list[dict[str, Any]] = []
        self._state = self._load_state()
        self._refresh_report(force=True)

    def _empty_market(self, market: str) -> dict[str, Any]:
        return {
            "market": market,
            "signals": {},
            "sessions": [],
            "outcomes": [],
        }

    def _load_state(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            payload = {}
        markets = payload.get("markets") if isinstance(payload, dict) else None
        state = {
            "version": VERSION,
            "mode": "five_day_flow_weight_shadow",
            "created_at": payload.get("created_at") if isinstance(payload, dict) else None,
            "markets": {},
        }
        state["created_at"] = state["created_at"] or datetime.now(timezone.utc).isoformat(timespec="seconds")
        for market in MARKETS:
            saved = markets.get(market) if isinstance(markets, dict) else None
            clean = self._empty_market(market)
            if isinstance(saved, dict):
                clean["signals"] = saved.get("signals") if isinstance(saved.get("signals"), dict) else {}
                clean["sessions"] = saved.get("sessions") if isinstance(saved.get("sessions"), list) else []
                clean["outcomes"] = saved.get("outcomes") if isinstance(saved.get("outcomes"), list) else []
            state["markets"][market] = clean
        return state

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(self._state, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(self.state_path)

    def _refresh_report(self, *, force: bool = False) -> None:
        try:
            mtime = self.report_path.stat().st_mtime
        except OSError:
            return
        if not force and self._report_mtime == mtime:
            return
        try:
            payload = json.loads(self.report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return
        self._report = payload
        self._rows = [dict(row) for row in rows if isinstance(row, dict)]
        self._report_mtime = mtime

    def record_alert(self, alert: dict[str, Any]) -> None:
        market = str(alert.get("market") or "").upper()
        symbol = str(alert.get("symbol") or "").upper()
        epoch = _finite(alert.get("detected_at_epoch"))
        if market not in MARKETS or not symbol or epoch is None:
            return
        side = -1 if alert.get("alert_side") == "sell" else 1
        points = 2 if alert.get("trigger_type") == "cluster" else 1
        date = _session_date(epoch, market)
        with self._lock:
            signals = self._state["markets"][market]["signals"].setdefault(date, {})
            row = signals.setdefault(symbol, {
                "symbol": symbol,
                "name": str(alert.get("name") or symbol),
                "raw_alert_points": 0,
                "alert_count": 0,
                "buy_alerts": 0,
                "sell_alerts": 0,
                "last_price": None,
                "last_alert_at": None,
            })
            row["raw_alert_points"] = int(row.get("raw_alert_points") or 0) + side * points
            row["alert_count"] = int(row.get("alert_count") or 0) + 1
            row["buy_alerts" if side > 0 else "sell_alerts"] = int(
                row.get("buy_alerts" if side > 0 else "sell_alerts") or 0
            ) + 1
            row["last_price"] = _finite(alert.get("price"))
            row["last_alert_at"] = alert.get("detected_at")
            self._save()

    def _official_date(self, market: str) -> str:
        dates = [
            str(row.get("official_session_date") or "")
            for row in self._rows if _is_stock(row, market) and row.get("official_session_date")
        ]
        return Counter(dates).most_common(1)[0][0] if dates else ""

    def _flow_map(self, flow: dict[str, Any], market: str) -> dict[str, dict[str, Any]]:
        window = (((flow.get("markets") or {}).get(market) or {}).get("windows") or {}).get("15m") or {}
        rows = list(window.get("top_inflows") or []) + list(window.get("top_outflows") or [])
        return {str(row.get("symbol") or "").upper(): row for row in rows if row.get("symbol")}

    def _points(self, signal: dict[str, Any], flow_row: dict[str, Any] | None) -> tuple[float, list[str]]:
        raw = int(signal.get("raw_alert_points") or 0)
        alert_points = max(-2, min(2, raw))
        reasons = [f"大量成交警示 {alert_points:+d}"] if alert_points else []
        persistence = 0
        if flow_row and _number(flow_row.get("confidence")) >= 60:
            net = _number(flow_row.get("net_flow"))
            price_change = _number(flow_row.get("price_change_pct"))
            if net > 0 and price_change > 0:
                persistence = 1
            elif net < 0 and price_change < 0:
                persistence = -1
            if persistence:
                reasons.append(f"15分鐘持續且價格確認 {persistence:+d}")
        total = max(-MAX_POINT_ADJUSTMENT, min(MAX_POINT_ADJUSTMENT, alert_points + persistence))
        return total, reasons

    def _select(self, market: str, point_map: dict[str, tuple[float, list[str]]], *, adjusted: bool) -> list[dict[str, Any]]:
        ranked = []
        for source in self._rows:
            if not _is_stock(source, market):
                continue
            row = dict(source)
            symbol = str(row.get("symbol") or "").upper()
            points, reasons = point_map.get(symbol, (0.0, []))
            base = _base_score(row)
            row["_base"] = base
            row["_points"] = points
            row["_reasons"] = reasons
            row["_shadow"] = base + points if adjusted else base
            ranked.append(row)
        ranked.sort(key=lambda row: (
            -_rank_tier(row), -_number(row.get("_shadow")),
            -_number(row.get("short_term_score")), str(row.get("symbol") or ""),
        ))
        return [{
            "rank": index,
            "symbol": str(row.get("symbol") or ""),
            "name": str(row.get("name") or row.get("symbol") or ""),
            "base_score": round(_number(row.get("_base")), 2),
            "flow_adjustment_points": round(_number(row.get("_points")), 2) if adjusted else 0.0,
            "shadow_score": round(_number(row.get("_shadow")), 2),
            "reasons": row.get("_reasons") if adjusted else [],
            "signal_close_price": _finite(row.get("official_close_price")),
        } for index, row in enumerate(ranked[:PICKS], 1)]

    def _point_map(self, market: str, date: str, flow: dict[str, Any]) -> dict[str, tuple[float, list[str]]]:
        signals = self._state["markets"][market]["signals"].get(date) or {}
        flow_map = self._flow_map(flow, market)
        return {
            symbol: self._points(signal, flow_map.get(symbol))
            for symbol, signal in signals.items()
        }

    def _freeze(self, market: str, date: str, flow: dict[str, Any]) -> bool:
        market_state = self._state["markets"][market]
        if any(item.get("signal_session_date") == date for item in market_state["sessions"]):
            return False
        signals = market_state["signals"].get(date) or {}
        if not signals:
            return False
        point_map = self._point_map(market, date, flow)
        session = {
            "signal_session_date": date,
            "frozen_at": self._report.get("updated_at"),
            "status": "waiting_next_completed_session",
            "valid_signal_count": sum(int(row.get("alert_count") or 0) for row in signals.values()),
            "models": {
                "baseline": {"label": "A｜正式V6基準", "picks": self._select(market, point_map, adjusted=False)},
                "flow_shadow": {"label": "B｜資金流影子±3點", "picks": self._select(market, point_map, adjusted=True)},
            },
        }
        market_state["sessions"].append(session)
        market_state["sessions"] = market_state["sessions"][-MAX_SESSIONS:]
        return True

    def _settle(self, market: str, official_date: str) -> bool:
        market_state = self._state["markets"][market]
        current = {
            str(row.get("symbol") or "").upper(): row
            for row in self._rows
            if _is_stock(row, market) and str(row.get("official_session_date") or "") == official_date
        }
        changed = False
        for session in market_state["sessions"]:
            signal_date = str(session.get("signal_session_date") or "")
            if session.get("status") != "waiting_next_completed_session" or not official_date or official_date <= signal_date:
                continue
            outcome = {
                "signal_session_date": signal_date,
                "outcome_session_date": official_date,
                "status": "valid",
                "models": {},
            }
            for key, model in (session.get("models") or {}).items():
                positions = []
                for pick in model.get("picks") or []:
                    row = current.get(str(pick.get("symbol") or "").upper())
                    entry = _finite((row or {}).get("official_open_price"))
                    close = _finite((row or {}).get("official_close_price"))
                    if entry is None or close is None or entry <= 0:
                        positions.append({"symbol": pick.get("symbol"), "status": "quarantined_missing_price"})
                        continue
                    gross = (close / entry - 1) * 100
                    net = gross - ROUND_TRIP_COST_PCT[market]
                    positions.append({
                        "symbol": pick.get("symbol"), "status": "valid",
                        "entry_open_price": entry, "exit_close_price": close,
                        "gross_return_pct": round(gross, 4), "net_return_pct": round(net, 4),
                    })
                valid = [row for row in positions if row.get("status") == "valid"]
                if not valid:
                    outcome["status"] = "quarantined_missing_price"
                net_return = sum(_number(row.get("net_return_pct")) for row in valid) / len(valid) if valid else None
                outcome["models"][key] = {
                    "positions": positions,
                    "valid_positions": len(valid),
                    "net_return_pct": round(net_return, 4) if net_return is not None else None,
                    "net_profit": round(CAPITAL * net_return / 100, 2) if net_return is not None else None,
                }
            session["status"] = "settled" if outcome["status"] == "valid" else outcome["status"]
            market_state["outcomes"].append(outcome)
            market_state["outcomes"] = market_state["outcomes"][-MAX_SESSIONS:]
            changed = True
        return changed

    def _summary(self, market: str) -> dict[str, Any]:
        market_state = self._state["markets"][market]
        valid = [row for row in market_state["outcomes"] if row.get("status") == "valid"]
        baseline = sum(_number(((row.get("models") or {}).get("baseline") or {}).get("net_profit")) for row in valid)
        shadow = sum(_number(((row.get("models") or {}).get("flow_shadow") or {}).get("net_profit")) for row in valid)
        signal_count = sum(int(row.get("valid_signal_count") or 0) for row in market_state["sessions"])
        days = len(valid)
        gate_ready = days >= MIN_REVIEW_DAYS and signal_count >= MIN_REVIEW_SIGNALS
        return {
            "valid_trading_days": days,
            "quarantined_trading_days": sum(row.get("status") != "valid" for row in market_state["outcomes"]),
            "valid_signals": signal_count,
            "five_day_cycle_complete": days >= 5,
            "baseline_net_profit": round(baseline, 2),
            "flow_shadow_net_profit": round(shadow, 2),
            "incremental_net_profit": round(shadow - baseline, 2),
            "review_status": "candidate_review_only" if gate_ready else "collecting_only",
            "formal_ranking_locked": True,
        }

    def snapshot(self, flow: dict[str, Any] | None = None) -> dict[str, Any]:
        flow = flow or {}
        with self._lock:
            self._refresh_report()
            changed = False
            period = str(self._report.get("period") or "")
            markets: dict[str, Any] = {}
            for market in MARKETS:
                official_date = self._official_date(market)
                changed = self._settle(market, official_date) or changed
                if period == CLOSED_PERIOD[market]:
                    changed = self._freeze(market, official_date, flow) or changed
                today = _session_date(self.clock(), market)
                signal_dates = sorted((self._state["markets"][market]["signals"] or {}).keys())
                preview_date = today if today in signal_dates else (signal_dates[-1] if signal_dates else official_date)
                points = self._point_map(market, preview_date, flow) if preview_date else {}
                market_state = self._state["markets"][market]
                markets[market] = {
                    "market": market,
                    "preview_session_date": preview_date,
                    "official_session_date": official_date,
                    "status": "active_preview" if points else "waiting_for_valid_large_trade_signal",
                    "baseline_top10": self._select(market, points, adjusted=False),
                    "shadow_top10": self._select(market, points, adjusted=True),
                    "signals": list((market_state["signals"].get(preview_date) or {}).values()),
                    "summary": self._summary(market),
                    "latest_outcomes": market_state["outcomes"][-5:],
                }
            if changed:
                self._save()
            return {
                "version": VERSION,
                "updated_at": datetime.fromtimestamp(self.clock(), timezone.utc).isoformat(timespec="seconds"),
                "mode": "five_day_flow_weight_shadow",
                "markets": markets,
                "policy": {
                    "markets_separate": True,
                    "short_term_only": True,
                    "max_adjustment_points": MAX_POINT_ADJUSTMENT,
                    "single_alert_points": 1,
                    "cluster_alert_points": 2,
                    "persistence_confirmation_points": 1,
                    "formal_ranking_locked": True,
                    "medium_45_day_unchanged": True,
                    "long_6_month_unchanged": True,
                    "automatic_promotion": False,
                    "broker_orders": False,
                    "review_gate": {"trading_days": MIN_REVIEW_DAYS, "valid_signals": MIN_REVIEW_SIGNALS},
                    "missing_prices": "quarantine_not_score",
                },
            }
