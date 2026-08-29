from portfolio_control import build_portfolio_control


def _decision(symbol, market="TW", industry="半導體", code="can_scale", *, risk=False, missing=False, score=80):
    return {
        "symbol": symbol, "name": symbol, "market": market, "industry": industry,
        "formal_rank": 1, "risk_blocks": ["x"] if risk else [],
        "core_data_missing": ["price"] if missing else [],
        "final": {"recommendation": code},
        "horizons": {h: {"recommendation": code, "score": score} for h in ("short", "medium", "long")},
    }


def test_portfolio_blocks_risk_and_missing_data_and_never_places_orders():
    report = build_portfolio_control([
        _decision("GOOD.TW"), _decision("RISK.TW", risk=True), _decision("MISS.TW", missing=True)
    ])
    assert "GOOD.TW" in report["by_symbol"]
    assert "RISK.TW" not in report["by_symbol"]
    assert "MISS.TW" not in report["by_symbol"]
    assert "不自動下單" in report["risk_controls"]["orders"]


def test_waiting_entry_reserves_zero_and_industry_cap_is_enforced():
    rows = [_decision(f"S{i}.TW", industry="同產業", score=90-i) for i in range(3)]
    rows.append(_decision("WAIT.TW", industry="其他", code="wait_pullback"))
    report = build_portfolio_control(rows)
    medium_same = [r for r in report["allocations"]["medium"] if r["industry"] == "同產業"]
    assert len(medium_same) == 1  # 30% of one million cannot hold two 200k names in one industry.
    waiting = next(r for r in report["allocations"]["medium"] if r["symbol"] == "WAIT.TW")
    assert waiting["suggested_twd"] == 0
