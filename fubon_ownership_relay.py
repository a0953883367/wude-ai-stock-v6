"""Fetch the display-only ownership supplement through authenticated Railway."""
import json
from datetime import datetime

from config import ROOT, TAIPEI
from us_market_data import _relay_request
from watchlist import load_watchlist


def refresh():
    path = ROOT / "reports" / "fubon_ownership.json"
    try:
        old = json.loads(path.read_text(encoding="utf-8")).get("data", {})
    except (OSError, ValueError, TypeError):
        old = {}
    stamp = datetime.now(TAIPEI).isoformat(timespec="seconds")
    pool = [row for row in load_watchlist() if row["symbol"].endswith((".TW", ".TWO"))]
    data = {}
    stopped = False
    for offset in range(0, len(pool), 5):
        batch = [row["symbol"] for row in pool[offset:offset+5]]
        fetched = {} if stopped else _relay_request("ownership", {"symbols": batch}, timeout=25)
        if not fetched or any(entry.get("status") == "rate_limited"
                              for kinds in fetched.values() if isinstance(kinds, dict)
                              for entry in kinds.values() if isinstance(entry, dict)):
            stopped = True
        for symbol in batch:
            if isinstance(fetched.get(symbol), dict):
                data[symbol] = fetched[symbol]
            else:
                data[symbol] = {}
                for kind in ("institutional_trades", "tdcc_distribution", "director_holdings"):
                    entry = dict(old.get(symbol, {}).get(kind, {}))
                    entry.update(status="relay_unavailable", attempted_at=stamp)
                    data[symbol][kind] = entry
    report = {"version": 1, "mode": "display_only", "source": "Fubon Neo via authenticated Railway",
              "updated_at": stamp, "data": data,
              "available_count": sum(row.get("status") == "available" for kinds in data.values() for row in kinds.values())}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    tmp.replace(path)
    print("Fubon ownership datasets available:", report["available_count"])


if __name__ == "__main__":
    refresh()
