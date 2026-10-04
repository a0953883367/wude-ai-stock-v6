from copy import deepcopy
import json

from weight_price_evidence import prepare_rows, evidence_valid, audit_model, compare_horizons
from weight_experiment import _price_snapshot, _pending_readiness, update_weight_experiment


class Calendar:
    def __init__(self, available=True, closed=False, complete=True):
        self.available, self.closed, self.complete = available, closed, complete
    def session_status(self, market, date):
        return {"available": self.available, "is_session": not self.closed}
    def session_complete(self, market, date, at_epoch=None):
        return self.complete


def prepared(calendar=None):
    rows = [{"symbol": "2330.TW", "market": "TW", "type": "股票", "official_session_date": "2026-10-02",
             "tw_official_session_date": "2026-10-02", "tw_official_price_available": True,
             "official_open_price": 999, "official_close_price": 888}]
    prices = {"2330": {"date": "2026-10-02", "open": 100, "close": 102,
        "tw_price_source": "TWSE OpenAPI", "tw_price_unit": "TWD/shares", "tw_official_price_available": True}}
    original = deepcopy(rows)
    safe = prepare_rows(rows, prices, calendar or Calendar(), "2026-10-02 20:00:00")
    assert rows == original
    return safe[0]


def test_price_snapshot_uses_exchange_bar_not_mislabeled_feature_prices():
    row = prepared()
    assert _price_snapshot(row) == ("2026-10-02", 100, 102)
    row["shadow_price_evidence"]["close"] = 999
    assert _price_snapshot(row) == ("", 0, 0)


def test_calendar_missing_holiday_and_preclose_fail_closed():
    for calendar in (Calendar(available=False), Calendar(closed=True), Calendar(complete=False)):
        assert _price_snapshot(prepared(calendar)) == ("", 0, 0)


def test_missing_official_snapshot_never_falls_back_to_daily_history():
    row = prepared()
    safe = prepare_rows([row], {}, Calendar(), "2026-10-02 20:00:00")[0]
    assert _price_snapshot(safe) == ("", 0, 0)


def test_expected_execution_date_never_moves_to_later_available_date():
    row = prepared()
    pending = {"signal_session_date": "2026-09-29", "expected_execution_session_date": "2026-09-30",
               "picks": [{"symbol": row["symbol"]} for _ in range(10)]}
    result = _pending_readiness(pending, [row])
    assert result["settlement_status"] != "ready"
    assert result["execution_session_date"] == "2026-09-30"


def test_legacy_without_evidence_is_not_a_verified_zero_profit():
    model = {"days": [{"session_date": "2026-10-02", "positions": [{"symbol": "2330.TW", "data_available": True,
               "gross_profit_twd": -100, "net_profit_twd": -200}], "gross_profit_twd": -100, "net_profit_twd": -200}]}
    before = deepcopy(model)
    audit = audit_model(model)
    assert model == before
    assert audit["verified_portfolio_net_profit_twd"] is None
    assert audit["verified_subset_gross_profit_twd"] is None
    assert audit["promotion_allowed"] is False


def test_partial_horizons_do_not_claim_benchmark_or_use_future_price():
    days = [{"session_date": "2026-10-01", "positions": [{"symbol": "2330.TW", "data_available": True}]}]
    bars = {"2330.TW": {"2026-10-01": {"open": 100, "close": 101, "source": "TWSE", "response_sha256": "hash"}}}
    comparison = compare_horizons(days, bars, {"sessions": ["2026-10-01", "2026-10-02"], "completed_through": "2026-10-01"})
    assert comparison["horizons"]["1"]["average_gross_return_pct"] == 1
    assert comparison["horizons"]["1"]["excess_return_pct"] is None
    assert comparison["horizons"]["5"]["average_gross_return_pct"] is None
    assert comparison["horizons"]["5"]["blocked"][0]["reason"] == "not_matured"


def test_production_update_preserves_every_legacy_observation(tmp_path):
    from tests.test_weight_experiment import universe
    from weight_experiment import empty_state, update_state
    state = empty_state()
    update_state(state, universe(), period="evening", updated_at="signal")
    update_state(state, universe("2026-08-24"), period="evening", updated_at="first")
    for m in state["models"].values():
        m["days"][0]["positions"][0]["data_available"] = False
    before = {k: deepcopy(v["days"]) for k,v in state["models"].items()}
    (tmp_path / "tw_weight_experiment.json").write_text(json.dumps(state))
    update_weight_experiment(tmp_path, [], period="morning", updated_at="2026-10-04 06:00:00", official_prices={})
    after = json.loads((tmp_path / "tw_weight_experiment.json").read_text())
    assert {k:v["days"] for k,v in after["models"].items()} == before
