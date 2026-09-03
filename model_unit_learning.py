"""Forward-only learning ledgers for Central AI evidence units.

Each unit freezes its own point-in-time signal, settles it against the next
completed market session, and may earn a bounded trust multiplier inside the
Central AI shadow layer.  Formal V6 scores, ranks, ledgers and orders are never
written by this module.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

from prediction_engine.storage import PredictionStore


SCHEMA_VERSION = 1
MIN_SESSIONS = 20
MIN_SAMPLES = 100
MIN_HOLDOUT_HIT_PCT = 52.0
ROLLBACK_HIT_PCT = 48.0
REQUIRED_WINS = 3
ROLLBACK_FAILURES = 2
MIN_MULTIPLIER = 0.85
MAX_MULTIPLIER = 1.15
COHORTS = ("TW_STOCK", "TW_ETF", "US_STOCK", "US_ETF")

UNIT_SPECS: dict[str, dict[str, str]] = {
    "central_decision": {"label": "中央AI決策中樞", "objective": "forward_direction"},
    "technical_kline": {"label": "技術趨勢／K線／頭肩", "objective": "forward_direction"},
    "volume_attack": {"label": "量能／分時量／攻擊量", "objective": "forward_direction"},
    "capital_flow": {"label": "大量買賣／資金流", "objective": "forward_direction"},
    "tw_credit_broker": {"label": "融資融券／借券／券商分點", "objective": "forward_direction"},
    "tw_accumulation": {"label": "台股法人累積", "objective": "forward_direction"},
    "macro_regime": {"label": "大盤／總經狀態", "objective": "forward_direction"},
    "news_event": {"label": "新聞／公告／突發事件", "objective": "forward_direction"},
    "fundamental_growth_quality": {"label": "基本面／成長／財務品質", "objective": "forward_direction"},
    "etf_structure": {"label": "ETF成分／廣度／折溢價／追蹤／費用", "objective": "forward_direction"},
    "data_quality": {"label": "資料品質／日期／來源契約", "objective": "source_reliability"},
}


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _bounded(value: Any, low: float = 0.0, high: float = 100.0) -> float:
    number = _finite(value)
    return round(max(low, min(high, number if number is not None else 0.0)), 4)


def _asset_group(row: dict[str, Any]) -> str:
    market = str(row.get("market") or "").upper()
    asset = "ETF" if "ETF" in str(row.get("type") or "").upper() else "STOCK"
    return f"{market}_{asset}"


def _score_direction(score: Any, *, available: bool = True) -> tuple[int, float, str]:
    value = _finite(score)
    if not available or value is None:
        return 0, 0.0, "missing"
    if value >= 55.0:
        return 1, _bounded(value), "ready"
    if value <= 45.0:
        return -1, _bounded(100.0 - value), "ready"
    return 0, _bounded(abs(value - 50.0) * 2.0), "neutral"


def _mean(values: list[Any]) -> float | None:
    clean = [number for number in (_finite(value) for value in values) if number is not None]
    return sum(clean) / len(clean) if clean else None


def _quality_score(row: dict[str, Any]) -> float:
    direct = _finite(row.get("market_data_quality_score"))
    if direct is not None:
        return _bounded(direct)
    text = str(row.get("overall_data_quality") or "")
    if "/" in text:
        try:
            available, total = (float(value) for value in text.split("/", 1))
            if total > 0:
                return _bounded(available / total * 100.0)
        except ValueError:
            pass
    return 0.0


def _unit_observations(
    row: dict[str, Any], decision: dict[str, Any]
) -> dict[str, tuple[int, float, float, str]]:
    quality = _quality_score(row)
    result: dict[str, tuple[int, float, float, str]] = {}

    recommendation = str((decision.get("final") or {}).get("recommendation") or "")
    central_direction = 1 if recommendation in {"can_scale", "wait_pullback"} else -1 if recommendation == "avoid" else 0
    result["central_decision"] = (
        central_direction,
        _bounded((decision.get("final") or {}).get("confidence")),
        _bounded((decision.get("final") or {}).get("confidence")),
        "ready" if central_direction else "neutral",
    )

    for unit_id, score_key in (
        ("technical_kline", "technical_score"),
        ("volume_attack", "volume_score"),
        ("macro_regime", "macro_score"),
    ):
        direction, strength, status = _score_direction(row.get(score_key))
        result[unit_id] = (direction, strength, quality, status)

    flow = decision.get("capital_flow_shadow") or {}
    flow_direction = str(flow.get("direction") or "")
    flow_sign = 1 if flow_direction == "support" else -1 if flow_direction == "oppose" else 0
    flow_ready = bool(flow.get("validated_for_decision"))
    result["capital_flow"] = (
        flow_sign if flow_ready else 0,
        _bounded(flow.get("confidence")),
        _bounded(flow.get("confidence")),
        "ready" if flow_ready and flow_sign else "missing",
    )

    credit_available = bool(row.get("credit_available")) and str(row.get("market") or "").upper() == "TW"
    direction, strength, status = _score_direction(row.get("credit_score"), available=credit_available)
    result["tw_credit_broker"] = (direction, strength, quality, status)

    accumulation_available = bool(row.get("tw_accumulation_available")) and _asset_group(row) == "TW_STOCK"
    direction, strength, status = _score_direction(
        row.get("tw_accumulation_score"), available=accumulation_available
    )
    result["tw_accumulation"] = (direction, strength, quality, status)

    news_available = bool(row.get("news_data_available"))
    penalty = _finite(row.get("news_penalty")) or 0.0
    news_direction = -1 if news_available and penalty > 0 else 0
    result["news_event"] = (
        news_direction,
        _bounded(abs(penalty) * 10.0),
        100.0 if row.get("news_verified") else 30.0 if news_available else 0.0,
        "ready" if news_direction else "neutral" if news_available else "missing",
    )

    is_etf = "ETF" in str(row.get("type") or "").upper()
    fundamental_score = _mean([
        row.get("financial_quality_score"), row.get("growth_score"), row.get("fundamental_score")
    ])
    direction, strength, status = _score_direction(
        fundamental_score,
        available=bool(row.get("fundamental_available")) and not is_etf,
    )
    result["fundamental_growth_quality"] = (direction, strength, quality, status)

    if is_etf and row.get("etf_premium_blocked"):
        result["etf_structure"] = (-1, 80.0, quality, "ready")
    else:
        direction, strength, status = _score_direction(
            row.get("group_score"), available=is_etf
        )
        result["etf_structure"] = (direction, strength, quality, status)

    result["data_quality"] = (
        0,
        quality,
        100.0,
        "ready" if row.get("market_contract_valid") is not False else "invalid_contract",
    )
    return result


def _market_ready(period: str, market: str) -> bool:
    if period == "evening":
        return market == "TW"
    if period == "morning":
        return market == "US"
    if period == "noon":
        return False
    return True


def record_unit_signals(
    store: PredictionStore,
    rows: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    *,
    period: str,
    updated_at: str,
    intraday: bool,
) -> int:
    if intraday:
        return 0
    decision_index = {
        (str(item.get("market") or "").upper(), str(item.get("symbol") or "").upper()): item
        for item in decisions if isinstance(item, dict)
    }
    values = []
    with store.connect() as db:
        recorded_sessions = {
            (str(item[0]), str(item[1]))
            for item in db.execute("SELECT market,session_date FROM market_sessions")
        }
    for row in rows:
        if not isinstance(row, dict) or not row.get("symbol"):
            continue
        market = str(row.get("market") or "").upper()
        session_date = str(row.get("official_session_date") or "")
        price = _finite(row.get("official_close_price") or row.get("price"))
        if (
            market not in {"TW", "US"}
            or not _market_ready(period, market)
            or (market, session_date) not in recorded_sessions
            or price is None
            or price <= 0
        ):
            continue
        decision = decision_index.get((market, str(row.get("symbol") or "").upper()))
        if not decision:
            continue
        group = _asset_group(row)
        for unit_id, (direction, strength, confidence, evidence_status) in _unit_observations(row, decision).items():
            values.append((
                unit_id, UNIT_SPECS[unit_id]["objective"], market, group,
                session_date, str(row.get("symbol")), str(row.get("name") or row.get("symbol")),
                float(price), int(direction), float(strength), float(confidence),
                evidence_status, updated_at,
            ))
    with store.connect() as db:
        before = db.total_changes
        db.executemany(
            """INSERT OR IGNORE INTO unit_learning_predictions(
            unit_id,objective,market,asset_group,session_date,symbol,name,
            source_price,direction,strength,confidence,evidence_status,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            values,
        )
        return db.total_changes - before


