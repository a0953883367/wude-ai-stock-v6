from pathlib import Path


def replace_once(text, old, new, label):
    if old not in text:
        raise SystemExit(f"missing marker: {label}")
    return text.replace(old, new, 1)


p = Path("data_fetcher.py")
s = p.read_text(encoding="utf-8")
marker = "\ndef fetch_core_market() -> dict[str, dict[str, float | None]]:\n"
insert = r'''

def _normalize_us_equity_info(info: dict[str, Any]) -> dict[str, Any]:
    """Normalize Yahoo company fields into the same scoring vocabulary used by TW equities."""
    def number(*keys: str) -> float | None:
        for key in keys:
            value = info.get(key)
            try:
                result = float(value)
            except (TypeError, ValueError):
                continue
            if pd.notna(result):
                return result
        return None

    per = number("trailingPE", "forwardPE")
    pbr = number("priceToBook")
    dividend = _ratio_percent(info.get("dividendYield"))
    revenue_growth = _ratio_percent(info.get("revenueGrowth"))
    earnings_growth = _ratio_percent(info.get("earningsGrowth")) if info.get("earningsGrowth") is not None else None
    gross_margin = _ratio_percent(info.get("grossMargins"))
    operating_margin = _ratio_percent(info.get("operatingMargins"))
    roe = _ratio_percent(info.get("returnOnEquity"))
    debt_to_equity = number("debtToEquity")
    debt_ratio = None
    if debt_to_equity is not None and debt_to_equity >= 0:
        de = debt_to_equity / 100 if debt_to_equity > 3 else debt_to_equity
        debt_ratio = de / (1 + de) * 100
    operating_cash_flow = number("operatingCashflow")
    eps = number("trailingEps", "forwardEps")
    market_cap = number("marketCap")
    fields = [per, pbr, dividend, revenue_growth, earnings_growth, gross_margin,
              operating_margin, roe, debt_ratio, operating_cash_flow, eps, market_cap]
    available = sum(value is not None for value in fields)
    return {
        "per": per, "pbr": pbr, "dividend_yield": dividend,
        "revenue_yoy_pct": revenue_growth, "eps_yoy_pct": earnings_growth,
        "gross_margin_pct": gross_margin, "operating_margin_pct": operating_margin,
        "roe_pct": roe, "debt_ratio_pct": debt_ratio,
        "operating_cash_flow": operating_cash_flow,
        "operating_cash_flow_positive": None if operating_cash_flow is None else float(operating_cash_flow > 0),
        "eps": eps, "market_cap": market_cap,
        "fundamental_available": float(any(value is not None for value in (per, pbr, dividend, revenue_growth))),
        "financial_quality_available": float(any(value is not None for value in (earnings_growth, gross_margin, operating_margin, roe, debt_ratio, operating_cash_flow))),
        "us_company_data_available": available > 0,
        "us_company_data_fields": available,
    }


def fetch_us_company_metadata(universe: list[dict[str, Any]], cache_path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Fetch US company fundamentals once daily with cache/fallback; ETFs are excluded."""
    cache_path = cache_path or (SETTINGS.reports_dir / "us_company_metadata_cache.json")
    symbols = [str(item.get("symbol") or "").upper() for item in universe
               if item.get("market") == "US" and "ETF" not in str(item.get("type") or "").upper() and item.get("symbol")]
    if not symbols:
        return {}
    today = date.today()
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8")).get("companies", {})
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        cached = {}
    output, pending = {}, []
    for symbol in symbols:
        item = cached.get(symbol, {})
        try:
            fresh = (today - date.fromisoformat(str(item.get("cached_at")))).days < 1
        except (TypeError, ValueError):
            fresh = False
        if fresh:
            output[symbol] = {k: v for k, v in item.items() if k != "cached_at"}
        else:
            pending.append(symbol)
    def fetch_one(symbol):
        try:
            return symbol, _normalize_us_equity_info(yf.Ticker(symbol).get_info() or {})
        except Exception as exc:
            LOG.debug("US company metadata unavailable for %s: %s", symbol, exc)
            return symbol, {}
    with ThreadPoolExecutor(max_workers=5) as pool:
        jobs = [pool.submit(fetch_one, symbol) for symbol in pending]
        for job in as_completed(jobs):
            symbol, item = job.result()
            if item and item.get("us_company_data_available"):
                output[symbol] = item
                cached[symbol] = {**item, "cached_at": today.isoformat()}
            elif symbol in cached:
                output[symbol] = {k: v for k, v in cached[symbol].items() if k != "cached_at"}
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps({"updated_at": today.isoformat(), "companies": cached}, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        LOG.debug("US company metadata cache write failed: %s", exc)
    return output
'''
s = replace_once(s, marker, insert + marker, "data fetch insertion")
p.write_text(s, encoding="utf-8")

p = Path("briefing.py")
s = p.read_text(encoding="utf-8")
s = replace_once(s, "    fetch_us_short_volume,\n", "    fetch_us_short_volume,\n    fetch_us_company_metadata,\n", "briefing import")
s = replace_once(s, "    us_short_volume = fetch_us_short_volume(us_symbols)\n    etf_metadata = fetch_etf_metadata(universe)\n", "    us_short_volume = fetch_us_short_volume(us_symbols)\n    us_company_metadata = fetch_us_company_metadata(universe)\n    etf_metadata = fetch_etf_metadata(universe)\n", "metadata fetch")
s = replace_once(s, "            elif item.get(\"market\") == \"US\":\n                row.update(us_short_volume.get(symbol.upper(), {}))\n", "            elif item.get(\"market\") == \"US\":\n                row.update(us_short_volume.get(symbol.upper(), {}))\n                if \"ETF\" not in str(item.get(\"type\", \"\")).upper():\n                    row.update(us_company_metadata.get(symbol.upper(), {}))\n", "metadata merge")
p.write_text(s, encoding="utf-8")

p = Path("tests/test_data_fetcher.py")
s = p.read_text(encoding="utf-8")
s += r'''


def test_normalize_us_equity_info_maps_company_fundamentals():
    from data_fetcher import _normalize_us_equity_info
    result = _normalize_us_equity_info({"trailingPE":25,"priceToBook":8,"dividendYield":0.005,"revenueGrowth":0.18,"earningsGrowth":0.22,"grossMargins":0.55,"operatingMargins":0.31,"returnOnEquity":0.42,"debtToEquity":45,"operatingCashflow":123456,"trailingEps":4.2,"marketCap":1_000_000_000})
    assert result["per"] == 25
    assert round(result["revenue_yoy_pct"], 6) == 18
    assert round(result["eps_yoy_pct"], 6) == 22
    assert round(result["gross_margin_pct"], 6) == 55
    assert result["operating_cash_flow_positive"] == 1.0
    assert result["fundamental_available"] == 1.0
    assert result["financial_quality_available"] == 1.0
    assert result["us_company_data_fields"] >= 10
'''
p.write_text(s, encoding="utf-8")
print("ALL V3 US/ETF patch applied")
