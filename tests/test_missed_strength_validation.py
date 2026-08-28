from copy import deepcopy
import gzip
import json

from missed_strength_validation import empty_state, update_state


def rows(market: str, session_date: str, *, outcome: bool = False) -> list[dict]:
    output = []
    for index in range(30):
        if outcome:
            change = 30 - index if index >= 20 else 20 - index if index >= 10 else -index
        else:
            change = 0.1
        output.append({
            "symbol": f"{market}{index:02d}",
            "name": f"{market}股票{index:02d}",
            "market": market,
            "type": "個股",
            "official_session_date": session_date,
            "official_close_price": 100 + change,
            "change_pct": change,
            "overall_display_rank": index + 1,
            "overall_rank": index + 1 if index < 20 else None,
            "overall_rank_tier": 2 if index < 20 else 1,
            "overall_ranking_score": 100 - index,
            "score": 90 - index / 2,
            "short_term_score": 75 - index / 2,
            "entry_score": 70 - index,
            "technical_score": 80 - index,
            "volume_score": 65 - index,
            "institution_score": 60 - index,
            "market_data_quality_score": 95,
            "overall_confidence": 90,
            "short_term_confidence": 88,
            "entry_data_coverage": 6,
            "entry_data_total": 6,
            "market_contract_valid": True,
            "price_plan_rank_factor": 1,
            "action": "觀察",
            "buy_candidate_status": "等待買進條件",
            "buy_candidate_reasons": ["量能不足"] if index >= 20 else [],
            "short_term_reason": "條件尚未齊全",
            "outlook_reasons": ["等待量價確認"],
            "daily_volume_ratio": 1.5 if outcome and index >= 20 else 0.8,
            "avg_volume20": 1_000_000 + index,
            "volume_price_pattern": "價漲量增" if outcome and index >= 10 else "價穩量縮",
        })
    return output


def regimes(market: str, *dates: str, label: str = "bull") -> dict:
    return {
        market: {
            date: {
                "market": market,
                "session_date": date,
                "regime": label,
                "benchmark": "加權指數" if market == "TW" else "S&P 500",
                "benchmark_close": 100,
                "ma20": 98,
                "ma60": 95,
                "return20_pct": 3,
                "ma20_slope5_pct": 1,
                "classification_rule": "past_only",
            }
            for date in dates
        }
    }


def test_intraday_and_mislabeled_preclose_report_cannot_create_result(tmp_path):
    state = empty_state()
    original = deepcopy(state)
    current = rows("TW", "2026-08-24")
    update_state(
        state, current, regimes("TW", "2026-08-24"), tmp_path,
        period="evening", updated_at="2026-08-24 11:00:00", intraday=False,
    )
    assert state == original
    update_state(
        state, current, regimes("TW", "2026-08-24"), tmp_path,
        period="evening", updated_at="2026-08-24 20:00:00", intraday=True,
    )
    assert state == original
    assert list(tmp_path.glob("*.json.gz")) == []


