#!/usr/bin/env python3
"""用完全假的資料驗收紙上交易；不連券商、不讀正式金鑰、不碰正式資料。"""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trade_engine import JsonTradingStateStore, PaperTradingEngine  # noqa: E402


TAIPEI = ZoneInfo("Asia/Taipei")


def _candidate() -> dict[str, Any]:
    return {
        "symbol": "2330.TW",
        "name": "假資料測試股",
        "market": "TW",
        "type": "個股",
        "price": 100,
        "market_contract_valid": True,
        "trade_guard_blocked": False,
        "short_term_eligible": True,
        "overall_eligible": True,
        "next_session_direction": "📈 看漲",
        "next_session_confidence": 80,
        "outlook_direction": "📈 看漲",
        "short_term_entry_low": 98,
        "short_term_entry_high": 102,
        "short_term_stop": 95,
        "resistance1": 105,
    }


def run_acceptance() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    with TemporaryDirectory(prefix="wude-paper-acceptance-") as directory:
        root = Path(directory)
        report = root / "fake_candidates.json"
        report.write_text(json.dumps({"data": [_candidate()]}, ensure_ascii=False), encoding="utf-8")
        fx = root / "fake_market.json"
        fx.write_text(json.dumps({"market": {"美元台幣": {"price": 32}}}), encoding="utf-8")
        state_path = root / "paper_state.json"
        prices = {"2330.TW": 100.0}
        quote_calls = 0

        def quote_fetcher(symbol: str, market: str) -> dict[str, Any]:
            nonlocal quote_calls
            quote_calls += 1
            return {"source": "acceptance-fake-quote", "quote": {"lastPrice": prices[symbol]}}

        store = JsonTradingStateStore(state_path, initial_cash=20_000, mode="paper")
        engine = PaperTradingEngine(
            store,
            report_path=report,
            fx_path=fx,
            quote_fetcher=quote_fetcher,
            clock=lambda: datetime(2026, 9, 21, 10, 0, tzinfo=TAIPEI),
        )

        engine.configure(selected=["2330"], cash_limit=20_000, enabled=True)
        bought = engine.run_cycle(session_date="2026-09-21", markets={"TW"})
        buy_orders = [item for item in bought["orders"] if item["side"] == "BUY"]
        check("假資料買進", len(buy_orders) == 1 and "2330.TW" in bought["positions"], "合格標的只建立一筆模擬買單")
        check("資金上限", 0 <= bought["paper_cash"] <= 20_000, "紙上帳戶不超過20,000元且不會變成負數")
        check("保留現金", bought["paper_cash"] >= 2_000, "單一標的仍保留至少10%安全現金")

        duplicate = engine.run_cycle(session_date="2026-09-21", markets={"TW"})
        duplicate_buys = [item for item in duplicate["orders"] if item["side"] == "BUY"]
        check("防重複下單", len(duplicate_buys) == 1, "同一交易日重跑不會再買一次")

        held = engine.run_cycle(session_date="2026-09-22", markets={"TW"})
        check("持有流程", "2330.TW" in held["positions"] and len(held["orders"]) == 1, "未觸發退出條件時保持原持倉")

        prices["2330.TW"] = 94.0
        sold = engine.run_cycle(session_date="2026-09-23", markets={"TW"})
        sell_orders = [item for item in sold["orders"] if item["side"] == "SELL"]
        check("停損賣出", len(sell_orders) == 1 and not sold["positions"], "跌破停損價後完成模擬賣出")
        check("單日損失停止", sold["enabled"] is False and sold["daily_realized_pnl"] < -400, "超過2%單日損失後自動關閉新買進")
        check("不會送真單", sold["real_orders_sent"] == 0 and all(item["mode"] == "paper" for item in sold["orders"]), "全流程真實委託數維持0")

        reloaded = JsonTradingStateStore(state_path, initial_cash=20_000, mode="paper").load()
        check("暫存狀態可還原", reloaded["orders"] == sold["orders"] and reloaded["positions"] == {}, "紙上交易狀態可由獨立暫存檔完整讀回")
        check("測試資料隔離", state_path.is_relative_to(root) and str(root).startswith("/tmp/"), "所有驗收檔案只存在作業系統暫存目錄")
        check("行情來源隔離", quote_calls > 0, "只呼叫程式內假行情，不使用外部行情API")

        try:
            engine.configure(selected=["2330"], cash_limit=20_001)
        except ValueError:
            over_cap_rejected = True
        else:
            over_cap_rejected = False
        check("超額資金拒絕", over_cap_rejected, "20,001元設定會被拒絕")

    return {
        "status": "passed" if all(item["passed"] for item in checks) else "failed",
        "scope": "假資料紙上交易買進、持有、賣出、資金上限、防重複與停損；不連券商、不讀正式金鑰",
        "checks": checks,
    }


def main() -> int:
    result = run_acceptance()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
