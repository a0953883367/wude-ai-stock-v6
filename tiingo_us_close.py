"""Quota-bounded Tiingo EOD fallback for private US holding valuation.

The free Tiingo plan is an internal-use source with an hourly request limit.
This collector therefore refreshes at most 47 symbols per scheduled run and
promotes a batch only after every requested symbol has been attempted. During
the free-tier trial raw prices stay in a private CI cache. They may value
already-open owner-only medium/long holdings, but can never create positions,
settle the five-day experiment, alter ranking, or enter a public payload.
"""

from __future__ import annotations

import json
import math
import os
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, time as clock_time, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests


API_ROOT = "https://api.tiingo.com/tiingo/daily"
SCHEMA_VERSION = 1
# Four fixed daily report runs must be able to finish the 186-symbol universe
# before the next completed US session appears. 47 * 4 = 188, while each run
# remains below Tiingo Starter's 50-request hourly ceiling.
FREE_BATCH_LIMIT = 47
MAX_STALE_SECONDS = 10 * 24 * 60 * 60
NEW_YORK = ZoneInfo("America/New_York")


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _cache_age(payload: dict[str, Any], now_epoch: float) -> float | None:
    try:
        return max(0.0, now_epoch - float(payload["fetched_epoch"]))
    except (KeyError, TypeError, ValueError):
        return None


def _is_us_regular_session(now: datetime) -> bool:
    local = now.astimezone(NEW_YORK) if now.tzinfo else now.replace(tzinfo=NEW_YORK)
    return (
        local.weekday() < 5
        and clock_time(9, 30) <= local.time().replace(tzinfo=None) < clock_time(16, 10)
    )


