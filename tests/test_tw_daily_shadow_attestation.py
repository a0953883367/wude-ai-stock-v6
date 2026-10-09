import copy
from datetime import datetime, timezone

import pytest

import tw_daily_shadow_attestation as attestation

FETCHED_AT = "2026-10-09T17:20:00+00:00"
NOW = datetime.fromisoformat(FETCHED_AT)


def twse_raw(**changes):
    return {"Code": "2330", "Date": "1151008", "OpeningPrice": "2,550.00",
            "HighestPrice": "2,575.00", "LowestPrice": "2,550.00",
            "ClosingPrice": "2,550.00", "TradeVolume": "23,145,193", **changes}


def tpex_raw(**changes):
    return {"SecuritiesCompanyCode": "6488", "Date": "1151008", "Open": "1180",
            "High": "1195", "Low": "1095", "Close": "1130",
            "TradingShares": "1,200,000", **changes}


def frozen(**changes):
    return {"symbol": "2330.TW", "market": "TW", "market_contract_valid": True,
            "price": 2560, "official_session_date": "2026-10-08",
            "official_open_price": 2550, "official_high_price": 2575,
            "official_low_price": 2550, "official_close_price": 2550,
            "tw_price_source": "TWSE OpenAPI", "tw_price_unit": "TWD/shares",
            "tw_official_session_date": "2026-10-08", "score": 88,
            "rank": 2, "next_session_tracks": {"one": {"vote": "up"}}, **changes}


def record(**changes):
    return attestation.parse_official_price_rows("TWSE OpenAPI", [twse_raw(**changes)], fetched_at=FETCHED_AT)["2330.TW"]


def test_exact_match_attests_missing_volume_without_replacing_quote_or_formal_fields():
    row = frozen()
    original = copy.deepcopy(row)
    raw = record()
    output = attestation.attest_frozen_tw_row(row, raw)
    assert row == original
    for key, value in row.items():
        assert output[key] == value
    assert output["price"] == 2560
    assert output["official_close_price"] == 2550
    assert output["official_volume"] == 23_145_193
    assert output["source_daily_ohlcv_complete"] is True
    assert output["source_daily_ohlcv_session_date"] == "2026-10-08"
    proof = output["shadow_daily_ohlcv_proof"]
    assert proof["status"] == "attested"
    assert proof["quote_price"] == 2560
    assert proof["ohlcv"]["close"] == 2550
    assert proof["source_url"] == attestation.SOURCES["TWSE OpenAPI"]["url"]
    assert proof["fetched_at"] == FETCHED_AT
    assert proof["raw_record"] == twse_raw()
    assert len(proof["raw_record_sha256"]) == 64
    assert len(proof["source_payload_sha256"]) == 64


@pytest.mark.parametrize("field", ["open", "high", "low", "close"])
def test_any_frozen_ohlc_mismatch_stays_blocked(field):
    row = frozen(**{f"official_{field}_price": 2551})
    output = attestation.attest_frozen_tw_row(row, record())
    assert output["source_daily_ohlcv_complete"] is False
    assert output["source_daily_ohlcv_session_date"] is None
    assert output["shadow_daily_ohlcv_proof"]["reason"] == "frozen_ohlcv_mismatch"
    assert field in output["shadow_daily_ohlcv_proof"]["mismatched_fields"]
    assert "official_volume" not in output
    assert output[f"official_{field}_price"] == 2551


