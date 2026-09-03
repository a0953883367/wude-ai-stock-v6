"""Build an auditable error-learning report without touching production V6.

The report converts frozen validation errors into event-level diagnoses and
research-only challenger proposals.  It never rewrites a forecast, changes a
weight, promotes a model, or places an order.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from model_learning_catalog import build_complete_learning_catalog


SCHEMA_VERSION = 2
PRELIMINARY_DAYS = 20
PROMOTION_DAYS = 60
COHORT_LABELS = {
    "TW_STOCK": "台股個股",
    "TW_ETF": "台灣ETF",
    "US_STOCK": "美股個股",
    "US_ETF": "美國ETF",
}

CANDIDATE_RULES = {
    "event_gap_risk": {
        "name": "事件風險防護影子候選",
        "purpose": "遇到預測當時已存在的重大新聞或盤前異常跳空時，降低方向信心或棄權。",
        "required_evidence": "具時間戳的事前新聞、公告與盤前異常資料",
    },
    "missed_strength_rotation": {
        "name": "強勢反轉與族群輪動影子候選",
        "purpose": "檢查量能、突破、攻擊量與族群廣度是否已推翻原本看跌判斷。",
        "required_evidence": "事前凍結的量價、族群廣度與資金流證據",
    },
    "intraday_reversal": {
        "name": "開盤確認影子候選",
        "purpose": "把隔夜方向和盤中進場分開，使用15／30分鐘確認及風險控制。",
        "required_evidence": "事前預判、正式開盤及15／30分鐘確認資料",
    },
    "etf_model_separation": {
        "name": "ETF專屬影子候選",
        "purpose": "台灣ETF與美國ETF分開驗證，不再以個股規則解讀ETF。",
        "required_evidence": "ETF類型、成分／槓桿結構及分市場前向結果",
    },
    "direction_calibration": {
        "name": "方向門檻校準影子候選",
        "purpose": "在固定資料上比較共識門檻，不回頭改寫正式V6預測。",
        "required_evidence": "相同市場、盤勢與期間的獨立前向事件",
    },
}


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def _write(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _metric(group: dict[str, Any], section: str, horizon: str = "1") -> dict[str, Any]:
    value = ((group.get(section) or {}).get(horizon) or {})
    return value if isinstance(value, dict) else {}


def _signal_health(performance: dict[str, Any]) -> dict[str, Any]:
    groups = performance.get("groups") or {}
    health: dict[str, Any] = {}
    for cohort, label in COHORT_LABELS.items():
        group = groups.get(cohort) or {}
        direction = _metric(group, "horizons")
        trades = _metric(group, "trade_signals")
        trade_samples = int(trades.get("samples") or 0)
        direction_samples = int(direction.get("samples") or 0)
        health[cohort] = {
            "label": label,
            "direction_samples": direction_samples,
            "trade_signal_samples": trade_samples,
            "status": "collecting_trade_outcomes" if trade_samples else "no_trade_signal_yet",
            "detail": (
                f"已有 {trade_samples} 筆完整交易訊號結果，繼續前向驗證。"
                if trade_samples
                else f"已有 {direction_samples} 筆方向結果，但尚無完整交易訊號；需檢查門檻或資料契約。"
            ),
        }
    return health


def _candidate_stage(days: int, evidence_events: int) -> str:
    if not evidence_events:
        return "waiting_evidence"
    if days < PRELIMINARY_DAYS:
        return "collecting_before_20d"
    if days < PROMOTION_DAYS:
        return "preliminary_review_only"
    return "manual_promotion_review_only"


def _candidate_registry(
    error_cases: dict[str, Any],
    signal_health: dict[str, Any],
    days: int,
) -> list[dict[str, Any]]:
    counts = error_cases.get("cause_counts") or {}
    candidates: list[dict[str, Any]] = []
    for cause, rule in CANDIDATE_RULES.items():
        count = int(counts.get(cause) or 0)
        if not count:
            continue
        candidates.append({
            "candidate_id": f"shadow:{cause}:v1",
            "name": rule["name"],
            "cause": cause,
            "evidence_event_count": count,
            "purpose": rule["purpose"],
            "required_evidence": rule["required_evidence"],
            "stage": _candidate_stage(days, count),
            "uses_future_data": False,
            "affects_formal_v6": False,
            "automatic_promotion": False,
            "broker_orders": False,
        })

    zero_signal_cohorts = [
        key for key, value in signal_health.items()
        if int(value.get("trade_signal_samples") or 0) == 0
        and int(value.get("direction_samples") or 0) > 0
    ]
    if zero_signal_cohorts:
        candidates.append({
            "candidate_id": "shadow:trade_threshold_diagnostic:v1",
            "name": "交易門檻診斷影子候選",
            "cause": "zero_trade_signal",
            "evidence_event_count": sum(
                int(signal_health[key].get("direction_samples") or 0)
                for key in zero_signal_cohorts
            ),
            "affected_cohorts": zero_signal_cohorts,
            "purpose": "比較資料契約缺欄與門檻過嚴兩種原因；只做影子門檻試算，不放寬正式買進條件。",
            "required_evidence": "方向樣本、買點觸發欄位、15／30分鐘確認與交易成本",
            "stage": _candidate_stage(days, 1),
            "uses_future_data": False,
            "affects_formal_v6": False,
            "automatic_promotion": False,
            "broker_orders": False,
        })
    candidates.sort(key=lambda item: (-int(item.get("evidence_event_count") or 0), str(item.get("candidate_id"))))
    return candidates


def update_model_learning(reports_dir: Path, *, updated_at: str = "") -> dict[str, Any]:
    reports_dir = Path(reports_dir)
    previous = _read(reports_dir / "model_learning.json")
    performance = _read(reports_dir / "performance.json")
    calibration = performance.get("calibration") or {}
    error_cases = performance.get("error_cases") or {}
    days = int(calibration.get("trading_days_collected") or 0)
    signal_health = _signal_health(performance)
    candidates = _candidate_registry(error_cases, signal_health, days)
    events = error_cases.get("event_clusters") or []
    previous_errors = previous.get("error_learning") or {}
    previous_candidates = previous.get("shadow_candidates") or []
    has_event_level_errors = bool(
        error_cases.get("unique_event_count") is not None
        or error_cases.get("event_clusters")
        or error_cases.get("cause_counts")
    )
    if not has_event_level_errors and int(previous_errors.get("independent_events") or 0) > 0:
        events = previous_errors.get("recent_events") or []
        if isinstance(previous_candidates, list) and previous_candidates:
            candidates = previous_candidates
    generated_at = updated_at or str(performance.get("updated_at") or datetime.now().isoformat(timespec="seconds"))
    complete_catalog = build_complete_learning_catalog(reports_dir)

    payload = {
        "schema_version": SCHEMA_VERSION,
        "updated_at": generated_at,
        "mode": "event_level_shadow_learning_only",
        "status": "collecting" if days < PROMOTION_DAYS else "manual_review_available",
        "progress": {
            "trading_days_collected": days,
            "preliminary_review_days": PRELIMINARY_DAYS,
            "promotion_review_days": PROMOTION_DAYS,
            "remaining_to_preliminary": max(PRELIMINARY_DAYS - days, 0),
            "remaining_to_promotion_review": max(PROMOTION_DAYS - days, 0),
        },
        "error_learning": {
            "raw_error_rows": int(error_cases.get("row_count") or error_cases.get("count") or 0),
            "independent_events": int(error_cases.get("unique_event_count") or (previous_errors.get("independent_events") if not has_event_level_errors else 0) or 0),
            "unique_symbols": int(error_cases.get("unique_symbol_count") or (previous_errors.get("unique_symbols") if not has_event_level_errors else 0) or 0),
            "duplicate_rows_collapsed": int(error_cases.get("duplicate_row_count") or (previous_errors.get("duplicate_rows_collapsed") if not has_event_level_errors else 0) or 0),
            "cause_counts": error_cases.get("cause_counts") or (previous_errors.get("cause_counts") if not has_event_level_errors else {}) or {},
            "recent_events": events[:20],
        },
        "signal_health": signal_health,
        "shadow_candidates": candidates,
        "complete_learning": complete_catalog,
        "policy": {
            "formal_v6_frozen": True,
            "formal_ranking_unchanged": True,
            "formal_weights_unchanged": True,
            "historical_predictions_never_rewritten": True,
            "future_data_forbidden": True,
            "event_rows_not_treated_as_independent": True,
            "formal_candidate_only_until_manual_approval": True,
            "controlled_shadow_auto_promotion": True,
            "controlled_central_trust_auto_update": True,
            "formal_v6_automatic_promotion": False,
            "automatic_merge": False,
            "broker_orders": False,
        },
        "plain_language": (
            "每天把錯題合併成獨立事件並提出影子改善候選；正式V6繼續原本60日考試，"
            "影子層達守門條件才會受控自動升級或退版；正式V6不會因單日輸贏自動改排名、權重或下單。"
        ),
    }
    _write(reports_dir / "model_learning.json", payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reports-dir", default="reports")
    parser.add_argument("--updated-at", default="")
    args = parser.parse_args()
    report = update_model_learning(Path(args.reports_dir), updated_at=args.updated_at)
    print(
        "model learning: "
        f"{report['error_learning']['independent_events']} events / "
        f"{len(report['shadow_candidates'])} candidates"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
