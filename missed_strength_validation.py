"""Forward-only validation for strong stocks omitted by the ranking model.

The completed ranking from session N is frozen before it can be compared with
anything from session N+1.  Only after N+1 has officially closed do we compare
that immutable ranking with the actual daily-return TOP10/TOP20.  The module is
research-only: it has no scoring or broker imports and cannot modify V6 ranks.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
import gzip
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable


VERSION = 1
MARKETS = ("TW", "US")
CLOSED_PERIOD = {"TW": "evening", "US": "morning"}
MIN_OUTCOME_COVERAGE_PCT = 90.0
MIN_ACTUAL_ROWS = 20
MAX_OUTCOMES_PER_MARKET = 120

FACTOR_FIELDS = (
    ("short_term_score", "1至5日趨勢"),
    ("entry_score", "進場條件"),
    ("technical_score", "技術結構"),
    ("volume_score", "量能"),
    ("institution_score", "法人籌碼"),
    ("financial_quality_score", "財務品質"),
    ("fundamental_score", "基本面"),
    ("growth_score", "成長"),
    ("valuation_score", "估值"),
    ("group_score", "族群共振"),
    ("position_score", "技術位置"),
    ("market_flow_score", "市場資金"),
    ("credit_score", "融資借券"),
    ("kline_score", "K線結構"),
    ("tw_accumulation_score", "法人蓄力"),
    ("positioning_score", "籌碼雷達"),
    ("market_data_quality_score", "資料品質"),
    ("overall_confidence", "整體資料信心"),
    ("short_term_confidence", "短線資料信心"),
)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _is_stock(row: dict[str, Any], market: str) -> bool:
    return (
        str(row.get("market") or "").upper() == market
        and "ETF" not in str(row.get("type") or "").upper()
    )


def _session_date(rows: Iterable[dict[str, Any]], market: str) -> str:
    dates = [
        str(row.get("official_session_date") or "")
        for row in rows
        if _is_stock(row, market) and row.get("official_session_date")
    ]
    return Counter(dates).most_common(1)[0][0] if dates else ""


def _completed_checkpoint(market: str, period: str, updated_at: str) -> bool:
    """Reject intraday/manual pseudo-close reports even if period is mislabeled."""
    if period != CLOSED_PERIOD[market]:
        return False
    try:
        local_time = datetime.fromisoformat(updated_at.replace("/", "-"))
    except (TypeError, ValueError):
        return False
    if market == "TW":
        return local_time.hour >= 14
    # US scheduled morning report runs after the regular close in both DST
    # and standard time.  Noon/evening data must never create a US outcome.
    return 5 <= local_time.hour < 12


def _incomplete_reasons(row: dict[str, Any], expected_session_date: str) -> list[str]:
    reasons: list[str] = []
    if str(row.get("official_session_date") or "") != expected_session_date:
        reasons.append("事前行情不是本次凍結交易日")
    if row.get("market_contract_valid") is False:
        reasons.append("市場資料契約未通過")
    quality = _finite(row.get("market_data_quality_score"))
    if quality is None or quality < 50:
        reasons.append("市場資料品質不足50")
    coverage = _finite(row.get("entry_data_coverage"))
    total = _finite(row.get("entry_data_total"))
    if total is not None and total > 0 and (coverage is None or coverage < total):
        reasons.append("進場資料欄位不完整")
    session_close = _finite(row.get("official_close_price"))
    if session_close is None or session_close <= 0:
        reasons.append("事前正式收盤價缺失")
    return reasons


def _factor_snapshot(row: dict[str, Any]) -> dict[str, float]:
    result: dict[str, float] = {}
    for key, _label in FACTOR_FIELDS:
        value = _finite(row.get(key))
        if value is not None:
            result[key] = round(value, 4)
    return result


def _pressure_factors(row: dict[str, Any], factors: dict[str, float]) -> list[dict[str, Any]]:
    pressure: list[dict[str, Any]] = []
    labels = dict(FACTOR_FIELDS)
    for key, raw_value in factors.items():
        value = _finite(raw_value)
        threshold = 75 if key in {"market_data_quality_score", "overall_confidence", "short_term_confidence"} else 50
        if value is not None and value < threshold:
            pressure.append({
                "key": key,
                "label": labels.get(key, key),
                "value": round(value, 4),
                "threshold": threshold,
                "kind": "low_factor_score",
            })
    plan_factor = _finite(row.get("price_plan_rank_factor"))
    if plan_factor is not None and plan_factor < 0.9:
        pressure.append({
            "key": "price_plan_rank_factor", "label": "買進計畫完整度",
            "value": round(plan_factor, 4), "threshold": 0.9, "kind": "ranking_multiplier",
        })
    if row.get("trade_guard_blocked"):
        pressure.append({
            "key": "trade_guard_blocked", "label": "交易安全阻擋", "value": True,
            "kind": "hard_guard", "reason": row.get("trade_guard_reason") or "安全條件未通過",
        })
    for index, reason in enumerate(row.get("buy_candidate_reasons") or []):
        pressure.append({
            "key": f"buy_candidate_reason_{index + 1}", "label": "候選資格條件",
            "value": None, "kind": "eligibility_reason", "reason": str(reason),
        })
    return sorted(
        pressure,
        key=lambda item: (
            0 if item.get("kind") == "hard_guard" else 1,
            _finite(item.get("value")) if _finite(item.get("value")) is not None else 999,
            str(item.get("key") or ""),
        ),
    )


def _judgment(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "action": row.get("action"),
        "buy_candidate_status": row.get("buy_candidate_status"),
        "buy_candidate_reasons": list(row.get("buy_candidate_reasons") or []),
        "short_term_reason": row.get("short_term_reason"),
        "outlook_reasons": list(row.get("outlook_reasons") or []),
        "trade_guard_reason": row.get("trade_guard_reason"),
        "next_session_direction": row.get("next_session_direction"),
        "next_session_note": row.get("next_session_note"),
    }


def _freeze_row(row: dict[str, Any], fallback_rank: int, session_date: str) -> dict[str, Any]:
    factors = _factor_snapshot(row)
    incomplete = _incomplete_reasons(row, session_date)
    return {
        "symbol": str(row.get("symbol") or ""),
        "name": str(row.get("name") or row.get("symbol") or ""),
        "display_rank": int(_finite(row.get("overall_display_rank")) or fallback_rank),
        "qualified_rank": int(_finite(row.get("overall_rank"))) if _finite(row.get("overall_rank")) is not None else None,
        "rank_tier": int(_finite(row.get("overall_rank_tier")) or 0),
        "ranking_score": _finite(row.get("overall_ranking_score")),
        "total_score": _finite(row.get("score")),
        "short_term_score": _finite(row.get("short_term_score")),
        "entry_score": _finite(row.get("entry_score")),
        "signal_close": _finite(row.get("official_close_price")),
        "data_complete": not incomplete,
        "incomplete_reasons": incomplete,
        "judgment": _judgment(row),
        "factor_scores": factors,
        "pressure_factors": _pressure_factors(row, factors),
    }


def _snapshot_hash(snapshot: dict[str, Any]) -> str:
    payload = {key: value for key, value in snapshot.items() if key != "integrity_sha256"}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _build_snapshot(
    rows: list[dict[str, Any]],
    market: str,
    session_date: str,
    regime: dict[str, Any],
    updated_at: str,
) -> dict[str, Any] | None:
    market_rows = sorted(
        (dict(row) for row in rows if _is_stock(row, market)),
        key=lambda row: (
            int(_finite(row.get("overall_display_rank")) or 10**9),
            str(row.get("symbol") or ""),
        ),
    )
    if len(market_rows) < MIN_ACTUAL_ROWS:
        return None
    frozen = [_freeze_row(row, index, session_date) for index, row in enumerate(market_rows, 1)]
    if len({row["symbol"] for row in frozen}) != len(frozen):
        return None
    snapshot = {
        "version": VERSION,
        "market": market,
        "signal_session_date": session_date,
        "created_at": updated_at,
        "target": "next_completed_trading_session",
        "ranking_count": len(frozen),
        "top10": [row["symbol"] for row in frozen[:10]],
        "top20": [row["symbol"] for row in frozen[:20]],
        "regime": str(regime.get("regime") or "unknown"),
        "regime_evidence": {
            key: regime.get(key)
            for key in ("benchmark", "benchmark_close", "ma20", "ma60", "return20_pct", "ma20_slope5_pct", "classification_rule")
        },
        "rows": frozen,
        "policy": "排名只可對照下一個完成交易日；禁止同日結果、盤中結果與未來資料",
    }
    snapshot["integrity_sha256"] = _snapshot_hash(snapshot)
    return snapshot


def _read_snapshot(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        if path.suffix == ".gz":
            with gzip.open(path, "rt", encoding="utf-8") as stream:
                snapshot = json.load(stream)
        else:
            snapshot = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None, "snapshot_unreadable"
    if snapshot.get("version") != VERSION:
        return None, "snapshot_version_mismatch"
    if snapshot.get("integrity_sha256") != _snapshot_hash(snapshot):
        return None, "snapshot_hash_mismatch"
    return snapshot, None


def _write_snapshot(snapshot_dir: Path, snapshot: dict[str, Any]) -> tuple[str, str | None]:
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{snapshot['market']}-{snapshot['signal_session_date']}.json.gz"
    path = snapshot_dir / filename
    if path.exists():
        existing, error = _read_snapshot(path)
        if error:
            return filename, error
        if existing.get("integrity_sha256") != snapshot.get("integrity_sha256"):
            return filename, "immutable_snapshot_conflict"
        return filename, None
    tmp = snapshot_dir / f"{filename}.tmp"
    with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=9) as stream:
        json.dump(snapshot, stream, ensure_ascii=False, separators=(",", ":"))
    tmp.replace(path)
    return filename, None


def _actual_row(row: dict[str, Any]) -> dict[str, Any] | None:
    change = _finite(row.get("change_pct"))
    close = _finite(row.get("official_close_price"))
    if change is None or close is None or close <= 0:
        return None
    ratio = _finite(row.get("daily_volume_ratio"))
    if ratio is None:
        ratio = _finite(row.get("volume_pace"))
    pattern = str(row.get("volume_price_pattern") or "")
    if not pattern:
        pattern = (
            "價漲量增" if change > 0 and (ratio or 0) >= 1.2
            else "價漲量縮" if change > 0 and ratio is not None and ratio < 0.8
            else "價跌量增" if change < 0 and (ratio or 0) >= 1.2
            else "量價中性"
        )
    return {
        "symbol": str(row.get("symbol") or ""),
        "name": str(row.get("name") or row.get("symbol") or ""),
        "actual_return_pct": round(change, 6),
        "official_close_price": round(close, 6),
        "volume_ratio": round(ratio, 6) if ratio is not None else None,
        "avg_volume20": _finite(row.get("avg_volume20")),
        "volume_score": _finite(row.get("volume_score")),
        "volume_price_result": pattern,
        "market_relative_volume": _finite(row.get("market_relative_volume")),
    }


def _settle_snapshot(
    snapshot: dict[str, Any],
    rows: list[dict[str, Any]],
    outcome_session_date: str,
    updated_at: str,
) -> dict[str, Any]:
    market = str(snapshot.get("market") or "")
    valid_actual = [
        item for row in rows
        if _is_stock(row, market)
        and str(row.get("official_session_date") or "") == outcome_session_date
        for item in [_actual_row(row)] if item is not None
    ]
    expected = max(int(snapshot.get("ranking_count") or 0), len([
        row for row in rows if _is_stock(row, market)
    ]))
    coverage = len(valid_actual) / expected * 100 if expected else 0.0
    base = {
        "market": market,
        "signal_session_date": snapshot.get("signal_session_date"),
        "outcome_session_date": outcome_session_date,
        "settled_at": updated_at,
        "snapshot_integrity_sha256": snapshot.get("integrity_sha256"),
        "regime": snapshot.get("regime") or "unknown",
        "ranking_count": snapshot.get("ranking_count"),
        "actual_valid_count": len(valid_actual),
        "outcome_coverage_pct": round(coverage, 4),
    }
    if len(valid_actual) < MIN_ACTUAL_ROWS or coverage < MIN_OUTCOME_COVERAGE_PCT:
        return {
            **base,
            "status": "data_insufficient",
            "reason": "正式收盤結果覆蓋不足，不建立有效強勢股樣本",
            "actual_top20": [],
        }
    actual_top20 = sorted(
        valid_actual,
        key=lambda item: (-float(item["actual_return_pct"]), item["symbol"]),
    )[:20]
    frozen_map = {row["symbol"]: row for row in snapshot.get("rows") or []}
    frozen_top10 = set(snapshot.get("top10") or [])
    frozen_top20 = set(snapshot.get("top20") or [])
    enriched = []
    for actual_rank, actual in enumerate(actual_top20, 1):
        prior = frozen_map.get(actual["symbol"])
        captured_top10 = actual_rank <= 10 and actual["symbol"] in frozen_top10
        captured_top20 = actual["symbol"] in frozen_top20
        # ``actual_top20`` is the TOP20 audit, so a symbol already present in
        # the frozen TOP20 is captured even when another optional data field
        # was incomplete.  The old order counted captured symbols as misses.
        if captured_top20:
            error_type = "captured"
        elif prior is None or not prior.get("data_complete"):
            error_type = "data_incomplete"
        else:
            error_type = "model_judgment_error"
        top10_error_type = None
        if actual_rank <= 10:
            if captured_top10:
                top10_error_type = "captured"
            elif prior is None or not prior.get("data_complete"):
                top10_error_type = "data_incomplete"
            else:
                top10_error_type = "model_judgment_error"
        enriched.append({
            **actual,
            "actual_rank": actual_rank,
            "captured_by_matching_top10": captured_top10 if actual_rank <= 10 else None,
            "captured_by_matching_top20": captured_top20,
            "error_type": error_type,
            "top10_error_type": top10_error_type,
            "top20_error_type": error_type,
            "prior": prior,
        })
    captured10 = sum(item.get("captured_by_matching_top10") is True for item in enriched[:10])
    captured20 = sum(item.get("captured_by_matching_top20") is True for item in enriched)
    return {
        **base,
        "status": "valid",
        "actual_top20": enriched,
        "top10": {
            "actual_count": 10, "captured_count": captured10,
            "capture_rate_pct": round(captured10 / 10 * 100, 4),
        },
        "top20": {
            "actual_count": 20, "captured_count": captured20,
            "capture_rate_pct": round(captured20 / 20 * 100, 4),
        },
        "missed_model_judgment_count": sum(item["error_type"] == "model_judgment_error" for item in enriched),
        "missed_data_incomplete_count": sum(item["error_type"] == "data_incomplete" for item in enriched),
        "top10_missed_model_judgment_count": sum(
            item.get("top10_error_type") == "model_judgment_error" for item in enriched[:10]
        ),
        "top10_missed_data_incomplete_count": sum(
            item.get("top10_error_type") == "data_incomplete" for item in enriched[:10]
        ),
    }


def _aggregate(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [item for item in outcomes if item.get("status") == "valid"]
    captured10 = sum(int((item.get("top10") or {}).get("captured_count") or 0) for item in valid)
    captured20 = sum(int((item.get("top20") or {}).get("captured_count") or 0) for item in valid)
    actual10 = len(valid) * 10
    actual20 = len(valid) * 20
    return {
        "valid_sessions": len(valid),
        "invalid_sessions": sum(item.get("status") != "valid" for item in outcomes),
        "top10_actual_total": actual10,
        "top10_captured_total": captured10,
        "top10_capture_rate_pct": round(captured10 / actual10 * 100, 4) if actual10 else None,
        "top20_actual_total": actual20,
        "top20_captured_total": captured20,
        "top20_capture_rate_pct": round(captured20 / actual20 * 100, 4) if actual20 else None,
        "model_judgment_misses": sum(int(item.get("missed_model_judgment_count") or 0) for item in valid),
        "data_incomplete_misses": sum(int(item.get("missed_data_incomplete_count") or 0) for item in valid),
    }


def _common_features(outcomes: list[dict[str, Any]], valid_sessions: int) -> list[dict[str, Any]]:
    if valid_sessions < 20:
        return []
    counts: Counter[tuple[str, str]] = Counter()
    missed = 0
    for outcome in outcomes:
        if outcome.get("status") != "valid":
            continue
        for item in outcome.get("actual_top20") or []:
            if item.get("error_type") != "model_judgment_error":
                continue
            missed += 1
            for pressure in ((item.get("prior") or {}).get("pressure_factors") or []):
                counts[(str(pressure.get("key") or ""), str(pressure.get("label") or ""))] += 1
    return [
        {"key": key, "label": label, "missed_count": count, "share_of_model_misses_pct": round(count / missed * 100, 4) if missed else 0.0}
        for (key, label), count in counts.most_common(10)
    ]


def _summarize(market_state: dict[str, Any]) -> dict[str, Any]:
    outcomes = market_state.get("outcomes") or []
    overall = _aggregate(outcomes)
    regimes = {
        regime: _aggregate([item for item in outcomes if item.get("regime") == regime])
        for regime in ("bull", "bear", "sideways", "unknown")
    }
    days = int(overall["valid_sessions"])
    return {
        "overall": overall,
        "regimes": regimes,
        "review_gate": {
            "valid_trading_days": days,
            "preliminary_at": 20,
            "formal_candidate_at": 60,
            "status": "collecting" if days < 20 else "preliminary_review" if days < 60 else "candidate_review",
            "formal_ranking_locked": True,
            "review_with": "既有 reports/accuracy.json 的 TOP10 勝率",
        },
        "common_missed_features": _common_features(outcomes, days),
    }


def _market_shell(market: str) -> dict[str, Any]:
    return {
        "market": market,
        "status": "waiting_for_first_snapshot",
        "pending_snapshot": None,
        "outcomes": [],
        "quarantines": [],
        "summary": {},
    }


def empty_state(updated_at: str = "") -> dict[str, Any]:
    return {
        "version": VERSION,
        "updated_at": updated_at,
        "mode": "forward_validation_only",
        "title": "強勢股漏選驗證 V1",
        "policy": {
            "ranking_snapshot": "完成收盤的完整排名只對照下一完成交易日",
            "actual_result": "正式收盤後依當日漲幅排序TOP10／TOP20",
            "same_day_backfill_forbidden": True,
            "intraday_outcome_forbidden": True,
            "future_data_forbidden": True,
            "formal_v6_modified": False,
            "all_logic_modified": False,
            "top10_logic_modified": False,
            "sixty_day_gate_modified": False,
            "automatic_merge": False,
            "broker_orders": False,
        },
        "markets": {market: _market_shell(market) for market in MARKETS},
    }


def _load(path: Path, updated_at: str) -> dict[str, Any]:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return empty_state(updated_at)
    if state.get("version") != VERSION or not isinstance(state.get("markets"), dict):
        return empty_state(updated_at)
    for market in MARKETS:
        state["markets"].setdefault(market, _market_shell(market))
    return state


def update_state(
    state: dict[str, Any],
    rows: list[dict[str, Any]],
    market_regimes: dict[str, dict[str, dict[str, Any]]],
    snapshot_dir: Path,
    *,
    period: str,
    updated_at: str,
    intraday: bool = False,
) -> dict[str, Any]:
    if intraday:
        return state
    if not any(_completed_checkpoint(market, period, updated_at) for market in MARKETS):
        return state
    changed = False
    for market in MARKETS:
        if not _completed_checkpoint(market, period, updated_at):
            continue
        session_date = _session_date(rows, market)
        if not session_date:
            continue
        market_state = state["markets"][market]
        pending = market_state.get("pending_snapshot")
        if pending and session_date > str(pending.get("signal_session_date") or ""):
            snapshot, error = _read_snapshot(snapshot_dir / str(pending.get("file") or ""))
            if error or snapshot is None or snapshot.get("integrity_sha256") != pending.get("integrity_sha256"):
                market_state.setdefault("quarantines", []).append({
                    "signal_session_date": pending.get("signal_session_date"),
                    "detected_at": updated_at,
                    "reason": error or "pending_snapshot_hash_mismatch",
                })
            else:
                market_state.setdefault("outcomes", []).append(
                    _settle_snapshot(snapshot, rows, session_date, updated_at)
                )
                market_state["outcomes"] = market_state["outcomes"][-MAX_OUTCOMES_PER_MARKET:]
            market_state["pending_snapshot"] = None
            changed = True
        if market_state.get("pending_snapshot") is None:
            regime = (market_regimes.get(market) or {}).get(session_date) or {}
            snapshot = _build_snapshot(rows, market, session_date, regime, updated_at)
            if snapshot is None:
                market_state.setdefault("quarantines", []).append({
                    "signal_session_date": session_date,
                    "detected_at": updated_at,
                    "reason": "complete_ranking_snapshot_insufficient",
                })
            else:
                filename, error = _write_snapshot(snapshot_dir, snapshot)
                if error:
                    market_state.setdefault("quarantines", []).append({
                        "signal_session_date": session_date,
                        "detected_at": updated_at,
                        "reason": error,
                    })
                else:
                    market_state["pending_snapshot"] = {
                        "signal_session_date": session_date,
                        "file": filename,
                        "integrity_sha256": snapshot["integrity_sha256"],
                        "ranking_count": snapshot["ranking_count"],
                        "top10": snapshot["top10"],
                        "top20": snapshot["top20"],
                        "regime": snapshot["regime"],
                    }
            changed = True
        market_state["quarantines"] = market_state.get("quarantines", [])[-120:]
        market_state["summary"] = _summarize(market_state)
        market_state["status"] = (
            "running" if market_state.get("pending_snapshot")
            else "data_quarantined" if market_state.get("quarantines")
            else "waiting_for_first_snapshot"
        )
    if changed:
        state["updated_at"] = updated_at
    values = {state["markets"][market].get("status") for market in MARKETS}
    state["status"] = "running" if "running" in values else "data_quarantined" if "data_quarantined" in values else "waiting"
    return state


def update_missed_strength_validation(
    reports_dir: Path,
    rows: list[dict[str, Any]],
    market_regimes: dict[str, dict[str, dict[str, Any]]],
    *,
    period: str,
    updated_at: str,
    intraday: bool = False,
) -> Path:
    path = reports_dir / "missed_strength_validation.json"
    state = update_state(
        _load(path, updated_at), rows, market_regimes,
        reports_dir / "missed_strength_snapshots",
        period=period, updated_at=updated_at, intraday=intraday,
    )
    tmp = reports_dir / "missed_strength_validation.tmp"
    tmp.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)
    return path
