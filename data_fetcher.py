"""Market-data adapters with graceful fallbacks.

Daily and intraday prices come from Yahoo Finance via yfinance. FinMind is
optional and enriches the model with Taiwan institutional flows.
"""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import requests
import yfinance as yf

from config import SETTINGS


LOG = logging.getLogger(__name__)
CORE_MARKET = {
    "加權指數": "^TWII",
    "S&P 500": "^GSPC",
    "Nasdaq": "^IXIC",
    "費城半導體": "^SOX",
    "NVDA": "NVDA",
    "TSM ADR": "TSM",
    "AMD": "AMD",
    "SMH": "SMH",
    "美元台幣": "TWD=X",
    "美元指數": "DX-Y.NYB",
    "VIX": "^VIX",
    "美國10年期公債殖利率": "^TNX",
}


def load_search_universe(path: Path = SETTINGS.search_data_path) -> list[dict[str, Any]]:
    """Load every Taiwan and US candidate maintained by the V6 dashboard."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in payload.get("data", []):
        symbol = str(row.get("代號", "")).strip().upper()
        market_text = str(row.get("市場", ""))
        if not symbol or symbol in seen:
            continue
        if "台灣" in market_text:
            if not (symbol.endswith(".TW") or symbol.endswith(".TWO")):
                continue
            market = "TW"
        elif "美國" in market_text:
            if symbol.endswith(".TW") or symbol.endswith(".TWO"):
                continue
            market = "US"
        else:
            continue
        seen.add(symbol)
        rows.append({
            "symbol": symbol,
            "name": row.get("股票", symbol),
            "market": market,
            "type": row.get("類型", "個股"),
            "theme": row.get("主題", "其他"),
            "industry": row.get("次產業", "其他"),
        })
    if not rows:
        raise RuntimeError("search_data.json 沒有可用的股票候選清單")
    return rows


def load_taiwan_universe(path: Path = SETTINGS.search_data_path) -> list[dict[str, Any]]:
    """Backward-compatible Taiwan-only view of the maintained search universe."""
    return [row for row in load_search_universe(path) if row.get("market") == "TW"]


def _chunks(items: list[str], size: int) -> Iterable[list[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _extract_frame(raw: pd.DataFrame, symbol: str, multi: bool) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame()
    if multi:
        try:
            frame = raw[symbol].copy()
        except (KeyError, TypeError):
            return pd.DataFrame()
    else:
        frame = raw.copy()
    frame.columns = [str(c).lower() for c in frame.columns]
    return frame.dropna(how="all")


def download_history(symbols: list[str], period: str = "3mo") -> dict[str, pd.DataFrame]:
    """Download daily OHLCV in chunks so one bad ticker cannot stop the run."""
    result: dict[str, pd.DataFrame] = {}
    for chunk in _chunks(symbols, 35):
        try:
            raw = yf.download(
                tickers=chunk,
                period=period,
                interval="1d",
                group_by="ticker",
                auto_adjust=False,
                actions=False,
                threads=True,
                progress=False,
                timeout=SETTINGS.request_timeout,
            )
            multi = isinstance(raw.columns, pd.MultiIndex)
            for symbol in chunk:
                frame = _extract_frame(raw, symbol, multi)
                if not frame.empty:
                    result[symbol] = frame
        except Exception as exc:
            LOG.warning("daily batch failed (%s): %s", ",".join(chunk[:3]), exc)
        time.sleep(0.3)
    return result


def download_intraday(symbols: list[str]) -> dict[str, pd.DataFrame]:
    """Download today's 5-minute bars used for volume pace and attack-volume proxy."""
    result: dict[str, pd.DataFrame] = {}
    for chunk in _chunks(symbols, 25):
        try:
            raw = yf.download(
                tickers=chunk,
                period="1d",
                interval="5m",
                group_by="ticker",
                auto_adjust=False,
                actions=False,
                prepost=False,
                threads=True,
                progress=False,
                timeout=SETTINGS.request_timeout,
            )
            multi = isinstance(raw.columns, pd.MultiIndex)
            for symbol in chunk:
                frame = _extract_frame(raw, symbol, multi)
                if not frame.empty:
                    result[symbol] = frame
        except Exception as exc:
            LOG.warning("intraday batch failed (%s): %s", ",".join(chunk[:3]), exc)
        time.sleep(0.3)
    return result




