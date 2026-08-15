"""Temporary runtime compatibility shim for the V6.29 short/mid-long plan.

Python imports sitecustomize automatically at startup when it is importable.
This supplies the missing _tick_size name through builtins so strategy.py can
run without changing the generated trading-plan logic.
"""

import builtins


def _tick_size(row, price):
    market = str((row or {}).get("market") or "").upper()
    instrument_type = str((row or {}).get("type") or "").upper()
    price = float(price or 0.0)

    # US stocks / ETFs and Taiwan ETFs use cent-level rounding in this model.
    if market != "TW" or "ETF" in instrument_type:
        return 0.01

    # TWSE/TPEX common-stock price increments.
    if price < 10:
        return 0.01
    if price < 50:
        return 0.05
    if price < 100:
        return 0.10
    if price < 500:
        return 0.50
    if price < 1000:
        return 1.00
    return 5.00


builtins._tick_size = _tick_size
