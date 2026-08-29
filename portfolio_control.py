"""Portfolio and position-capacity layer for Central Decision Hub outputs."""

from __future__ import annotations

from collections import defaultdict
from typing import Any


POLICY = {
    "short": {"capital_twd": {"TW": 1_000_000, "US": 1_000_000}, "max_pick_twd": 50_000, "max_picks": 10, "same_industry_max_pct": 30},
    "medium": {"capital_twd": {"TW": 1_000_000, "US": 1_000_000}, "max_pick_twd": 200_000, "max_picks": 5, "same_industry_max_pct": 30},
    "long": {"capital_twd": {"ALL": 1_000_000}, "max_pick_twd": 500_000, "max_picks": 2, "same_industry_max_pct": 50},
}


def build_portfolio_control(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    allocations: dict[str, list[dict[str, Any]]] = {"short": [], "medium": [], "long": []}
    by_symbol: dict[str, dict[str, Any]] = defaultdict(dict)
    for horizon in ("short", "medium"):
        for market in ("TW", "US"):
            candidates = [
                item for item in decisions
                if item.get("market") == market
                and not item.get("core_data_missing")
                and not item.get("risk_blocks")
                and (item.get("final") or {}).get("recommendation") in {"can_scale", "wait_pullback"}
                and (item.get("horizons", {}).get(horizon) or {}).get("recommendation")
                in {"can_scale", "wait_pullback"}
            ]
            candidates.sort(key=lambda x: (
                (x.get("horizons", {}).get(horizon) or {}).get("recommendation") != "can_scale",
                -float((x.get("horizons", {}).get(horizon) or {}).get("score") or 0),
                x.get("formal_rank") or 999999,
            ))
            industry_exposure: dict[str, int] = defaultdict(int)
            market_capital = POLICY[horizon]["capital_twd"][market]
            industry_cap = round(market_capital * POLICY[horizon]["same_industry_max_pct"] / 100)
            for item in candidates:
                if len([row for row in allocations[horizon] if row["market"] == market]) >= POLICY[horizon]["max_picks"]:
                    break
                horizon_code = item["horizons"][horizon]["recommendation"]
                code = (
                    "can_scale"
                    if horizon_code == "can_scale"
                    and (item.get("final") or {}).get("recommendation") == "can_scale"
                    else "wait_pullback"
                )
                industry = str(item.get("industry") or "未分類")
                amount = POLICY[horizon]["max_pick_twd"] if code == "can_scale" else 0
                if amount and industry_exposure[industry] + amount > industry_cap:
                    continue
                row = {
                    "symbol": item["symbol"], "name": item["name"], "market": market,
                    "industry": item.get("industry"),
                    "status": "allocatable" if code == "can_scale" else "reserved_waiting_entry",
                    "suggested_twd": POLICY[horizon]["max_pick_twd"] if code == "can_scale" else 0,
                    "maximum_twd": POLICY[horizon]["max_pick_twd"],
                    "requires_price_confirmation": True,
                }
                allocations[horizon].append(row)
                by_symbol[item["symbol"]][horizon] = row
                industry_exposure[industry] += amount
    long_candidates = [
        item for item in decisions
        if not item.get("core_data_missing") and not item.get("risk_blocks")
        and (item.get("final") or {}).get("recommendation") in {"can_scale", "wait_pullback"}
        and (item.get("horizons", {}).get("long") or {}).get("recommendation")
        in {"can_scale", "wait_pullback"}
    ]
    long_candidates.sort(key=lambda x: (
        (x.get("horizons", {}).get("long") or {}).get("recommendation") != "can_scale",
        -float((x.get("horizons", {}).get("long") or {}).get("score") or 0),
        x.get("formal_rank") or 999999,
    ))
    long_industry_exposure: dict[str, int] = defaultdict(int)
    long_industry_cap = round(
        POLICY["long"]["capital_twd"]["ALL"] * POLICY["long"]["same_industry_max_pct"] / 100
    )
    for item in long_candidates:
        if len(allocations["long"]) >= POLICY["long"]["max_picks"]:
            break
        horizon_code = item["horizons"]["long"]["recommendation"]
        code = (
            "can_scale"
            if horizon_code == "can_scale"
            and (item.get("final") or {}).get("recommendation") == "can_scale"
            else "wait_pullback"
        )
        industry = str(item.get("industry") or "未分類")
        amount = POLICY["long"]["max_pick_twd"] if code == "can_scale" else 0
        if amount and long_industry_exposure[industry] + amount > long_industry_cap:
            continue
        row = {
            "symbol": item["symbol"], "name": item["name"], "market": item.get("market"),
            "industry": item.get("industry"),
            "status": "allocatable" if code == "can_scale" else "reserved_waiting_entry",
            "suggested_twd": POLICY["long"]["max_pick_twd"] if code == "can_scale" else 0,
            "maximum_twd": POLICY["long"]["max_pick_twd"],
            "requires_price_confirmation": True,
        }
        allocations["long"].append(row)
        by_symbol[item["symbol"]]["long"] = row
        long_industry_exposure[industry] += amount
    invested = sum(row["suggested_twd"] for rows in allocations.values() for row in rows)
    return {
        "schema_version": 1,
        "status": "cash_only" if invested == 0 else "planned_positions_available",
        "policy": POLICY,
        "allocations": allocations,
        "by_symbol": dict(by_symbol),
        "suggested_invested_twd": invested,
        "risk_controls": {
            "risk_blocked_symbols_receive_zero": True,
            "missing_data_symbols_receive_zero": True,
            "waiting_entry_is_not_invested": True,
            "same_industry_max_pct_per_horizon": {"short": 30, "medium": 30, "long": 50},
            "orders": "不連券商、不自動下單",
        },
    }
