"""Complete, auditable learning contracts for every model-facing component.

This module does not train or promote production models.  It inventories the
forecast, evidence, execution and governance layers, attaches each component
to an existing forward-only evidence source, and states exactly what may be
learned and how a future shadow challenger must be judged.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from model_lab import MODEL_NAMES
from prediction_engine.models import HORIZONS, MIN_TRAINING_SAMPLES, MIN_TRAINING_SESSIONS


SCHEMA_VERSION = 1
LAYER_LABELS = {
    "forecast": "預測與排名",
    "evidence": "證據與資料",
    "execution": "交易與風險",
    "governance": "驗證與治理",
}
COHORTS = ("TW_STOCK", "TW_ETF", "US_STOCK", "US_ETF")
TRACKS = ("overnight", "session", "full_day")

MODEL_LABELS = {
    "balanced_next": "綜合平衡",
    "price_trend": "價格趨勢",
    "volume_confirmation": "量能確認",
    "market_flow": "市場資金流",
    "macro_risk": "大盤風險",
    "exhaustion_guard": "過熱防護",
    "mean_reversion": "均值回歸",
    "momentum_confirmed": "動能確認",
    "relative_strength": "相對強弱",
    "entry_timing": "進場時機",
}

MODEL_LEARNING = {
    "balanced_next": "學習不同盤勢下趨勢、量能、資金、新聞與風險證據的相對可信度",
    "price_trend": "學習趨勢延續、假突破與跌破止穩的分界",
    "volume_confirmation": "學習相對量、攻擊量與價格確認的有效門檻",
    "market_flow": "學習資金方向、持續時間及價量背離何時有效",
    "macro_risk": "學習大盤風險何時應壓低個股、何時允許個股獨強",
    "exhaustion_guard": "學習 RSI、跳空、爆量及上影造成追高失敗的條件",
    "mean_reversion": "學習真正止穩反彈與持續落刀的差異",
    "momentum_confirmed": "學習突破、量能、資金及族群共振可延續的組合",
    "relative_strength": "學習相對大盤與族群領先的延續及衰退",
    "entry_timing": "學習開盤後確認、可買區、滑價及放棄進場條件",
}


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def _spec(
    model_id: str,
    label: str,
    layer: str,
    role: str,
    learning_mode: str,
    learns: str,
    target: str,
    metrics: list[str],
    sources: list[str],
    *,
    segments: list[str] | None = None,
    minimum_sessions: int = 60,
    minimum_samples: int = 100,
    progress_tag: str = "shared",
    dedicated_validation: bool = False,
) -> dict[str, Any]:
    return {
        "model_id": model_id,
        "label": label,
        "layer": layer,
        "role": role,
        "learning_mode": learning_mode,
        "learns": learns,
        "outcome_target": target,
        "metrics": metrics,
        "source_reports": sources,
        "segments": segments or list(COHORTS),
        "minimum_sessions": minimum_sessions,
        "minimum_samples": minimum_samples,
        "progress_tag": progress_tag,
        "dedicated_validation": dedicated_validation,
    }


def _catalog_specs() -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for cohort, label in (
        ("TW_STOCK", "正式V6台股個股"),
        ("TW_ETF", "正式V6台灣ETF"),
        ("US_STOCK", "正式V6美股個股"),
        ("US_ETF", "正式V6美國ETF"),
    ):
        specs.append(_spec(
            f"formal_v6_{cohort.lower()}", label, "forecast", "production_baseline",
            "frozen_baseline", "只當固定比較基準；從錯誤找影子候選，不自行改權重",
            "1／2／3／5日方向與實際報酬", ["命中率", "平均報酬", "最大回撤", "覆蓋率"],
            ["performance.json"], segments=[cohort], progress_tag=f"cohort:{cohort}",
            dedicated_validation=True,
        ))

    for name in MODEL_NAMES:
        specs.append(_spec(
            f"next_session_{name}", f"隔日子模型・{MODEL_LABELS[name]}", "forecast",
            "shadow_submodel", "threshold_challenger", MODEL_LEARNING[name],
            "隔夜、盤中及全天方向", ["分段命中率", "平均報酬", "Brier校準", "覆蓋率", "最差報酬"],
            ["performance.json", "prediction_history.json"], segments=list(COHORTS) + list(TRACKS),
            minimum_sessions=60, minimum_samples=200, progress_tag=f"lab:{name}",
            dedicated_validation=True,
        ))

    for code, horizon in HORIZONS.items():
        side = "上漲" if horizon["side"] == "UP" else "下跌"
        specs.append(_spec(
            f"horizon_{code.lower()}", f"多期間引擎・{horizon['label']}", "forecast",
            "champion_challenger", "coefficient_challenger",
            f"分市場與資產類型學習{horizon['label']}的{side}機率、幅度與可買性",
            f"{horizon['sessions']}個交易日後實際報酬", ["樣本外方向命中", "MAE", "Brier校準", "扣成本報酬"],
            ["prediction_engine.json"], segments=list(COHORTS),
            minimum_sessions=MIN_TRAINING_SESSIONS[code], minimum_samples=MIN_TRAINING_SAMPLES[code],
            progress_tag=f"horizon:{code}", dedicated_validation=True,
        ))

    specs.extend([
        _spec("comprehensive_shadow", "台美綜合影子排名", "forecast", "shadow_ranker", "meta_challenger",
              "學習不同期間應採信哪些影子證據，並避免相關模型重複投票", "同期間正式基準與影子排名報酬差",
              ["TOP-K超額報酬", "名次穩定度", "最大回撤", "證據覆蓋"],
              ["comprehensive_shadow_ranking.json", "comprehensive_shadow_history.json"],
              progress_tag="comprehensive", dedicated_validation=True),
        _spec("central_decision", "中央AI決策中樞", "forecast", "meta_controller", "gating_challenger",
              "學習各盤勢、期間、群組與資料品質下該相信哪一個模型，以及何時棄權",
              "中央唯一答案的實際方向、報酬與風險", ["校準後機率", "扣成本超額報酬", "錯誤衝突率", "棄權品質"],
              ["decision_hub.json", "performance.json"], progress_tag="shared", dedicated_validation=False),
    ])

    evidence_specs = [
        ("technical_kline", "技術趨勢／K線／頭肩", "threshold_calibration", "學習均線、突破、跌破、K線與型態在各盤勢的有效門檻", "prediction_history.json"),
        ("volume_attack", "量能／分時量／攻擊量", "threshold_calibration", "學習相對量與攻擊量需達何種強度及持續時間", "prediction_history.json"),
        ("capital_flow", "大量買賣／資金流", "threshold_calibration", "學習資金方向、持續性、可信度與價格同步條件", "capital_flow_daily.json"),
        ("tw_institution", "台股三大法人", "weight_challenger", "學習外資、投信、自營商在不同產業與盤勢的有效權重", "tw_weight_experiment.json"),
        ("tw_credit_broker", "融資融券／借券／券商分點", "threshold_calibration", "學習籌碼壓力、軋空、分點連續性及反轉風險", "performance.json"),
        ("tw_accumulation", "台股法人累積", "weight_challenger", "學習強度、連續性、吸收、穩定與量縮的組合", "performance.json"),
        ("sector_relative_strength", "族群／相對強弱", "threshold_calibration", "學習個股相對族群與大盤的領先是否延續", "missed_strength_validation.json"),
        ("market_rotation", "市場規則／族群輪動", "stage_challenger", "學習點火、擴散、高潮、退潮及領頭股更換", "market_rotation_shadow.json"),
        ("macro_regime", "大盤／總經狀態", "regime_calibration", "學習多頭、空頭、盤整與高波動的分類及其模型可信度", "macro_history.json"),
        ("news_event", "新聞／公告／突發事件", "decay_calibration", "學習發布前可得訊息的方向、驚訝程度與影響衰減", "news_risk_cache.json"),
        ("fundamental_growth_quality", "基本面／成長／財務品質", "horizon_calibration", "學習營收、獲利、現金流與財務品質對45日及6個月的作用", "performance.json"),
        ("valuation", "估值風險雷達", "bucket_challenger", "學習估值壓力分桶與後續中長期報酬、回撤的關係", "valuation_risk_shadow.json"),
        ("etf_structure", "ETF成分／廣度／折溢價／追蹤／費用", "asset_specific_challenger", "學習ETF專屬結構，禁止直接套用公司個股規則", "etf_metadata_cache.json"),
        ("data_quality", "資料品質／日期／來源契約", "source_reliability", "學習各來源的延遲、缺值、錯值與市場別可靠度，但不猜股價", "system_guard.json"),
    ]
    for model_id, label, mode, learns, source in evidence_specs:
        specs.append(_spec(
            model_id, label, "evidence", "evidence_provider", mode, learns,
            "事前證據分桶對未來結果的穩定關係", ["涵蓋率", "資訊係數", "分桶報酬", "漂移", "缺值率"],
            [source, "prediction_history.json"], progress_tag=(
                "rotation" if model_id == "market_rotation" else
                "valuation" if model_id == "valuation" else
                "weight" if model_id == "tw_institution" else
                "shared"
            ), dedicated_validation=model_id in {"market_rotation", "valuation", "tw_institution", "sector_relative_strength"},
        ))

    execution_specs = [
        ("entry_confirmation", "建議買入價／15與30分鐘確認", "entry_challenger", "學習預判正確後何時真正可以進場", "performance.json", "shared", 60),
        ("trade_safety", "追高／接刀／交易安全防護", "risk_threshold_challenger", "學習哪些極端條件應降可買性或棄權", "performance.json", "shared", 60),
        ("exit_horizon", "1／2／3／5日出場", "exit_policy_challenger", "學習不同訊號與盤勢最合理的退出日", "exit_horizon_experiment.json", "exit", 60),
        ("holding_45d", "45日持有模型", "holding_policy_validation", "學習中期持有、停損與大盤基準差", "holding_simulation.json", "holding:medium", 45),
        ("holding_126d", "6個月持有模型", "holding_policy_validation", "學習長期持有、基本面與估值風險", "holding_simulation.json", "holding:long", 126),
        ("inverse_etf", "反向ETF專屬模型", "product_specific_challenger", "學習實際ETF價格、每日重置、波動耗損與持有天數", "inverse_etf_shadow.json", "inverse_etf", 20),
        ("inverse_abc", "A做多／B現金／C反向ETF", "strategy_challenger", "學習空頭期間持有現金或反向ETF何者較佳", "inverse_experiment.json", "inverse_experiment", 20),
        ("portfolio_control", "資金配置／相關性／部位控制", "portfolio_policy_challenger", "學習集中度、同族群曝險、現金比例與最大回撤", "decision_hub.json", "shared", 60),
        ("million_simulation", "60日百萬試走", "portfolio_validation", "學習排名組、嚴格買進組及大盤基準的實際差異", "million_simulation.json", "million", 60),
    ]
    for model_id, label, mode, learns, source, progress, sessions in execution_specs:
        specs.append(_spec(
            model_id, label, "execution", "execution_or_risk", mode, learns,
            "含手續費、稅、滑價後的報酬與回撤", ["淨報酬", "最大回撤", "成交覆蓋", "滑價", "盈虧比"],
            [source], minimum_sessions=sessions, minimum_samples=20,
            progress_tag=progress, dedicated_validation=True,
        ))

    governance_specs = [
        ("historical_lab", "歷史資料代理驗證", "proxy_validation", "只檢查策略結構與極端盤勢，不取代真實前向驗證", "historical_lab.json", "historical"),
        ("missed_strength", "強勢股漏選驗證", "error_discovery", "學習哪些完整資料股票被模型錯誤壓低", "missed_strength_validation.json", "missed"),
        ("error_learning", "錯題事件與原因診斷", "event_diagnosis", "將重疊錯誤合併成事件並提出可驗證假說", "performance.json", "shared"),
        ("validation_60d", "60日前向驗證", "monitor_only", "守住同版本、同日期及不回填規則", "validation_60d.json", "monitor"),
        ("model_graduation", "模型畢業控制器", "manual_gate", "檢查樣本、期間、品質及樣本外績效是否達標", "model_graduation.json", "monitor"),
        ("system_guard", "系統值班員／監控員", "monitor_only", "監控資料、排程、漂移、停滯及隔離政策", "system_guard.json", "monitor"),
    ]
    for model_id, label, mode, learns, source, progress in governance_specs:
        specs.append(_spec(
            model_id, label, "governance", "governance_guard", mode, learns,
            "系統完整、可稽核且未越過安全邊界", ["完整率", "異常數", "停滯日", "版本一致性"],
            [source], minimum_sessions=0, minimum_samples=0, progress_tag=progress,
            dedicated_validation=True,
        ))
    return specs


def _metric(performance: dict[str, Any], cohort: str, model: str | None = None) -> dict[str, Any]:
    group = (performance.get("groups") or {}).get(cohort) or {}
    if model:
        return ((((group.get("models") or {}).get(model) or {}).get("tracks") or {}).get("full_day") or {})
    return ((group.get("horizons") or {}).get("1") or {})


def _progress(tag: str, reports: dict[str, dict[str, Any]]) -> dict[str, Any]:
    performance = reports["performance.json"]
    days = int((performance.get("calibration") or {}).get("trading_days_collected") or 0)
    if tag.startswith("cohort:"):
        metric = _metric(performance, tag.split(":", 1)[1])
        return {"sessions": days, "samples": int(metric.get("samples") or 0), "detail": metric}
    if tag.startswith("lab:"):
        name = tag.split(":", 1)[1]
        metrics = [_metric(performance, cohort, name) for cohort in COHORTS]
        return {"sessions": days, "samples": sum(int(row.get("samples") or 0) for row in metrics), "by_cohort": dict(zip(COHORTS, metrics))}
    if tag.startswith("horizon:"):
        code = tag.split(":", 1)[1]
        learning = reports["prediction_engine.json"].get("learning") or {}
        rows = [((learning.get(cohort) or {}).get(code) or {}) for cohort in COHORTS]
        session_values = [int(row.get("session_count") or 0) for row in rows]
        return {"sessions": min(session_values) if session_values else 0, "samples": sum(int(row.get("sample_count") or 0) for row in rows), "streams": len(rows)}
    if tag == "comprehensive":
        days_by_market = reports["comprehensive_shadow_history.json"].get("valid_trading_days") or {}
        values = [int(days_by_market.get(market) or 0) for market in ("TW", "US")]
        return {"sessions": min(values) if values else 0, "samples": sum(values), "by_market": days_by_market}
    if tag == "rotation":
        markets = reports["market_rotation_shadow.json"].get("markets") or {}
        values = [int(((markets.get(market) or {}).get("summary") or {}).get("valid_trading_days") or len((markets.get(market) or {}).get("snapshots") or [])) for market in ("TW", "US")]
        return {"sessions": min(values) if values else 0, "samples": sum(values)}
    if tag == "valuation":
        validation = reports["valuation_risk_shadow.json"].get("validation") or {}
        sessions = [int((validation.get(market) or {}).get("effective_sessions") or 0) for market in ("TW", "US")]
        samples = sum(int((validation.get(market) or {}).get("effective_samples") or 0) for market in ("TW", "US"))
        return {"sessions": min(sessions) if sessions else 0, "samples": samples}
    if tag == "weight":
        report = reports["tw_weight_experiment.json"]
        return {"sessions": int(report.get("completed_days") or 0), "samples": int(report.get("completed_cycles") or 0)}
    if tag == "inverse_etf":
        markets = reports["inverse_etf_shadow.json"].get("markets") or {}
        samples = sum(int(summary.get("samples") or 0) for market in ("TW", "US") for summary in ((markets.get(market) or {}).get("summary") or {}).values() if isinstance(summary, dict))
        return {"sessions": min(20, samples), "samples": samples}
    if tag == "inverse_experiment":
        markets = reports["inverse_experiment.json"].get("markets") or {}
        samples = sum(len((markets.get(market) or {}).get("cohorts") or []) for market in ("TW", "US"))
        return {"sessions": min(20, samples), "samples": samples}
    if tag == "exit":
        markets = reports["exit_horizon_experiment.json"].get("markets") or {}
        metrics = [row for market in ("TW", "US") for row in ((markets.get(market) or {}).get("horizons") or {}).values() if isinstance(row, dict)]
        return {"sessions": days, "samples": sum(int(row.get("samples") or 0) for row in metrics)}
    if tag.startswith("holding:"):
        section = tag.split(":", 1)[1]
        report = reports["holding_simulation.json"].get(section) or {}
        books = list(report.values()) if isinstance(report, dict) and section == "medium" else [report]
        completed = [int((book or {}).get("completed_days") or (book or {}).get("elapsed_trading_days") or 0) for book in books if isinstance(book, dict)]
        return {"sessions": min(completed) if completed else 0, "samples": len([value for value in completed if value > 0])}
    if tag == "million":
        markets = reports["million_simulation.json"].get("markets") or {}
        completed = [int((markets.get(market) or {}).get("completed_days") or 0) for market in ("TW", "US")]
        return {"sessions": min(completed) if completed else 0, "samples": sum(completed)}
    if tag == "missed":
        markets = reports["missed_strength_validation.json"].get("markets") or {}
        overall = [(((markets.get(market) or {}).get("summary") or {}).get("overall") or {}) for market in ("TW", "US")]
        return {"sessions": min([int(row.get("valid_sessions") or 0) for row in overall] or [0]), "samples": sum(int(row.get("model_judgment_misses") or 0) for row in overall)}
    if tag == "historical":
        markets = reports["historical_lab.json"].get("markets") or {}
        return {"sessions": 0, "samples": sum(int((row or {}).get("loaded_symbols") or 0) for row in markets.values() if isinstance(row, dict))}
    if tag == "monitor":
        return {"sessions": days, "samples": 0}
    samples = sum(int(_metric(performance, cohort).get("samples") or 0) for cohort in COHORTS)
    return {"sessions": days, "samples": samples}


def build_complete_learning_catalog(reports_dir: Path) -> dict[str, Any]:
    reports_dir = Path(reports_dir)
    filenames = {
        source for spec in _catalog_specs() for source in spec["source_reports"]
    } | {"performance.json", "prediction_engine.json"}
    reports = {filename: _read(reports_dir / filename) for filename in filenames}
    units = []
    for spec in _catalog_specs():
        progress = _progress(spec["progress_tag"], reports)
        source_available = any(bool(reports.get(name)) for name in spec["source_reports"])
        if spec["learning_mode"] == "frozen_baseline":
            stage = "frozen_baseline"
        elif spec["learning_mode"] in {"monitor_only", "manual_gate"}:
            stage = "monitoring"
        elif not source_available:
            stage = "waiting_source"
        elif int(progress.get("sessions") or 0) < spec["minimum_sessions"] or int(progress.get("samples") or 0) < spec["minimum_samples"]:
            stage = "collecting"
        else:
            stage = "manual_review_available"
        units.append({
            **{key: value for key, value in spec.items() if key != "progress_tag"},
            "source_available": source_available,
            "connected_to_learning_governance": True,
            "progress": progress,
            "stage": stage,
            "uses_future_data": False,
            "changes_formal_v6": False,
            "automatic_promotion": False,
            "broker_orders": False,
        })

    layer_counts = Counter(item["layer"] for item in units)
    mode_counts = Counter(item["learning_mode"] for item in units)
    stage_counts = Counter(item["stage"] for item in units)
    source_ready = sum(bool(item["source_available"]) for item in units)
    dedicated = sum(bool(item["dedicated_validation"]) for item in units)
    gaps = []
    if source_ready < len(units):
        gaps.append(f"{len(units) - source_ready}個單元等待來源報表")
    if dedicated < len(units):
        gaps.append(f"{len(units) - dedicated}個單元目前共用前向帳本，尚待累積到可建立專屬績效")
    return {
        "schema_version": SCHEMA_VERSION,
        "inventory_complete": True,
        "unit_definition": "包含可預測模型、證據模型、交易風控與治理單元；不是把每個欄位冒充獨立AI",
        "summary": {
            "registered_units": len(units),
            "connected_units": len(units),
            "source_ready_units": source_ready,
            "dedicated_validation_units": dedicated,
            "learning_governance_coverage_pct": 100.0,
            "by_layer": {key: layer_counts.get(key, 0) for key in LAYER_LABELS},
            "by_mode": dict(sorted(mode_counts.items())),
            "by_stage": dict(sorted(stage_counts.items())),
        },
        "layers": [
            {
                "layer": key,
                "label": label,
                "units": [item for item in units if item["layer"] == key],
            }
            for key, label in LAYER_LABELS.items()
        ],
        "remaining_gaps": gaps,
        "shared_rules": {
            "point_in_time_only": True,
            "markets_and_asset_types_separate": True,
            "horizons_separate": True,
            "overlapping_outcomes_are_not_independent_events": True,
            "twenty_days_preliminary_only": True,
            "sixty_days_manual_review_only": True,
            "long_126d_requires_126_mature_sessions": True,
            "probability_calibration_required": True,
            "cost_and_drawdown_required": True,
            "formal_v6_frozen": True,
            "automatic_promotion": False,
            "automatic_merge": False,
            "broker_orders": False,
        },
    }
