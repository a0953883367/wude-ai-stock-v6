from types import SimpleNamespace

from fubon_runner import _level_total, build_fubon_intraday, parse_fubon_quote


def test_parse_direct_fubon_quote_sums_five_levels():
    payload = {
        "lastPrice": 1060,
        "bids": [{"price": 1055, "size": 100}, {"price": 1050, "size": "80"}],
        "asks": [{"price": 1060, "size": 70}, {"price": 1065, "size": 30}],
    }

    result = parse_fubon_quote(payload)

    assert result["lastPrice"] == 1060
    assert result["bidPrice"] == 1055
    assert result["askPrice"] == 1060
    assert result["bidTotal"] == 180
    assert result["askTotal"] == 100
    assert result["fetchedAt"].endswith("+08:00")


def test_parse_sdk_wrapped_quote_and_model_levels():
    payload = SimpleNamespace(
        data=SimpleNamespace(
            lastPrice="88.5",
            bids=[SimpleNamespace(size=12), SimpleNamespace(size=8)],
            asks=[SimpleNamespace(size=10), SimpleNamespace(size=15)],
        )
    )

    result = parse_fubon_quote(payload)

    assert result["lastPrice"] == 88.5
    assert result["bidTotal"] == 20
    assert result["askTotal"] == 25


def test_empty_levels_do_not_masquerade_as_zero_depth():
    assert _level_total([]) is None
    assert parse_fubon_quote({"lastPrice": None, "bids": [], "asks": []}) == {}


def test_every_taiwan_symbol_gets_a_fubon_quote(monkeypatch):
    class Intraday:
        def __init__(self):
            self.quote_calls = []

        def quote(self, symbol):
            self.quote_calls.append(symbol)
            return {"lastPrice": 100, "bids": [{"size": 10}], "asks": [{"size": 8}]}

        def candles(self, **_kwargs):
            return {"data": []}

    intraday = Intraday()
    sdk = SimpleNamespace(
        marketdata=SimpleNamespace(
            rest_client=SimpleNamespace(stock=SimpleNamespace(intraday=intraday))
        )
    )
    monkeypatch.setattr("fubon_runner.yahoo_download_intraday", lambda _symbols: {})
    quotes = {}

    build_fubon_intraday(
        sdk, ["2330.TW", "6488.TWO", "NVDA"], quote_results=quotes
    )

    assert intraday.quote_calls == ["2330", "6488"]
    assert set(quotes) == {"2330.TW", "6488.TWO"}