def test_first_close_only_freezes_and_next_close_settles_without_changing_rows(tmp_path):
    state = empty_state()
    signal_rows = rows("TW", "2026-08-21")
    untouched = deepcopy(signal_rows)
    history = regimes("TW", "2026-08-21", "2026-08-24", label="bull")
    update_state(
        state, signal_rows, history, tmp_path,
        period="evening", updated_at="2026-08-21 20:00:00",
    )
    market = state["markets"]["TW"]
    assert market["outcomes"] == []
    assert market["pending_snapshot"]["signal_session_date"] == "2026-08-21"
    assert market["pending_snapshot"]["top10"] == [f"TW{i:02d}" for i in range(10)]
    assert signal_rows == untouched

    snapshot_path = tmp_path / "TW-2026-08-21.json.gz"
    with gzip.open(snapshot_path, "rt", encoding="utf-8") as stream:
        snapshot = json.load(stream)
    frozen_hash = snapshot["integrity_sha256"]
    assert snapshot["ranking_count"] == 30
    assert len(snapshot["rows"]) == 30
    assert len(snapshot["top10"]) == 10 and len(snapshot["top20"]) == 20
    assert "judgment" in snapshot["rows"][0]
    assert "factor_scores" in snapshot["rows"][0]
    # Seeing different same-session movers later can never overwrite the file.
    altered = rows("TW", "2026-08-21", outcome=True)
    update_state(
        state, altered, history, tmp_path,
        period="evening", updated_at="2026-08-21 21:00:00",
    )
    with gzip.open(snapshot_path, "rt", encoding="utf-8") as stream:
        assert json.load(stream)["integrity_sha256"] == frozen_hash
    assert market["outcomes"] == []

    update_state(
        state, rows("TW", "2026-08-24", outcome=True), history, tmp_path,
        period="evening", updated_at="2026-08-24 20:00:00",
    )
    outcome = market["outcomes"][0]
    assert outcome["signal_session_date"] == "2026-08-21"
    assert outcome["outcome_session_date"] == "2026-08-24"
    assert outcome["status"] == "valid"
    assert outcome["top10"]["captured_count"] == 0
    assert outcome["top10"]["capture_rate_pct"] == 0
    assert outcome["top20"]["captured_count"] == 10
    assert outcome["top20"]["capture_rate_pct"] == 50
    assert market["pending_snapshot"]["signal_session_date"] == "2026-08-24"


def test_missed_stock_keeps_frozen_rank_score_reason_and_factor_pressure(tmp_path):
    state = empty_state()
    signal = rows("TW", "2026-08-21")
    signal[20]["volume_score"] = 25
    signal[20]["buy_candidate_reasons"] = ["成交量過低", "技術結構未達候選門檻"]
    history = regimes("TW", "2026-08-21", "2026-08-24", label="sideways")
    update_state(
        state, signal, history, tmp_path,
        period="evening", updated_at="2026-08-21 20:00:00",
    )
    update_state(
        state, rows("TW", "2026-08-24", outcome=True), history, tmp_path,
        period="evening", updated_at="2026-08-24 20:00:00",
    )
    missed = next(item for item in state["markets"]["TW"]["outcomes"][0]["actual_top20"] if item["symbol"] == "TW20")
    assert missed["error_type"] == "model_judgment_error"
    assert missed["prior"]["display_rank"] == 21
    assert missed["prior"]["ranking_score"] == 80
    assert missed["prior"]["judgment"]["buy_candidate_reasons"] == ["成交量過低", "技術結構未達候選門檻"]
    assert any(item["key"] == "volume_score" and item["value"] == 25 for item in missed["prior"]["pressure_factors"])
    assert missed["actual_return_pct"] == 10
    assert missed["volume_ratio"] == 1.5
    assert missed["volume_price_result"] == "價漲量增"


def test_data_incomplete_and_model_judgment_are_separated(tmp_path):
    state = empty_state()
    signal = rows("TW", "2026-08-21")
    signal[20]["market_data_quality_score"] = 40
    signal[20]["entry_data_coverage"] = 4
    history = regimes("TW", "2026-08-21", "2026-08-24")
    update_state(
        state, signal, history, tmp_path,
        period="evening", updated_at="2026-08-21 20:00:00",
    )
    update_state(
        state, rows("TW", "2026-08-24", outcome=True), history, tmp_path,
        period="evening", updated_at="2026-08-24 20:00:00",
    )
    actual = state["markets"]["TW"]["outcomes"][0]["actual_top20"]
    incomplete = next(item for item in actual if item["symbol"] == "TW20")
    judgment = next(item for item in actual if item["symbol"] == "TW21")
    assert incomplete["error_type"] == "data_incomplete"
    assert "市場資料品質不足50" in incomplete["prior"]["incomplete_reasons"]
    assert judgment["error_type"] == "model_judgment_error"
    summary = state["markets"]["TW"]["summary"]["overall"]
    assert summary["data_incomplete_misses"] == 1
    assert summary["model_judgment_misses"] >= 1


