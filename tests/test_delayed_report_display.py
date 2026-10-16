"""Offline contract checks for presentation-only delayed US data."""
import ast
from pathlib import Path

from market_models import enforce_market_contract, assess_market_data_quality

ROOT = Path(__file__).resolve().parents[1]


def test_delayed_data_preserved_only_in_us_market():
    row = {"market": "US", "price": 101, "score": 62,
           "us_live_data_available": False, "us_delayed_data_available": True,
           "us_delayed_price": 999, "us_delayed_asof": "2026-10-16T14:00:00Z"}
    result = enforce_market_contract(dict(row))
    assert result["price"] == 101 and result["score"] == 62
    assert result["us_delayed_price"] == 999
    tw = enforce_market_contract({**row, "market": "TW"})
    assert not any(key.startswith("us_delayed_") for key in tw)


def test_delayed_has_no_live_coverage_credit():
    base = {"market": "US", "price": 101, "us_live_data_available": False}
    a = assess_market_data_quality(dict(base))
    b = assess_market_data_quality({**base, "us_delayed_data_available": True,
                                    "us_delayed_price": 999})
    assert a["market_data_quality_score"] == b["market_data_quality_score"]


def test_briefing_merge_strips_private_bars():
    tree = ast.parse((ROOT / "briefing.py").read_text())
    merges = [node for node in ast.walk(tree) if isinstance(node, ast.DictComp)
              and 'us_delayed_bars' in ast.unparse(node)]
    assert len(merges) == 1
    merged = eval(compile(ast.Expression(merges[0]), "<merge>", "eval"), {}, {
        "us_live": {"AAPL": {"us_delayed_bars": [1], "us_delayed_price": 55,
                             "us_live_data_available": False}}, "symbol": "AAPL"})
    assert merged == {"us_delayed_price": 55, "us_live_data_available": False}