def _ratio_percent(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not pd.notna(number):
        return None
    return number * 100 if abs(number) <= 1 else number


def _normalize_etf_info(info: dict[str, Any]) -> dict[str, Any]:
    """Normalize Yahoo fund fields and keep missing values explicitly unavailable."""
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

    nav = number("navPrice", "netAssetValue")
    bid, ask = number("bid"), number("ask")
    spread = None
    if bid and ask and ask >= bid:
        spread = (ask - bid) / ((ask + bid) / 2) * 100
    result = {
        "nav_price": nav,
        "bid_ask_spread_pct": spread,
        "expense_ratio_pct": _ratio_percent(info.get("annualReportExpenseRatio")),
        "aum": number("totalAssets", "netAssets"),
        "etf_return_3y_pct": _ratio_percent(info.get("threeYearAverageReturn")),
        "etf_return_5y_pct": _ratio_percent(info.get("fiveYearAverageReturn")),
        "etf_ytd_return_pct": _ratio_percent(info.get("ytdReturn")),
        "beta_3y": number("beta3Year"),
        "etf_category": info.get("category"),
        "etf_family": info.get("fundFamily"),
    }
    price = number("regularMarketPrice", "currentPrice")
    if nav and price:
        result["premium_discount_pct"] = (price / nav - 1) * 100
    available = sum(value is not None for key, value in result.items() if key not in {"etf_category", "etf_family"})
    result["etf_metadata_available"] = available > 0
    result["etf_metadata_fields"] = available
    return result


def fetch_etf_metadata(
    universe: list[dict[str, Any]],
    cache_path: Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Fetch ETF-only metadata once per day so two-hour refreshes stay lightweight."""
    cache_path = cache_path or (SETTINGS.reports_dir / "etf_metadata_cache.json")
    etfs = {
        str(item.get("symbol") or ""): item
        for item in universe
        if "ETF" in str(item.get("type") or "").upper() and item.get("symbol")
    }
    if not etfs:
        return {}
    today = date.today()
    cached: dict[str, Any] = {}
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8")).get("funds", {})
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        cached = {}

    output: dict[str, dict[str, Any]] = {}
    pending: list[str] = []
    for symbol in etfs:
        item = cached.get(symbol, {})
        try:
            fresh = (today - date.fromisoformat(str(item.get("cached_at")))).days < 1
        except (TypeError, ValueError):
            fresh = False
        if fresh:
            output[symbol] = {key: value for key, value in item.items() if key != "cached_at"}
        else:
            pending.append(symbol)

    def fetch_one(symbol: str) -> tuple[str, dict[str, Any]]:
        try:
            info = yf.Ticker(symbol).get_info() or {}
            return symbol, _normalize_etf_info(info)
        except Exception as exc:
            LOG.debug("ETF metadata unavailable for %s: %s", symbol, exc)
            return symbol, {}

    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs = [pool.submit(fetch_one, symbol) for symbol in pending]
        for job in as_completed(jobs):
            symbol, item = job.result()
            if item:
                output[symbol] = item
                cached[symbol] = {**item, "cached_at": today.isoformat()}
            elif symbol in cached:
                output[symbol] = {key: value for key, value in cached[symbol].items() if key != "cached_at"}

    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps({"updated_at": today.isoformat(), "funds": cached}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        LOG.debug("ETF metadata cache write failed: %s", exc)
    return output


def fetch_core_market() -> dict[str, dict[str, float | None]]:
    histories = download_history(list(CORE_MARKET.values()), period="7d")
    output: dict[str, dict[str, float | None]] = {}
    for label, symbol in CORE_MARKET.items():
        frame = histories.get(symbol)
        if frame is None or frame.empty or "close" not in frame:
            output[label] = {"price": None, "change_pct": None}
            continue
        close = frame["close"].dropna()
        price = float(close.iloc[-1]) if len(close) else None
        change = float((close.iloc[-1] / close.iloc[-2] - 1) * 100) if len(close) > 1 else None
        output[label] = {"price": price, "change_pct": change}
    return output


def fetch_macro_history(period: str = "3mo") -> list[dict[str, Any]]:
    """Return date-aligned macro snapshots for immediate historical calibration."""
    labels = ("美元台幣", "美元指數", "VIX", "美國10年期公債殖利率")
    histories = download_history([CORE_MARKET[label] for label in labels], period=period)
    by_date: dict[str, dict[str, dict[str, float | None]]] = {}
    for label in labels:
        frame = histories.get(CORE_MARKET[label])
        if frame is None or frame.empty or "close" not in frame:
            continue
        close = frame["close"].dropna()
        for index in range(len(close)):
            timestamp = pd.Timestamp(close.index[index])
            trade_date = timestamp.date().isoformat()
            price = float(close.iloc[index])
            change = (
                float((close.iloc[index] / close.iloc[index - 1] - 1) * 100)
                if index > 0 else None
            )
            by_date.setdefault(trade_date, {})[label] = {
                "price": price,
                "change_pct": change,
            }
    return [
        {"date": trade_date, "market": by_date[trade_date]}
        for trade_date in sorted(by_date)
    ][-45:]


def _aggregate_institutional_rows(
    rows: list[dict[str, Any]], stock_ids: set[str]
) -> dict[str, dict[str, float]]:
    """Aggregate institutional flows over 1/3/5/10 available sessions."""
    daily: dict[str, dict[str, dict[str, float]]] = {}
    for row in rows:
        sid = str(row.get("stock_id", ""))
        if sid not in stock_ids:
            continue
        trade_date = str(row.get("date", ""))
        name = str(row.get("name", ""))
        net = float(row.get("buy", 0) or 0) - float(row.get("sell", 0) or 0)
        item = daily.setdefault(sid, {}).setdefault(
            trade_date, {"foreign": 0.0, "trust": 0.0, "dealer": 0.0}
        )
        if name == "Foreign_Investor":
            item["foreign"] += net
        elif name == "Investment_Trust":
            item["trust"] += net
        elif name in {"Dealer", "Dealer_self", "Dealer_Hedging"}:
            item["dealer"] += net

    output: dict[str, dict[str, float]] = {}
    for sid, by_date in daily.items():
        dates = sorted(by_date, reverse=True)
        if not dates:
            continue
        latest = by_date[dates[0]]
        item: dict[str, float] = {
            "foreign": latest["foreign"],
            "trust": latest["trust"],
            "dealer": latest["dealer"],
            "available": 1.0,
        }
        for window in (1, 3, 5, 10):
            selected = dates[:window]
            for group in ("foreign", "trust", "dealer"):
                item[f"{group}_{window}d"] = sum(by_date[d][group] for d in selected)
            item[f"institution_{window}d"] = sum(
                sum(by_date[d][group] for group in ("foreign", "trust", "dealer"))
                for d in selected
            )
        output[sid] = item
    return output


def fetch_institutional_flows(stock_ids: set[str]) -> dict[str, dict[str, float]]:
    """Fetch recent all-market institutional trades from FinMind in one request."""
    if not SETTINGS.finmind_token:
        return {}
    end = date.today()
    # 21 calendar days normally covers at least 10 Taiwan trading sessions.
    start = end - timedelta(days=21)
    rows = _dataset_for_ids(
        "TaiwanStockInstitutionalInvestorsBuySell", stock_ids, start, end
    )
    return _aggregate_institutional_rows(rows, stock_ids)


def _finmind_rows(dataset: str, start: date, end: date, stock_id: str | None = None) -> list[dict[str, Any]]:
    """Read one FinMind dataset and treat plan/rate failures as unavailable."""
    if not SETTINGS.finmind_token:
        return []
    params: dict[str, str] = {
        "dataset": dataset,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }
    if stock_id:
        params["data_id"] = stock_id
    try:
        response = requests.get(
            "https://api.finmindtrade.com/api/v4/data",
            params=params,
            headers={"Authorization": f"Bearer {SETTINGS.finmind_token}"},
            timeout=SETTINGS.request_timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") not in (None, 200):
            return []
        return payload.get("data", []) or []
    except Exception as exc:
        LOG.debug("FinMind %s unavailable for %s: %s", dataset, stock_id or "all", exc)
        return []


def _dataset_for_ids(dataset: str, stock_ids: set[str], start: date, end: date) -> list[dict[str, Any]]:
    """Prefer one all-market request; free plans fall back to per-stock calls."""
    rows = _finmind_rows(dataset, start, end)
    if rows:
        return [row for row in rows if str(row.get("stock_id", "")) in stock_ids]
    output: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs = {pool.submit(_finmind_rows, dataset, start, end, sid): sid for sid in stock_ids}
        for job in as_completed(jobs):
            output.extend(job.result())
    return output


def _aggregate_credit_rows(
    margin_rows: list[dict[str, Any]], short_rows: list[dict[str, Any]], stock_ids: set[str]
) -> dict[str, dict[str, float]]:
    """Build latest and 5-session changes for margin, short sale and SBL balances."""
    by_sid: dict[str, dict[str, dict[str, dict[str, Any]]]] = {}
    for kind, rows in (("margin", margin_rows), ("short", short_rows)):
        for row in rows:
            sid = str(row.get("stock_id", ""))
            if sid not in stock_ids:
                continue
            by_sid.setdefault(sid, {}).setdefault(kind, {})[str(row.get("date", ""))] = row

    output: dict[str, dict[str, float]] = {}
    for sid, kinds in by_sid.items():
        item: dict[str, float] = {}
        margin_dates = sorted(kinds.get("margin", {}), reverse=True)
        if margin_dates:
            latest = kinds["margin"][margin_dates[0]]
            oldest = kinds["margin"][margin_dates[min(4, len(margin_dates) - 1)]]
            margin_balance = float(latest.get("MarginPurchaseTodayBalance", 0) or 0)
            short_balance = float(latest.get("ShortSaleTodayBalance", 0) or 0)
            item.update({
                "credit_available": 1.0,
                "margin_balance": margin_balance,
                "margin_1d_change": margin_balance - float(latest.get("MarginPurchaseYesterdayBalance", 0) or 0),
                "margin_5d_change": margin_balance - float(oldest.get("MarginPurchaseYesterdayBalance", 0) or 0),
                "short_balance": short_balance,
                "short_1d_change": short_balance - float(latest.get("ShortSaleYesterdayBalance", 0) or 0),
                "short_5d_change": short_balance - float(oldest.get("ShortSaleYesterdayBalance", 0) or 0),
            })
        short_dates = sorted(kinds.get("short", {}), reverse=True)
        if short_dates:
            latest = kinds["short"][short_dates[0]]
            oldest = kinds["short"][short_dates[min(4, len(short_dates) - 1)]]
            sbl_balance = float(latest.get("SBLShortSalesCurrentDayBalance", 0) or 0)
            item.update({
                "credit_available": 1.0,
                "sbl_balance": sbl_balance,
                "sbl_1d_change": sbl_balance - float(latest.get("SBLShortSalesPreviousDayBalance", 0) or 0),
                "sbl_5d_change": sbl_balance - float(oldest.get("SBLShortSalesPreviousDayBalance", 0) or 0),
            })
        if item:
            output[sid] = item
    return output


def fetch_credit_flows(stock_ids: set[str]) -> dict[str, dict[str, float]]:
    """Fetch margin/short and securities-borrowing short-sale balances."""
    if not SETTINGS.finmind_token or not stock_ids:
        return {}
    end = date.today()
    start = end - timedelta(days=14)
    margin_rows = _dataset_for_ids("TaiwanStockMarginPurchaseShortSale", stock_ids, start, end)
    short_rows = _dataset_for_ids("TaiwanDailyShortSaleBalances", stock_ids, start, end)
    return _aggregate_credit_rows(margin_rows, short_rows, stock_ids)


def _aggregate_fundamental_rows(
    per_rows: list[dict[str, Any]], revenue_rows: list[dict[str, Any]], stock_ids: set[str]
) -> dict[str, dict[str, float]]:
    """Combine latest valuation with monthly revenue YoY/MoM trends."""
    output: dict[str, dict[str, float]] = {}
    per_by_sid: dict[str, list[dict[str, Any]]] = {}
    for row in per_rows:
        sid = str(row.get("stock_id", ""))
        if sid in stock_ids:
            per_by_sid.setdefault(sid, []).append(row)
    for sid, rows in per_by_sid.items():
        latest = max(rows, key=lambda row: str(row.get("date", "")))
        output.setdefault(sid, {}).update({
            "fundamental_available": 1.0,
            "per": float(latest.get("PER", 0) or 0),
            "pbr": float(latest.get("PBR", 0) or 0),
            "dividend_yield": float(latest.get("dividend_yield", 0) or 0),
        })

    revenue_by_sid: dict[str, dict[tuple[int, int], float]] = {}
    for row in revenue_rows:
        sid = str(row.get("stock_id", ""))
        if sid not in stock_ids:
            continue
        year = int(float(row.get("revenue_year", 0) or 0))
        month = int(float(row.get("revenue_month", 0) or 0))
        if year and month:
            revenue_by_sid.setdefault(sid, {})[(year, month)] = float(row.get("revenue", 0) or 0)
    for sid, periods in revenue_by_sid.items():
        if not periods:
            continue
        keys = sorted(periods)
        latest_key = keys[-1]
        latest_revenue = periods[latest_key]
        prior_year = periods.get((latest_key[0] - 1, latest_key[1]))
        previous = periods[keys[-2]] if len(keys) > 1 else None
        yoy = (latest_revenue / prior_year - 1) * 100 if prior_year else None
        mom = (latest_revenue / previous - 1) * 100 if previous else None
        item = output.setdefault(sid, {})
        item.update({
            "fundamental_available": 1.0,
            "revenue_year": float(latest_key[0]),
            "revenue_month": float(latest_key[1]),
            "monthly_revenue": latest_revenue,
        })
        if yoy is not None:
            item["revenue_yoy_pct"] = yoy
        if mom is not None:
            item["revenue_mom_pct"] = mom
    return output


def fetch_fundamentals(stock_ids: set[str]) -> dict[str, dict[str, float]]:
    """Fetch free Taiwan valuation and monthly-revenue datasets."""
    if not SETTINGS.finmind_token or not stock_ids:
        return {}
    end = date.today()
    per_rows = _dataset_for_ids(
        "TaiwanStockPER", stock_ids, end - timedelta(days=21), end
    )
    revenue_rows = _dataset_for_ids(
        "TaiwanStockMonthRevenue", stock_ids, end - timedelta(days=450), end
    )
    return _aggregate_fundamental_rows(per_rows, revenue_rows, stock_ids)


def _statement_value(rows: list[dict[str, Any]], aliases: set[str], name_tokens: tuple[str, ...] = ()) -> float | None:
    for row in rows:
        if str(row.get("type", "")) in aliases:
            return float(row.get("value", 0) or 0)
    for row in rows:
        origin = str(row.get("origin_name", ""))
        if name_tokens and any(token in origin for token in name_tokens):
            return float(row.get("value", 0) or 0)
    return None


def _aggregate_financial_quality_rows(
    income_rows: list[dict[str, Any]], balance_rows: list[dict[str, Any]],
    cash_rows: list[dict[str, Any]], stock_ids: set[str],
) -> dict[str, dict[str, float]]:
    """Build comparable profitability, balance-sheet and cash-quality fields."""
    grouped: dict[str, dict[str, dict[str, list[dict[str, Any]]]]] = {}
    for section, rows in (("income", income_rows), ("balance", balance_rows), ("cash", cash_rows)):
        for row in rows:
            sid = str(row.get("stock_id", ""))
            if sid in stock_ids:
                grouped.setdefault(sid, {}).setdefault(section, {}).setdefault(str(row.get("date", "")), []).append(row)

    output: dict[str, dict[str, float]] = {}
    for sid, sections in grouped.items():
        item: dict[str, float] = {"financial_quality_available": 1.0}
        income_dates = sorted(sections.get("income", {}))
        net_income: float | None = None
        if income_dates:
            latest_date = income_dates[-1]
            latest = sections["income"][latest_date]
            revenue = _statement_value(latest, {"Revenue", "OperatingRevenue", "TotalRevenue"}, ("營業收入", "收入合計", "營收"))
            gross = _statement_value(latest, {"GrossProfit", "GrossProfitLoss"}, ("營業毛利",))
            operating = _statement_value(latest, {"OperatingIncome", "OperatingIncomeLoss"}, ("營業利益", "營業損益"))
            net_income = _statement_value(latest, {"IncomeAfterTaxes", "ProfitLoss", "NetIncome"}, ("本期淨利", "本期損益"))
            eps = _statement_value(latest, {"EPS", "BasicEarningsLossPerShare"}, ("每股盈餘",))
            item["financial_report_date"] = latest_date
            if eps is not None:
                item["eps"] = eps
                latest_year, latest_month = int(latest_date[:4]), latest_date[5:7]
                prior_eps = None
                for old_date in reversed(income_dates[:-1]):
                    if old_date.startswith(f"{latest_year - 1}-{latest_month}"):
                        prior_eps = _statement_value(sections["income"][old_date], {"EPS", "BasicEarningsLossPerShare"}, ("每股盈餘",))
                        break
                if prior_eps not in (None, 0):
                    item["eps_yoy_pct"] = (eps / prior_eps - 1) * 100
            if revenue not in (None, 0):
                if gross is not None:
                    item["gross_margin_pct"] = gross / revenue * 100
                if operating is not None:
                    item["operating_margin_pct"] = operating / revenue * 100

        balance_dates = sorted(sections.get("balance", {}))
        equity: float | None = None
        if balance_dates:
            latest = sections["balance"][balance_dates[-1]]
            assets = _statement_value(latest, {"Assets", "TotalAssets"}, ("資產總額", "資產合計"))
            liabilities = _statement_value(latest, {"Liabilities", "TotalLiabilities"}, ("負債總額", "負債合計"))
            equity = _statement_value(latest, {"Equity", "TotalEquity", "EquityAttributableToOwnersOfParent"}, ("權益總額", "權益合計"))
            if assets not in (None, 0) and liabilities is not None:
                item["debt_ratio_pct"] = liabilities / assets * 100
        if net_income is not None and equity not in (None, 0):
            item["roe_pct"] = net_income / equity * 100

        cash_dates = sorted(sections.get("cash", {}))
        if cash_dates:
            latest = sections["cash"][cash_dates[-1]]
            operating_cash = _statement_value(
                latest, {"CashFlowsFromOperatingActivities", "NetCashFlowsFromUsedInOperatingActivities"},
                ("營業活動之淨現金",),
            )
            if operating_cash is not None:
                item["operating_cash_flow"] = operating_cash
                item["operating_cash_flow_positive"] = 1.0 if operating_cash > 0 else 0.0
        if len(item) > 1:
            output[sid] = item
    return output


def fetch_financial_quality(
    stock_ids: set[str], cache_path: Path = SETTINGS.reports_dir / "financial_quality_cache.json",
    batch_size: int = 30,
) -> dict[str, dict[str, Any]]:
    """Refresh a quota-safe batch of quarterly statements and reuse the cache."""
    if not SETTINGS.finmind_token or not stock_ids:
        return {}
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
        cached_rows = dict(cache.get("stocks", {}))
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        cached_rows = {}
    today = date.today()
    pending = []
    for sid in sorted(stock_ids):
        cached_at = str(cached_rows.get(sid, {}).get("cached_at", ""))
        try:
            stale = (today - date.fromisoformat(cached_at)).days >= 30
        except ValueError:
            stale = True
        if sid not in cached_rows or stale:
            pending.append(sid)
    target = set(pending[:batch_size])
    if target:
        start = today - timedelta(days=500)
        income = _dataset_for_ids("TaiwanStockFinancialStatements", target, start, today)
        balance = _dataset_for_ids("TaiwanStockBalanceSheet", target, start, today)
        cash = _dataset_for_ids("TaiwanStockCashFlowsStatement", target, start, today)
        refreshed = _aggregate_financial_quality_rows(income, balance, cash, target)
        for sid, item in refreshed.items():
            cached_rows[sid] = {**item, "cached_at": today.isoformat()}
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps({"updated_at": today.isoformat(), "stocks": cached_rows}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return {sid: cached_rows[sid] for sid in stock_ids if sid in cached_rows}


def fetch_broker_branches(stock_ids: set[str]) -> dict[str, dict[str, Any]]:
    """Fetch optional Sponsor-tier broker branch concentration for recent sessions."""
    if not SETTINGS.finmind_token or not stock_ids:
        return {}
    end = date.today()
    start = end - timedelta(days=7)

    def fetch_one(sid: str) -> tuple[str, list[dict[str, Any]]]:
        try:
            response = requests.get(
                "https://api.finmindtrade.com/api/v4/taiwan_stock_trading_daily_report_secid_agg",
                params={"data_id": sid, "start_date": start.isoformat(), "end_date": end.isoformat()},
                headers={"Authorization": f"Bearer {SETTINGS.finmind_token}"},
                timeout=SETTINGS.request_timeout,
            )
            response.raise_for_status()
            payload = response.json()
            return sid, payload.get("data", []) or []
        except Exception:
            return sid, []

    first = sorted(stock_ids)[0]
    sid, probe = fetch_one(first)
    if not probe:  # Sponsor dataset: stop immediately when the token has no access.
        return {}
    collected = {sid: probe}
    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs = [pool.submit(fetch_one, item) for item in stock_ids if item != first]
        for job in as_completed(jobs):
            item, rows = job.result()
            if rows:
                collected[item] = rows

    output: dict[str, dict[str, Any]] = {}
    for sid, rows in collected.items():
        latest_date = max((str(row.get("date", "")) for row in rows), default="")
        latest = [row for row in rows if str(row.get("date", "")) == latest_date]
        branches = []
        for row in latest:
            net = float(row.get("buy_volume", 0) or 0) - float(row.get("sell_volume", 0) or 0)
            branches.append({"name": str(row.get("securities_trader", "")), "net": int(net)})
        buyers = sorted((b for b in branches if b["net"] > 0), key=lambda b: b["net"], reverse=True)[:3]
        sellers = sorted((b for b in branches if b["net"] < 0), key=lambda b: b["net"])[:3]
        output[sid] = {"broker_available": True, "broker_date": latest_date, "top_brokers_buy": buyers, "top_brokers_sell": sellers}
    return output


def _parse_finra_short_volume(
    text: str, symbols: set[str], report_date: str
) -> dict[str, dict[str, Any]]:
    """Parse FINRA consolidated daily short-sale volume.

    This dataset describes short-sale *transactions* reported for one session.
    It is not outstanding short interest and must not be presented as such.
    """
    wanted = {str(symbol).upper() for symbol in symbols}
    totals: dict[str, dict[str, float]] = {}
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return {}
    headers = lines[0].lstrip("\ufeff").split("|")
    positions = {name: idx for idx, name in enumerate(headers)}
    required = {"Symbol", "ShortVolume", "ShortExemptVolume", "TotalVolume"}
    if not required.issubset(positions):
        return {}
    for line in lines[1:]:
        fields = line.split("|")
        try:
            symbol = fields[positions["Symbol"]].upper()
            if symbol not in wanted:
                continue
            item = totals.setdefault(
                symbol, {"short": 0.0, "short_exempt": 0.0, "total": 0.0}
            )
            item["short"] += float(fields[positions["ShortVolume"]] or 0)
            item["short_exempt"] += float(fields[positions["ShortExemptVolume"]] or 0)
            item["total"] += float(fields[positions["TotalVolume"]] or 0)
        except (IndexError, TypeError, ValueError):
            continue

    output: dict[str, dict[str, Any]] = {}
    for symbol, item in totals.items():
        total = item["total"]
        if total <= 0:
            continue
        output[symbol] = {
            "us_short_volume_available": 1.0,
            "us_short_volume_date": report_date,
            "us_short_volume": int(item["short"]),
            "us_short_exempt_volume": int(item["short_exempt"]),
            "us_total_reported_volume": int(total),
            "us_short_volume_ratio_pct": (item["short"] + item["short_exempt"]) / total * 100,
            "us_short_volume_note": "FINRA每日放空成交量，不等於未回補空單或即時主力部位",
        }
    return output


def fetch_us_short_volume(symbols: set[str]) -> dict[str, dict[str, Any]]:
    """Fetch the latest available FINRA consolidated daily short-volume file."""
    wanted = {str(symbol).upper() for symbol in symbols if symbol}
    if not wanted:
        return {}
    # FINRA files are session based. Try recent calendar days so weekends and
    # market holidays do not make the whole briefing fail.
    for days_back in range(0, 12):
        session = date.today() - timedelta(days=days_back)
        stamp = session.strftime("%Y%m%d")
        url = f"https://cdn.finra.org/equity/regsho/daily/CNMSshvol{stamp}.txt"
        try:
            response = requests.get(url, timeout=SETTINGS.request_timeout)
            if response.status_code != 200 or not response.text.strip():
                continue
            parsed = _parse_finra_short_volume(
                response.text, wanted, session.isoformat()
            )
            if parsed:
                return parsed
        except Exception:
            continue
    return {}