def test_incomplete_stock_captured_by_frozen_top20_is_not_counted_as_top20_miss(tmp_path):
    state = empty_state()
    signal = rows("TW", "2026-08-21")
    # TW10 is in the frozen TOP20 and in the following session's actual TOP20.
    signal[10]["market_data_quality_score"] = 40
    signal[10]["entry_data_coverage"] = 4
    history = regimes("TW", "2026-08-21", "2026-08-24")
    update_state(
        state, signal, history, tmp_path,
        period="evening", updated_at="2026-08-21 20:00:00",
    )
    update_state(
        state, rows("TW", "2026-08-24", outcome=True), history, tmp_path,
        period="evening", updated_at="2026-08-24 20:00:00",
    )

    outcome = state["markets"]["TW"]["outcomes"][0]
    captured = next(item for item in outcome["actual_top20"] if item["symbol"] == "TW10")
    assert captured["captured_by_matching_top20"] is True
    assert captured["error_type"] == "captured"
    assert outcome["missed_data_incomplete_count"] == 0
    assert outcome["top10_missed_data_incomplete_count"] >= 1


def test_tampered_snapshot_is_quarantined_and_never_counted(tmp_path):
    state = empty_state()
    history = regimes("US", "2026-08-21", "2026-08-24", label="bear")
    update_state(
        state, rows("US", "2026-08-21"), history, tmp_path,
        period="morning", updated_at="2026-08-22 06:00:00",
    )
    path = tmp_path / "US-2026-08-21.json.gz"
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        snapshot = json.load(stream)
    snapshot["top10"][0] = "TAMPERED"
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        json.dump(snapshot, stream)
    update_state(
        state, rows("US", "2026-08-24", outcome=True), history, tmp_path,
        period="morning", updated_at="2026-08-25 06:00:00",
    )
    market = state["markets"]["US"]
    assert market["outcomes"] == []
    assert market["quarantines"][-1]["reason"] == "snapshot_hash_mismatch"


def test_incomplete_official_close_coverage_is_not_hard_calculated(tmp_path):
    state = empty_state()
    history = regimes("TW", "2026-08-21", "2026-08-24")
    update_state(
        state, rows("TW", "2026-08-21"), history, tmp_path,
        period="evening", updated_at="2026-08-21 20:00:00",
    )
    update_state(
        state, rows("TW", "2026-08-24", outcome=True)[:15], history, tmp_path,
        period="evening", updated_at="2026-08-24 20:00:00",
    )
    outcome = state["markets"]["TW"]["outcomes"][0]
    assert outcome["status"] == "data_insufficient"
    assert outcome["actual_top20"] == []
    assert state["markets"]["TW"]["summary"]["overall"]["valid_sessions"] == 0
    assert state["markets"]["TW"]["summary"]["overall"]["invalid_sessions"] == 1


def test_regime_statistics_and_20_day_common_feature_gate(tmp_path):
    state = empty_state()
    dates = [f"2026-09-{day:02d}" for day in range(1, 22)]
    history = regimes("TW", *dates, label="sideways")
    for index, date in enumerate(dates):
        signal = rows("TW", date, outcome=index > 0)
        for row in signal[20:]:
            row["volume_score"] = 20
        update_state(
            state, signal, history, tmp_path,
            period="evening", updated_at=f"{date} 20:00:00",
        )
    summary = state["markets"]["TW"]["summary"]
    assert summary["overall"]["valid_sessions"] == 20
    assert summary["regimes"]["sideways"]["valid_sessions"] == 20
    assert summary["regimes"]["bull"]["valid_sessions"] == 0
    assert summary["review_gate"]["status"] == "preliminary_review"
    assert summary["review_gate"]["formal_ranking_locked"] is True
    assert any(item["key"] == "volume_score" for item in summary["common_missed_features"])
    assert state["policy"]["formal_v6_modified"] is False
    assert state["policy"]["top10_logic_modified"] is False
    assert state["policy"]["sixty_day_gate_modified"] is False
    assert state["policy"]["automatic_merge"] is False
    assert state["policy"]["broker_orders"] is False