@pytest.mark.parametrize("changes,reason", [
    ({"official_session_date": "2026-10-07"}, "frozen_session_mismatch"),
    ({"official_session_date": None}, "frozen_session_mismatch"),
    ({"tw_official_session_date": "2026-10-07"}, "previous_source_session_mismatch"),
    ({"tw_price_source": "Yahoo Finance"}, "previous_source_mismatch"),
    ({"tw_price_unit": "lots"}, "previous_unit_mismatch"),
    ({"official_volume": 0}, "frozen_ohlcv_mismatch"),
    ({"official_volume": True}, "frozen_ohlcv_mismatch"),
    ({"official_volume": float("nan")}, "frozen_ohlcv_mismatch"),
    ({"official_open_price": None}, "frozen_ohlcv_mismatch"),
    ({"official_close_price": 2550.00001}, "frozen_ohlcv_mismatch"),
])
def test_source_conflicts_are_not_repaired_by_relabelling(changes, reason):
    output = attestation.attest_frozen_tw_row(frozen(**changes), record())
    assert output["source_daily_ohlcv_complete"] is False
    assert output["shadow_daily_ohlcv_proof"]["reason"] == reason


@pytest.mark.parametrize("changes", [
    {"Date": "1150230"}, {"Date": ""}, {"Code": ""},
    {"OpeningPrice": "--"}, {"HighestPrice": None}, {"LowestPrice": "NaN"},
    {"ClosingPrice": 0}, {"OpeningPrice": True}, {"ClosingPrice": "Infinity"},
    {"ClosingPrice": "1e9999"}, {"HighestPrice": "2500"}, {"LowestPrice": "2600"},
    {"TradeVolume": None}, {"TradeVolume": "--"}, {"TradeVolume": "-1"},
    {"TradeVolume": False}, {"TradeVolume": "1.5"},
])
def test_incomplete_or_impossible_raw_candle_never_attests(changes):
    assert attestation.parse_official_price_rows("TWSE OpenAPI", [twse_raw(**changes)], fetched_at=FETCHED_AT) == {}


def test_real_zero_volume_is_not_replaced_or_invented():
    proof = record(TradeVolume="0")
    output = attestation.attest_frozen_tw_row(frozen(), proof)
    assert output["official_volume"] == 0
    assert output["source_daily_ohlcv_complete"] is True


def test_existing_matching_volume_is_verified():
    output = attestation.attest_frozen_tw_row(frozen(official_volume=23_145_193), record())
    assert output["source_daily_ohlcv_complete"] is True


def test_duplicate_symbols_are_ambiguous_even_when_identical():
    raw = twse_raw()
    assert attestation.parse_official_price_rows("TWSE OpenAPI", [raw, raw], fetched_at=FETCHED_AT) == {}


@pytest.mark.parametrize("source,payload,stamp", [
    ("Yahoo Finance", [twse_raw()], FETCHED_AT),
    (None, [twse_raw()], FETCHED_AT),
    ({}, [twse_raw()], FETCHED_AT),
    ("TWSE OpenAPI", {"data": [twse_raw()]}, FETCHED_AT),
    ("TWSE OpenAPI", [twse_raw()], "2026-10-09T17:20:00"),
    ("TWSE OpenAPI", [twse_raw()], "bad timestamp"),
])
def test_unsupported_provider_payload_or_unzoned_observation(source, payload, stamp):
    assert attestation.parse_official_price_rows(source, payload, fetched_at=stamp) == {}


@pytest.mark.parametrize("key,value", [
    ("source_url", "https://example.com/fake"), ("source", "Yahoo Finance"),
    ("symbol", "2330.TWO"), ("market", "US"), ("unit", "USD/shares"),
    ("interval", "1m"), ("source_session_date", "2026-10-09"),
    ("ohlcv", {"open": 2550, "high": 2575, "low": 2550, "close": 2550, "volume": 1}),
    ("raw_record_sha256", "fake"), ("raw_record", {}),
])
def test_normalized_proof_tampering_is_blocked(key, value):
    raw = record()
    raw[key] = value
    output = attestation.attest_frozen_tw_row(frozen(), raw)
    assert output["source_daily_ohlcv_complete"] is False
    assert output["shadow_daily_ohlcv_proof"]["reason"] == "invalid_official_record"


def test_false_boolean_volume_in_normalized_proof_is_blocked():
    raw = record(TradeVolume="1")
    raw["ohlcv"]["volume"] = True
    output = attestation.attest_frozen_tw_row(frozen(), raw)
    assert output["source_daily_ohlcv_complete"] is False