def settle_unit_signals(store: PredictionStore) -> int:
    settled = 0
    with store.connect() as db:
        for market in ("TW", "US"):
            sessions = [str(row[0]) for row in db.execute(
                "SELECT session_date FROM market_sessions WHERE market=? ORDER BY session_date",
                (market,),
            )]
            session_index = {value: index for index, value in enumerate(sessions)}
            pending = db.execute(
                "SELECT id,unit_id,objective,session_date,symbol,source_price,direction FROM unit_learning_predictions "
                "WHERE market=? AND status='pending'",
                (market,),
            ).fetchall()
            updates = []
            for row in pending:
                source_index = session_index.get(str(row["session_date"]))
                if source_index is None or source_index + 1 >= len(sessions):
                    continue
                target_day = sessions[source_index + 1]
                price_row = db.execute(
                    "SELECT close_price FROM prices WHERE market=? AND session_date=? AND symbol=?",
                    (market, target_day, row["symbol"]),
                ).fetchone()
                if (not price_row or float(price_row[0]) <= 0) and row["objective"] != "source_reliability":
                    continue
                outcome = float(price_row[0]) if price_row and float(price_row[0]) > 0 else None
                if row["objective"] == "source_reliability":
                    realized = None
                    correct = int(outcome is not None)
                else:
                    realized = (outcome / float(row["source_price"]) - 1.0) * 100.0
                    direction = int(row["direction"])
                    correct = None if direction == 0 else int(realized > 0 if direction > 0 else realized < 0)
                updates.append((target_day, outcome, realized, correct, row["id"]))
            db.executemany(
                "UPDATE unit_learning_predictions SET status='matured',outcome_session_date=?,"
                "outcome_price=?,realized_return_pct=?,direction_correct=? WHERE id=?",
                updates,
            )
            settled += len(updates)
    return settled