def parse_tiingo_eod(payload: Any, symbol: str) -> dict[str, Any] | None:
    """Normalize the newest dated raw close from a Tiingo EOD response."""
    if not isinstance(payload, list):
        return None
    valid: list[tuple[str, dict[str, Any]]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        session_date = str(item.get("date") or "")[:10]
        close_price = _number(item.get("close"))
        if len(session_date) == 10 and close_price is not None:
            valid.append((session_date, item))
    if not valid:
        return None
    session_date, latest = max(valid, key=lambda pair: pair[0])
    close_price = _number(latest.get("close"))
    return {
        "symbol": symbol.upper(),
        "official_session_date": session_date,
        "official_close_price": close_price,
        "price": close_price,
        "official_price_source": "Tiingo_after_close_close_only",
        "source": "Tiingo",
        "source_url": "https://www.tiingo.com/",
        "tiingo_close_only": True,
        "close_only_fallback": True,
    }


def _fetch_symbol(symbol: str, token: str, timeout: int) -> Any:
    response = requests.get(
        f"{API_ROOT}/{quote(symbol, safe='')}/prices",
        timeout=timeout,
        headers={
            "Authorization": f"Token {token}",
            "Accept": "application/json",
            "User-Agent": "wude-ai-stock-v6/1.0 private-eod-fallback",
        },
    )
    response.raise_for_status()
    return response.json()


def _with_coverage(payload: dict[str, Any], requested_symbols: Iterable[str]) -> dict[str, Any]:
    requested = {str(symbol).strip().upper() for symbol in requested_symbols if symbol}
    rows = payload.get("rows") if isinstance(payload.get("rows"), dict) else {}
    covered = sorted(requested.intersection(rows))
    return {
        **payload,
        "requested_count": len(requested),
        "covered_requested_count": len(covered),
        "covered_requested_symbols": covered,
        "missing_requested_symbols": sorted(requested.difference(rows)),
    }


def _empty_payload(updated_at: str, status: str, reason: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": updated_at,
        "status": status,
        "cache_status": "missing",
        "configured": False,
        "source": "Tiingo",
        "source_url": "https://www.tiingo.com/",
        "role": "美股收盤價私密涵蓋測試層",
        "after_close_only": True,
        "close_only": True,
        "private_internal_use_only": True,
        "affects_formal_ranking": False,
        "can_create_entries": False,
        "can_settle_open_to_close": False,
        "symbol_count": 0,
        "rows": {},
        "reason": reason,
    }


def _active_or_empty(
    active: dict[str, Any] | None,
    requested: set[str],
    *,
    updated_at: str,
    now_epoch: float,
    status: str,
    cache_status: str,
    reason: str,
    configured: bool,
) -> dict[str, Any]:
    if not active:
        payload = _empty_payload(updated_at, status, reason)
        payload.update({"configured": configured, "cache_status": cache_status})
        return _with_coverage(payload, requested)
    age = _cache_age(active, now_epoch)
    return _with_coverage({
        **active,
        "status": status if age is None or age > MAX_STALE_SECONDS else "stale_fallback",
        "cache_status": cache_status,
        "configured": configured,
        "cache_age_seconds": round(age) if age is not None else None,
        "reason": reason,
    }, requested)


def apply_tiingo_us_close_to_simulation_rows(
    rows: list[dict[str, Any]],
    tiingo: dict[str, Any],
    universe: Iterable[dict[str, Any]],
    *,
    required_symbols: Iterable[str] = (),
) -> tuple[list[dict[str, Any]], list[str]]:
    """Fill missing US closes for owner-only existing-holding valuation.

    The active cache is atomic: this function never reads the staging file.
    Close-only rows are deliberately entry-ineligible, so they cannot create a
    new holding even if a required symbol is absent from the primary rows.
    """
    output = [dict(row) for row in rows]
    indexes = {
        str(row.get("symbol") or "").upper(): index
        for index, row in enumerate(output)
        if str(row.get("market") or "").upper() == "US"
    }
    catalog = {
        str(item.get("symbol") or "").upper(): item
        for item in universe
        if str(item.get("market") or "").upper() == "US"
    }
    requested = set(indexes).union(str(symbol).upper() for symbol in required_symbols)
    fallback_rows = tiingo.get("rows") if isinstance(tiingo.get("rows"), dict) else {}
    applied: list[str] = []
    for symbol in sorted(requested.intersection(fallback_rows)):
        fallback = fallback_rows[symbol]
        if not isinstance(fallback, dict) or fallback.get("tiingo_close_only") is not True:
            continue
        session_date = str(fallback.get("official_session_date") or "")
        close_price = _number(fallback.get("official_close_price"))
        if not session_date or close_price is None:
            continue
        if symbol in indexes:
            current = output[indexes[symbol]]
            current_date = str(current.get("official_session_date") or "")
            current_close = _number(current.get("official_close_price"))
            if current_close is not None or (current_date and current_date != session_date):
                continue
            current.update({
                "official_session_date": session_date,
                "official_close_price": close_price,
                "price": current.get("price") or close_price,
                "official_price_source": "Tiingo_after_close_close_only",
                "tiingo_close_only": True,
                "close_only_fallback": True,
            })
        else:
            item = catalog.get(symbol)
            if not item:
                continue
            output.append({
                "symbol": symbol,
                "name": item.get("name") or symbol,
                "market": "US",
                "type": item.get("type") or "個股",
                "official_session_date": session_date,
                "official_close_price": close_price,
                "official_open_price": None,
                "price": close_price,
                "official_price_source": "Tiingo_after_close_close_only",
                "tiingo_close_only": True,
                "close_only_fallback": True,
                "trade_guard_blocked": True,
                "market_contract_valid": False,
            })
        applied.append(symbol)
    return output, applied


def update_tiingo_us_close_fallback(
    reports_dir: Path,
    *,
    updated_at: str,
    requested_symbols: Iterable[str],
    timeout: int = 20,
    max_requests: int = FREE_BATCH_LIMIT,
    now_epoch: float | None = None,
    now: datetime | None = None,
    api_token: str | None = None,
    fetcher: Callable[[str], Any] | None = None,
    allow_network: bool = True,
) -> dict[str, Any]:
    """Refresh one free-plan batch and atomically promote a completed cycle."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    active_path = reports_dir / "tiingo_us_close_fallback.json"
    staging_path = reports_dir / "tiingo_us_close_staging.json"
    active = _read_json(active_path)
    staging = _read_json(staging_path) or {}
    requested = {
        str(symbol).strip().upper() for symbol in requested_symbols
        if str(symbol).strip()
    }
    now_epoch = time.time() if now_epoch is None else now_epoch
    now = datetime.now(timezone.utc) if now is None else now
    token = (api_token if api_token is not None else os.getenv("TIINGO_API_KEY", "")).strip()

    if not allow_network or _is_us_regular_session(now):
        return _active_or_empty(
            active, requested, updated_at=updated_at, now_epoch=now_epoch,
            status="waiting_for_us_close", cache_status="us_market_open_hold",
            reason="美股盤中禁止抓價；等待正式收盤後更新 Tiingo 備援",
            configured=bool(token),
        )
    if not token and fetcher is None:
        return _active_or_empty(
            active, requested, updated_at=updated_at, now_epoch=now_epoch,
            status="unconfigured", cache_status="credential_missing_hold",
            reason="尚未設定 TIINGO_API_KEY；不連線、不影響既有報告",
            configured=False,
        )
    if not requested:
        return _with_coverage(
            _empty_payload(updated_at, "not_needed", "沒有需要備援的美股代碼"),
            requested,
        )

    staged_rows = {
        str(symbol).upper(): dict(row)
        for symbol, row in (staging.get("rows") or {}).items()
        if str(symbol).upper() in requested and isinstance(row, dict)
    }
    attempted = {
        str(symbol).upper() for symbol in (staging.get("attempted_symbols") or [])
        if str(symbol).upper() in requested
    }
    target_session = str(staging.get("target_session_date") or "")
    remaining = sorted(requested.difference(attempted))
    if not remaining:
        staged_rows, attempted, target_session = {}, set(), ""
        remaining = sorted(requested)
    batch = remaining[:max(1, min(int(max_requests), FREE_BATCH_LIMIT))]

    results: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}

    def get_one(symbol: str) -> tuple[str, dict[str, Any] | None, str | None]:
        try:
            raw = fetcher(symbol) if fetcher is not None else _fetch_symbol(symbol, token, timeout)
            return symbol, parse_tiingo_eod(raw, symbol), None
        except Exception as exc:  # noqa: BLE001 - provider boundary
            return symbol, None, f"{type(exc).__name__}: {str(exc)[:140]}"

    workers = min(6, len(batch))
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(get_one, symbol) for symbol in batch]
        for future in as_completed(futures):
            symbol, row, error = future.result()
            if row:
                results[symbol] = row
            elif error:
                errors[symbol] = error

    attempted.update(batch)
    result_dates = [str(row.get("official_session_date") or "") for row in results.values()]
    newest_date = max(result_dates) if result_dates else ""
    if newest_date and (not target_session or newest_date > target_session):
        # A new completed US session appeared while an older cycle was being
        # collected. Discard the older staging rows instead of mixing dates.
        target_session = newest_date
        staged_rows = {}
        attempted = set(batch)
    elif not target_session and result_dates:
        target_session = Counter(result_dates).most_common(1)[0][0]
    for symbol, row in results.items():
        if str(row.get("official_session_date") or "") == target_session:
            staged_rows[symbol] = row

    staging_payload = {
        "schema_version": SCHEMA_VERSION,
        "updated_at": updated_at,
        "target_session_date": target_session,
        "requested_symbols": sorted(requested),
        "attempted_symbols": sorted(attempted),
        "attempted_count": len(attempted),
        "batch_symbols": batch,
        "batch_success_count": len(results),
        "batch_error_count": len(errors),
        "batch_errors": errors,
        "rows": staged_rows,
    }
    _write_json(staging_path, staging_payload)

    if attempted >= requested:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "updated_at": updated_at,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "fetched_epoch": now_epoch,
            "status": "ok" if staged_rows else "unavailable",
            "cache_status": "promoted_complete_cycle",
            "configured": True,
            "source": "Tiingo",
            "source_url": "https://www.tiingo.com/",
            "role": "美股既有中長期持倉私密收盤備援層",
            "after_close_only": True,
            "close_only": True,
            "private_internal_use_only": True,
            "affects_formal_ranking": False,
            "can_create_entries": False,
            "can_settle_open_to_close": False,
            "official_session_date": target_session,
            "symbol_count": len(staged_rows),
            "attempted_count": len(attempted),
            "rows": staged_rows,
            "warning": "免費版分批完成後才切換私密快取；只補本人版既有中長期持倉估值，原始價格不進公開網站、五日結算或排名。",
        }
        _write_json(active_path, payload)
        try:
            staging_path.unlink()
        except FileNotFoundError:
            pass
        return _with_coverage(payload, requested)

    base = active or _empty_payload(
        updated_at, "collecting", "Tiingo免費版正在分批建立完整收盤備援快取"
    )
    age = _cache_age(active, now_epoch) if active else None
    return _with_coverage({
        **base,
        "updated_at": updated_at,
        "status": "collecting",
        "cache_status": "staging_not_promoted",
        "configured": True,
        "cache_age_seconds": round(age) if age is not None else None,
        "staging_session_date": target_session,
        "staging_attempted_count": len(attempted),
        "staging_requested_count": len(requested),
        "staging_available_count": len(staged_rows),
        "batch_success_count": len(results),
        "batch_error_count": len(errors),
        "reason": "分批尚未完成，沿用上一份完整快取；不使用半套資料",
    }, requested)