def test_missing_symbol_clears_prior_attestation_but_preserves_formal_prices():
    row = frozen(source_daily_ohlcv_complete=True, source_daily_ohlcv_session_date="2026-10-08")
    output = attestation.attest_frozen_tw_row(row, None)
    assert output["source_daily_ohlcv_complete"] is False
    assert output["source_daily_ohlcv_session_date"] is None
    assert output["price"] == row["price"]
    assert row["source_daily_ohlcv_complete"] is True


def test_provider_selection_uses_two_bulk_endpoints_and_never_us_or_yahoo():
    calls = []
    def fetch(url):
        calls.append(url)
        if url == attestation.SOURCES["TWSE OpenAPI"]["url"]:
            return [twse_raw()]
        if url == attestation.SOURCES["TPEx OpenAPI"]["url"]:
            return [tpex_raw()]
        raise AssertionError(url)
    tw = frozen(symbol="6488.TWO", official_open_price=1180, official_high_price=1195,
                official_low_price=1095, official_close_price=1130, price=1135,
                tw_price_source="TPEx OpenAPI")
    us = {"symbol": "AAPL", "market": "US", "price": 250, "official_volume": None}
    rows = [frozen(), tw, us]
    before = copy.deepcopy(rows)
    output, audit = attestation.enrich_frozen_tw_rows(rows, fetch_json=fetch, now=NOW)
    assert sorted(calls) == sorted(spec["url"] for spec in attestation.SOURCES.values())
    assert rows == before
    assert output[2] == us
    assert audit["attested_count"] == 2
    assert audit["tw_row_count"] == 2
    assert audit["formal_rows_rewritten"] is False
    assert output[1]["price"] == 1135
    assert output[1]["official_volume"] == 1_200_000
    assert all(source["fetched_at"] == FETCHED_AT for source in audit["sources"])


def test_failed_provider_stays_blocked_with_auditable_reason_and_no_fallback():
    calls = []
    def fetch(url):
        calls.append(url)
        raise RuntimeError("provider unavailable")
    output, audit = attestation.enrich_frozen_tw_rows([frozen()], fetch_json=fetch, now=NOW)
    assert calls == [attestation.SOURCES["TWSE OpenAPI"]["url"]]
    assert output[0]["source_daily_ohlcv_complete"] is False
    assert audit["attested_count"] == 0
    assert audit["sources"][0]["status"] == "unavailable"
    assert audit["sources"][0]["error_type"] == "RuntimeError"


def test_no_tw_symbols_means_no_network_requests():
    def fail(url):
        pytest.fail("US row must not trigger a TW or Yahoo request")
    us = {"symbol": "AAPL", "market": "US"}
    output, audit = attestation.enrich_frozen_tw_rows([us], fetch_json=fail, now=NOW)
    assert output == [us]
    assert audit["sources"] == []
    assert audit["tw_row_count"] == 0


def test_naive_clock_is_rejected_before_network():
    with pytest.raises(ValueError, match="timezone"):
        attestation.enrich_frozen_tw_rows([frozen()], now=datetime(2026, 10, 9))


def test_volume_and_candle_are_exposed_only_through_verified_shadow_copy():
    from shadow_stock_conclusion import input_evidence_categories, source_snapshot
    row = frozen()
    assert source_snapshot(row)["ohlcv_complete"] is False
    enriched = attestation.attest_frozen_tw_row(row, record())
    snapshot = source_snapshot(enriched)
    assert snapshot["ohlcv_complete"] is True
    assert snapshot["source_session_date"] == "2026-10-08"
    candle = next(c for c in input_evidence_categories(enriched) if c["id"] == "daily_candle")
    assert {item["key"]: item["value"] for item in candle["items"]}["official_volume"] == 23_145_193
    assert source_snapshot(row)["ohlcv_complete"] is False