def _metrics(store: PredictionStore, unit_id: str, group: str) -> dict[str, Any]:
    with store.connect() as db:
        rows = db.execute(
            "SELECT objective,session_date,direction,strength,confidence,direction_correct,realized_return_pct "
            "FROM unit_learning_predictions WHERE unit_id=? AND asset_group=? AND status='matured' "
            "ORDER BY session_date,symbol",
            (unit_id, group),
        ).fetchall()
    if not rows:
        return {
            "matured_rows": 0, "scored_samples": 0, "session_count": 0,
            "direction_hit_pct": None, "holdout_hit_pct": None,
            "average_strength": None, "latest_session": None,
        }
    sessions = sorted({str(row["session_date"]) for row in rows})
    scored = [row for row in rows if row["direction_correct"] is not None]
    holdout_count = max(4, len(sessions) // 5)
    holdout_sessions = set(sessions[-holdout_count:])
    holdout = [row for row in scored if str(row["session_date"]) in holdout_sessions]
    return_rows = [row for row in scored if row["realized_return_pct"] is not None]
    result = {
        "matured_rows": len(rows),
        "scored_samples": len(scored),
        "session_count": len(sessions),
        "direction_hit_pct": round(sum(int(row["direction_correct"]) for row in scored) / len(scored) * 100.0, 2) if scored else None,
        "holdout_hit_pct": round(sum(int(row["direction_correct"]) for row in holdout) / len(holdout) * 100.0, 2) if holdout else None,
        "holdout_samples": len(holdout),
        "average_return_pct": round(sum(float(row["realized_return_pct"]) for row in return_rows) / len(return_rows), 4) if return_rows else None,
        "average_strength": round(sum(float(row["strength"]) for row in rows) / len(rows), 2),
        "average_confidence": round(sum(float(row["confidence"]) for row in rows) / len(rows), 2),
        "latest_session": sessions[-1],
    }
    if rows[0]["objective"] == "source_reliability":
        result["source_reliability_pct"] = result["direction_hit_pct"]
        result["direction_hit_pct"] = None
        result["holdout_hit_pct"] = None
    return result


def _default_control(unit_id: str, group: str) -> dict[str, Any]:
    return {
        "unit_id": unit_id,
        "market": group.split("_", 1)[0],
        "asset_group": group,
        "active_multiplier": 1.0,
        "previous_multiplier": None,
        "candidate_multiplier": None,
        "status": "collecting",
        "consecutive_wins": 0,
        "consecutive_failures": 0,
        "last_evaluated_through": None,
        "reason": "等待至少20個有效交易日與100筆獨立方向樣本",
        "metrics": {},
        "updated_at": None,
    }


def _control(store: PredictionStore, unit_id: str, group: str) -> dict[str, Any]:
    with store.connect() as db:
        row = db.execute(
            "SELECT * FROM unit_trust_control WHERE unit_id=? AND asset_group=?",
            (unit_id, group),
        ).fetchone()
    if not row:
        return _default_control(unit_id, group)
    payload = dict(row)
    payload["metrics"] = json.loads(payload.pop("metrics_json"))
    return payload


def _candidate_multiplier(hit_pct: float) -> float:
    return round(max(MIN_MULTIPLIER, min(MAX_MULTIPLIER, 1.0 + (hit_pct - 50.0) / 100.0)), 4)


def _evaluate_control(
    store: PredictionStore,
    unit_id: str,
    group: str,
    metrics: dict[str, Any],
    *,
    updated_at: str,
) -> dict[str, Any]:
    state = _control(store, unit_id, group)
    latest = metrics.get("latest_session")
    if not latest or state.get("last_evaluated_through") == latest:
        return state
    objective = UNIT_SPECS[unit_id]["objective"]
    market = group.split("_", 1)[0]
    active = float(state.get("active_multiplier") or 1.0)
    previous = state.get("previous_multiplier")
    wins = int(state.get("consecutive_wins") or 0)
    failures = int(state.get("consecutive_failures") or 0)
    hit = _finite(metrics.get("holdout_hit_pct"))
    candidate = _candidate_multiplier(hit) if hit is not None else 1.0
    qualified = bool(
        objective == "forward_direction"
        and int(metrics.get("session_count") or 0) >= MIN_SESSIONS
        and int(metrics.get("scored_samples") or 0) >= MIN_SAMPLES
        and hit is not None
        and hit >= MIN_HOLDOUT_HIT_PCT
    )
    active_before = active
    event = "observed"
    if objective != "forward_direction":
        status = "monitoring_reliability"
        reason = "資料品質只學來源可靠度，不拿股價漲跌當答案，也不自動改分"
        wins = failures = 0
    elif int(metrics.get("session_count") or 0) < MIN_SESSIONS or int(metrics.get("scored_samples") or 0) < MIN_SAMPLES:
        status = "collecting"
        reason = f"有效交易日 {int(metrics.get('session_count') or 0)}/{MIN_SESSIONS}；方向樣本 {int(metrics.get('scored_samples') or 0)}/{MIN_SAMPLES}"
        wins = failures = 0
    elif active == 1.0:
        failures = 0
        wins = wins + 1 if qualified else 0
        status = "candidate_confirming" if qualified else "candidate_rejected"
        reason = "候選信任權重通過樣本外守門，等待連續3個不同交易日確認" if qualified else "樣本外命中率未達52%，維持中性信任"
        if wins >= REQUIRED_WINS:
            previous = active
            active = candidate
            wins = 0
            status = "shadow_trust_active"
            reason = "連續3個不同交易日通過守門，中央影子信任權重自動啟用"
            event = "promoted"
    elif qualified:
        failures = 0
        if abs(candidate - active) >= 0.01:
            wins += 1
            status = "trust_update_confirming"
            reason = "新信任權重等待連續3個不同交易日確認"
            if wins >= REQUIRED_WINS:
                previous = active
                active = candidate
                wins = 0
                status = "shadow_trust_active"
                reason = "新版信任權重連續3日通過守門，中央影子權重已更新"
                event = "updated"
        else:
            wins = 0
            status = "shadow_trust_active"
            reason = "目前信任權重仍符合樣本外守門"
    else:
        wins = 0
        failures = failures + 1 if hit is not None and hit < ROLLBACK_HIT_PCT else 0
        status = "active_trust_warning" if failures else "shadow_trust_active"
        reason = "近期樣本外命中率低於48%，等待第二個不同交易日確認" if failures else "近期表現未達更新門檻，維持現行信任權重"
        if failures >= ROLLBACK_FAILURES:
            rollback = float(previous) if previous is not None else 1.0
            previous = 1.0 if rollback != 1.0 else None
            active = rollback
            failures = 0
            status = "rolled_back"
            reason = "連續2個不同交易日低於安全門檻，中央影子信任權重已自動退回"
            event = "rolled_back"
    with store.connect() as db:
        db.execute(
            """INSERT OR REPLACE INTO unit_trust_control VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                unit_id, market, group, active, previous, candidate, status, wins,
                failures, latest, reason, json.dumps(metrics, separators=(",", ":")), updated_at,
            ),
        )
        db.execute(
            """INSERT OR IGNORE INTO unit_trust_events(
            unit_id,market,asset_group,previous_multiplier,new_multiplier,
            candidate_multiplier,event,qualified,evaluated_through,reason,metrics_json,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                unit_id, market, group, active_before, active, candidate, event,
                int(qualified), latest, reason, json.dumps(metrics, separators=(",", ":")), updated_at,
            ),
        )
    return _control(store, unit_id, group)


def refresh_unit_learning(
    store: PredictionStore, *, updated_at: str, intraday: bool
) -> tuple[dict[str, dict[str, float]], dict[str, Any]]:
    settled = 0 if intraday else settle_unit_signals(store)
    controls: dict[str, dict[str, Any]] = {}
    trust: dict[str, dict[str, float]] = {group: {} for group in COHORTS}
    for unit_id in UNIT_SPECS:
        controls[unit_id] = {}
        for group in COHORTS:
            metrics = _metrics(store, unit_id, group)
            state = _evaluate_control(store, unit_id, group, metrics, updated_at=updated_at)
            controls[unit_id][group] = {**state, "metrics": metrics}
            trust[group][unit_id] = float(state.get("active_multiplier") or 1.0)
    return trust, {"settled_rows": settled, "controls": controls}


def _recent_events(store: PredictionStore, limit: int = 60) -> list[dict[str, Any]]:
    with store.connect() as db:
        rows = db.execute(
            "SELECT * FROM unit_trust_events ORDER BY id DESC LIMIT ?", (int(limit),)
        ).fetchall()
    result = []
    for row in rows:
        payload = dict(row)
        payload["qualified"] = bool(payload["qualified"])
        payload["metrics"] = json.loads(payload.pop("metrics_json"))
        result.append(payload)
    return result


def build_unit_learning_report(
    reports_dir: Path,
    store: PredictionStore,
    refresh: dict[str, Any],
    *,
    inserted_rows: int,
    updated_at: str,
) -> dict[str, Any]:
    controls = refresh.get("controls") or {}
    events = _recent_events(store)
    material = [
        event for event in events
        if event.get("created_at") == updated_at
        and event.get("event") in {"promoted", "updated", "rolled_back"}
    ]
    notifications = []
    for event in material:
        label = UNIT_SPECS.get(str(event.get("unit_id")), {}).get("label") or event.get("unit_id")
        action = "自動退版" if event.get("event") == "rolled_back" else "自動更新" if event.get("event") == "updated" else "自動啟用"
        notifications.append({
            "id": f"unit-trust:{event.get('unit_id')}:{event.get('asset_group')}:{event.get('evaluated_through')}:{event.get('event')}",
            "event": event.get("event"),
            "message": f"🧠 模型成長｜{label}／{event.get('asset_group')} {action}影子信任權重：{float(event.get('previous_multiplier') or 1):.2f} → {float(event.get('new_multiplier') or 1):.2f}。正式V6未變更。",
        })
    active = sum(
        abs(float((state or {}).get("active_multiplier") or 1.0) - 1.0) >= 0.001
        for groups in controls.values() for state in groups.values()
    )
    with store.connect() as db:
        totals = {
            "frozen_rows": int(db.execute("SELECT COUNT(*) FROM unit_learning_predictions").fetchone()[0]),
            "matured_rows": int(db.execute("SELECT COUNT(*) FROM unit_learning_predictions WHERE status='matured'").fetchone()[0]),
            "pending_rows": int(db.execute("SELECT COUNT(*) FROM unit_learning_predictions WHERE status='pending'").fetchone()[0]),
        }
    units = []
    for unit_id, spec in UNIT_SPECS.items():
        units.append({
            "unit_id": unit_id,
            "label": spec["label"],
            "objective": spec["objective"],
            "dedicated_ledger": True,
            "cohorts": controls.get(unit_id) or {},
        })
    payload = {
        "schema_version": SCHEMA_VERSION,
        "updated_at": updated_at,
        "status": "ready",
        "mode": "private_forward_unit_learning_with_public_summary",
        "summary": {
            "registered_units": len(UNIT_SPECS),
            "dedicated_ledger_units": len(UNIT_SPECS),
            "cohort_streams": len(UNIT_SPECS) * len(COHORTS),
            "active_shadow_trust_streams": active,
            "inserted_rows_this_run": int(inserted_rows),
            **totals,
        },
        "policy": {
            "point_in_time_only": True,
            "next_completed_session_outcome": True,
            "minimum_sessions": MIN_SESSIONS,
            "minimum_samples": MIN_SAMPLES,
            "minimum_holdout_hit_pct": MIN_HOLDOUT_HIT_PCT,
            "activation_requires_distinct_session_wins": REQUIRED_WINS,
            "automatic_rollback_after_failures": ROLLBACK_FAILURES,
            "multiplier_bounds": [MIN_MULTIPLIER, MAX_MULTIPLIER],
            "scope": "central_ai_shadow_only",
            "formal_v6_unchanged": True,
            "formal_ranking_unchanged": True,
            "automatic_orders": False,
            "data_quality_never_judged_by_stock_return": True,
        },
        "units": units,
        "recent_events": events,
        "pending_notifications": notifications,
    }
    reports_dir = Path(reports_dir)
    path = reports_dir / "model_unit_learning.json"
    tmp = reports_dir / "model_unit_learning.tmp"
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return payload


def open_prediction_store(reports_dir: Path) -> PredictionStore:
    reports_dir = Path(reports_dir)
    project_root = reports_dir.parent if reports_dir.name == "reports" else reports_dir
    database_path = Path(
        os.getenv("PREDICTION_ENGINE_DB")
        or project_root / ".prediction_engine" / "prediction_engine.sqlite3"
    )
    max_bytes = int(os.getenv("PREDICTION_ENGINE_MAX_BYTES", str(500 * 1024 * 1024)))
    return PredictionStore(database_path, max_bytes=max_bytes)
