from treasury_stress import attach_treasury_market, evaluate_treasury_stress


def _market(*, tlt=-0.2, ief=-0.1, nasdaq=0.3, sp500=0.2, vix=18, vix_change=-2):
    return {
        "美國長債 TLT": {"price": 90, "change_pct": tlt},
        "美國中期債 IEF": {"price": 94, "change_pct": ief},
        "美國短債 SHY": {"price": 82, "change_pct": 0},
        "美國10年期公債殖利率": {"price": 4.2, "change_pct": 0.1},
        "Nasdaq": {"price": 18000, "change_pct": nasdaq},
        "S&P 500": {"price": 5500, "change_pct": sp500},
        "VIX": {"price": vix, "change_pct": vix_change},
    }


def test_bond_selloff_alone_is_watch_not_equity_flight():
    result = evaluate_treasury_stress(_market(tlt=-1.3, ief=-0.6))
    assert result["level"] == "watch"
    assert result["equity_confirmation"] is False
    assert result["affects_formal_v6"] is False
    assert result["can_place_orders"] is False


def test_joint_bond_equity_vix_stress_is_critical():
    result = evaluate_treasury_stress(
        _market(tlt=-2.1, ief=-1.1, nasdaq=-1.8, sp500=-1.2, vix=29, vix_change=15)
    )
    assert result["level"] == "critical"
    assert result["equity_confirmation"] is True
    assert result["volatility_confirmation"] is True


def test_equities_down_bonds_up_is_safe_haven_not_bond_selloff():
    result = evaluate_treasury_stress(
        _market(tlt=1.0, ief=0.5, nasdaq=-1.4, sp500=-1.0, vix=23)
    )
    assert result["level"] == "watch"
    assert result["safe_haven_rally"] is True
    assert "避險" in result["label"]


def test_attach_treasury_market_uses_sip_rows_only():
    output = attach_treasury_market({}, {
        "TLT": {
            "us_live_price": 90.5,
            "us_live_change_pct": -1.2,
            "us_live_previous_close": 91.6,
            "us_live_source": "Alpaca SIP",
        }
    })
    assert output["美國長債 TLT"]["price"] == 90.5
    assert output["美國長債 TLT"]["change_pct"] == -1.2
    assert "美國中期債 IEF" not in output
