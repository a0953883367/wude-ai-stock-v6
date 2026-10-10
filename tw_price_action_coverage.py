"""Bounded, official Taiwan price-basis event coverage for frozen plans.

This is not an adjusted-price calculator or an identity/merger/halt check.
Callers must establish TWSE-listed or TPEx-mainboard membership independently
(for example with the existing official daily-candle parser). Emerging stocks
are outside these feeds. No raw reports are persisted or returned by the fetch
helper. Current OpenAPI announcement snapshots cannot prove historical absence.

Source contracts were checked against official report pages and live responses
on 2026-10-10. TWSE TWT49U explicitly excludes dividends combined with capital
reductions, hence the reduction, par-value and ETF split reports are mandatory.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import re
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode, urlsplit

VERSION = "TW-PRICE-ACTION-COVERAGE-V1"
MAX_WINDOW_DAYS = 60
TAIPEI = timezone(timedelta(hours=8))
NO_MATCH = "很抱歉，沒有符合條件的資料!"


def _spec(url: str, venue: str, family: str, since: str, date_field: str,
          code_field: str, price_field: str) -> dict[str, str]:
    return {"url": url, "venue": venue, "family": family, "available_from": since,
            "date_field": date_field, "code_field": code_field, "price_field": price_field}


TWSE = "https://www.twse.com.tw/rwd/zh"
TPEX = "https://www.tpex.org.tw/www/zh-tw/bulletin"
SOURCES = {
    "twse_ex_right_dividend": _spec(f"{TWSE}/exRight/TWT49U", "TWSE", "ex_right_dividend", "2003-05-05", "資料日期", "股票代號", "除權息參考價"),
    # 'reducation' is the exchange's actual spelling, not a typo here.
    "twse_capital_reduction": _spec(f"{TWSE}/reducation/TWTAUU", "TWSE", "capital_reduction", "2011-01-01", "恢復買賣日期", "股票代號", "恢復買賣參考價"),
    "twse_par_value_change": _spec(f"{TWSE}/change/TWTB8U", "TWSE", "par_value_change", "2019-01-01", "恢復買賣日期", "股票代號", "恢復買賣參考價"),
    "twse_etf_split_reverse": _spec(f"{TWSE}/split/TWTCAU", "TWSE", "etf_split_reverse", "2024-01-01", "恢復買賣日期", "ETF代號", "恢復買賣參考價"),
    "tpex_ex_right_dividend": _spec(f"{TPEX}/exDailyQ", "TPEX_MAINBOARD", "ex_right_dividend", "2008-01-02", "除權息日期", "代號", "除權息參考價"),
    "tpex_capital_reduction": _spec(f"{TPEX}/revivt", "TPEX_MAINBOARD", "capital_reduction", "2013-01-01", "恢復買賣日期", "股票代號", "減資恢復買賣開始日參考價格"),
    "tpex_par_value_change": _spec(f"{TPEX}/pvChgRslt", "TPEX_MAINBOARD", "par_value_change", "2019-09-09", "恢復買賣日期", "證券代號", "恢復買賣開始參考價"),
    "tpex_etf_split": _spec(f"{TPEX}/etfSplitRslt", "TPEX_MAINBOARD", "etf_split", "2021-06-01", "恢復買賣日期", "證券代號", "恢復買賣開始參考價"),
    "tpex_etf_reverse_split": _spec(f"{TPEX}/etfRvsRslt", "TPEX_MAINBOARD", "etf_reverse_split", "2021-06-01", "恢復買賣日期", "證券代號", "恢復買賣開始參考價"),
}


def _date(value: Any) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("invalid_date")
    return date.fromisoformat(value)


def _event_date(value: Any) -> str:
    text = str(value).strip()
    match = re.fullmatch(r"(\d{2,4})(?:年|/|-)(\d{1,2})(?:月|/|-)(\d{1,2})日?", text)
    if match:
        year, month, day = map(int, match.groups())
    elif re.fullmatch(r"\d{7,8}", text):
        year, month, day = int(text[:-4]), int(text[-4:-2]), int(text[-2:])
    else:
        raise ValueError("invalid_event_date")
    return date(year + 1911 if year < 1911 else year, month, day).isoformat()


def _stamp(value: Any) -> datetime:
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timestamp_requires_timezone")
    return result


def _window(original: str, through: str) -> None:
    span = (_date(through) - _date(original)).days
    if not 0 <= span < MAX_WINDOW_DAYS:
        raise ValueError("invalid_or_over_60_day_window")


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def source_request_url(source: str, original_session: str, through_session: str) -> str:
    """Only established official endpoints and unfiltered, <=60-day queries."""
    _window(original_session, through_session)
    spec = SOURCES[source]
    fmt = "%Y%m%d" if spec["venue"] == "TWSE" else "%Y/%m/%d"
    return spec["url"] + "?" + urlencode({
        "startDate": _date(original_session).strftime(fmt),
        "endDate": _date(through_session).strftime(fmt), "response": "json"})


def _check_request(source: str, envelope: dict) -> None:
    expected = urlsplit(source_request_url(source, envelope["coverage_from"], envelope["through_session"]))
    actual = urlsplit(envelope.get("request_url", ""))
    if (actual.scheme, actual.netloc, actual.path, actual.fragment) != (expected.scheme, expected.netloc, expected.path, ""):
        raise ValueError("request_endpoint_mismatch")
    if parse_qs(actual.query, keep_blank_values=True) != parse_qs(expected.query):
        raise ValueError("request_scope_mismatch")


def _rows(source: str, envelope: dict) -> list[dict[str, Any]]:
    spec = SOURCES[source]
    _check_request(source, envelope)
    start, end = envelope["coverage_from"], envelope["through_session"]
    if start < spec["available_from"]:
        raise ValueError("before_source_available_range")
    raw = envelope.get("payload")
    if not isinstance(raw, dict):
        raise ValueError("invalid_report_payload")
    # These two ranged TWSE endpoints omit echoed dates/fields on no match.
    # Accept only their exact observed response AND a complete request envelope;
    # a bare empty list or an arbitrary error string can never establish absence.
    if source in {"twse_ex_right_dividend", "twse_capital_reduction"} and raw == {"stat": NO_MATCH}:
        return []
    if str(raw.get("stat", "")).lower() != "ok":
        raise ValueError("report_not_successful")
    compact = (start.replace("-", ""), end.replace("-", ""))
    if spec["venue"] == "TWSE":
        if source == "twse_par_value_change":
            params = raw.get("params")
            if not isinstance(params, dict):
                raise ValueError("missing_response_range")
            span = (params.get("startDate"), params.get("endDate"))
        else:
            span = (raw.get("strDate", raw.get("startDate")), raw.get("endDate"))
        if span != compact:
            raise ValueError("response_range_mismatch")
        table = raw
    else:
        if raw.get("date") != "~".join(compact):
            raise ValueError("response_range_mismatch")
        tables = raw.get("tables")
        if not isinstance(tables, list) or len(tables) != 1 or not isinstance(tables[0], dict):
            raise ValueError("unexpected_report_tables")
        table = tables[0]
        count = table.get("totalCount")
        if type(count) is not int or count < 0 or not isinstance(table.get("data"), list) or count != len(table["data"]):
            raise ValueError("incomplete_report_count")
    fields, data = table.get("fields"), table.get("data")
    if not isinstance(fields, list) or not all(isinstance(field, str) for field in fields):
        raise ValueError("invalid_report_fields")
    if len(set(fields)) != len(fields) or not {spec["date_field"], spec["code_field"], spec["price_field"]} <= set(fields):
        raise ValueError("report_schema_mismatch")
    if not isinstance(data, list):
        raise ValueError("invalid_report_rows")
    events, seen = [], set()
    for row in data:
        if not isinstance(row, list) or len(row) != len(fields):
            raise ValueError("malformed_report_row")
        record = dict(zip(fields, row))
        code = str(record[spec["code_field"]]).strip()
        if not re.fullmatch(r"[0-9][0-9A-Z]{2,7}", code):
            raise ValueError("invalid_event_symbol")
        session = _event_date(record[spec["date_field"]])
        if not start <= session <= end:
            raise ValueError("event_outside_report_range")
        key = (code, session)
        if key in seen:
            raise ValueError("duplicate_event_row")
        seen.add(key)
        events.append({"symbol": code + (".TW" if spec["venue"] == "TWSE" else ".TWO"),
                       "effective_session": session, "family": spec["family"], "source_id": source,
                       "raw_record_sha256": _digest(row)})
    return events


def evaluate_tw_price_action_coverage(payload: Any, *, symbol: str,
                                      original_session: str, through_session: str,
                                      observed_at: str) -> dict[str, Any]:
    """Evaluate explicitly complete official report envelopes, failing closed.

    ``payload`` is {venue: TWSE|TPEX_MAINBOARD, sources: [envelope, ...]}.
    Every envelope requires source_id, complete=True, coverage_from,
    through_session, observed_at, exact request_url, and the untouched payload.
    Complete means all rows for that ranged, unfiltered report were retrieved;
    it must not be inferred from zero symbol matches. A positive validated event
    can block even if some other required family is unavailable.
    """
    result = {"version": VERSION, "status": "unsupported", "symbol": symbol,
              "observed_at": observed_at, "coverage_from": original_session,
              "through_session": through_session, "source_ids": [], "events": [],
              "reasons": [], "sources": [], "scope": "price_basis_events_only"}
    try:
        _window(original_session, through_session)
        observed = _stamp(observed_at)
        close = datetime.combine(_date(through_session), time(13, 30), TAIPEI)
        if observed < close:
            raise ValueError("through_session_not_closed")
        if not isinstance(payload, dict) or payload.get("venue") not in {"TWSE", "TPEX_MAINBOARD"}:
            raise ValueError("venue_scope_unverified")
        venue = payload["venue"]
        suffix = ".TW" if venue == "TWSE" else ".TWO"
        if not isinstance(symbol, str) or not re.fullmatch(r"[0-9][0-9A-Z]{2,7}" + re.escape(suffix), symbol):
            raise ValueError("symbol_venue_mismatch")
        envelopes = payload.get("sources")
        if not isinstance(envelopes, list) or not all(isinstance(item, dict) for item in envelopes):
            raise ValueError("invalid_source_envelopes")
        needed = {name for name, spec in SOURCES.items() if spec["venue"] == venue}
        for source in sorted(needed):
            matches = [item for item in envelopes if item.get("source_id") == source]
            try:
                if len(matches) != 1:
                    raise ValueError("missing_or_duplicate_source")
                item = matches[0]
                if item.get("complete") is not True:
                    raise ValueError("coverage_not_declared_complete")
                if item.get("coverage_from") != original_session or item.get("through_session") != through_session:
                    raise ValueError("envelope_range_mismatch")
                fetched = _stamp(item.get("observed_at"))
                if not close <= fetched <= observed:
                    raise ValueError("stale_or_future_source_observation")
                events = _rows(source, item)
                result["source_ids"].append(source)
                result["sources"].append({"source_id": source, "source_url": SOURCES[source]["url"],
                                           "observed_at": item["observed_at"], "record_count": len(events),
                                           "source_payload_sha256": _digest(item["payload"])})
                # The original close already incorporates that session's action.
                result["events"].extend(event for event in events if event["symbol"] == symbol
                                         and original_session < event["effective_session"] <= through_session)
            except (ValueError, TypeError, KeyError, OverflowError) as exc:
                result["reasons"].append(source + ":" + str(exc))
        if result["events"]:
            result["status"] = "blocked"
        elif not result["reasons"]:
            result["status"] = "clear"
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        result["reasons"].append(str(exc))
    return result


def fetch_tw_price_action_coverage(symbols: set[str], original_session: str,
                                   through_session: str, *, now: datetime | None = None,
                                   clock: Callable[[], datetime] | None = None,
                                   fetch_json: Callable[[str], Any] | None = None) -> dict[str, dict]:
    """At most four TWSE and five TPEx bulk requests; never per-symbol fetches.

    The caller supplies only independently verified listed/mainboard symbols.
    Windows are at most 60 calendar days inclusive. All data remain in memory.
    ``now`` is an injectable test clock, not a historical-replay cutoff.
    """
    if now is not None and clock is not None:
        raise ValueError("provide_now_or_clock_not_both")
    read_clock = clock or (lambda: now or datetime.now(timezone.utc))
    observed = _stamp(read_clock().isoformat())
    normalized = {str(symbol).strip().upper() for symbol in symbols}
    empty = lambda symbol, reason: {"version": VERSION, "status": "unsupported", "symbol": symbol,
                                    "observed_at": observed.isoformat(), "coverage_from": original_session,
                                    "through_session": through_session, "source_ids": [], "events": [],
                                    "reasons": [reason], "sources": [], "scope": "price_basis_events_only"}
    try:
        _window(original_session, through_session)
        if observed < datetime.combine(_date(through_session), time(13, 30), TAIPEI):
            raise ValueError("through_session_not_closed")
    except (ValueError, TypeError) as exc:
        return {symbol: empty(symbol, str(exc)) for symbol in normalized}
    if not normalized:
        return {}
    if fetch_json is None:
        from tw_official_data import _get_json
        fetch_json = _get_json
    venues = {"TWSE" if symbol.endswith(".TW") else "TPEX_MAINBOARD"
              for symbol in normalized if re.fullmatch(r"[0-9][0-9A-Z]{2,7}\.(?:TW|TWO)", symbol)}
    requested = [source for source, spec in SOURCES.items() if spec["venue"] in venues]

    def fetch(source: str) -> dict:
        url = source_request_url(source, original_session, through_session)
        envelope = {"source_id": source, "request_url": url, "coverage_from": original_session,
                    "through_session": through_session, "complete": False}
        try:
            envelope["payload"] = fetch_json(url)
            envelope["observed_at"] = _stamp(read_clock().isoformat()).isoformat()
            _rows(source, envelope)
            envelope["complete"] = True
        except Exception as exc:
            envelope["error_type"] = type(exc).__name__
            envelope["error"] = str(exc)[:160]
        return envelope

    with ThreadPoolExecutor(max_workers=4) as pool:
        envelopes = list(pool.map(fetch, requested))
    finished = _stamp(read_clock().isoformat()).isoformat()
    results = {}
    for symbol in sorted(normalized):
        if not re.fullmatch(r"[0-9][0-9A-Z]{2,7}\.(?:TW|TWO)", symbol):
            results[symbol] = empty(symbol, "unsupported_symbol")
            continue
        venue = "TWSE" if symbol.endswith(".TW") else "TPEX_MAINBOARD"
        results[symbol] = evaluate_tw_price_action_coverage(
            {"venue": venue, "sources": envelopes}, symbol=symbol,
            original_session=original_session, through_session=through_session, observed_at=finished)
        for envelope in envelopes:
            if SOURCES[envelope["source_id"]]["venue"] == venue and envelope.get("error_type"):
                results[symbol]["reasons"].append(envelope["source_id"] + ":" + envelope["error_type"] + ":" + envelope["error"])
    return results


def fetch_tw_price_action_coverage_for_plans(requests: list[dict], *,
                                            now: datetime | None = None,
                                            clock: Callable[[], datetime] | None = None,
                                            fetch_json: Callable[[str], Any] | None = None) -> dict[str, dict]:
    """Batch by through-session, then assess each immutable plan's own interval.

    Input rows require plan_id, symbol, original_session and through_session.
    Each group fetches at most nine exchange/family reports, regardless of the
    number of symbols or plans. Ineligible (>60-day) windows stay unsupported;
    they cannot widen a valid group's request. Evidence retains its actual bulk
    coverage_from while events are filtered after each plan's source close.
    """
    if now is not None and clock is not None:
        raise ValueError("provide_now_or_clock_not_both")
    if not isinstance(requests, list) or not all(isinstance(item, dict) and isinstance(item.get("plan_id"), str)
                                                and item["plan_id"] for item in requests):
        raise ValueError("plan_requests_require_nonempty_plan_ids")
    read_clock = clock or (lambda: now or datetime.now(timezone.utc))
    observed_at = _stamp(read_clock().isoformat()).isoformat()
    results, groups = {}, {}
    counts: dict[str, int] = {}
    for item in requests:
        counts[item["plan_id"]] = counts.get(item["plan_id"], 0) + 1
    for item in requests:
        plan_id = item["plan_id"]
        try:
            if counts[plan_id] != 1:
                raise ValueError("duplicate_plan_id")
            _window(item.get("original_session"), item.get("through_session"))
            symbol = item.get("symbol")
            if not isinstance(symbol, str) or not re.fullmatch(r"[0-9][0-9A-Z]{2,7}\.(?:TW|TWO)", symbol):
                raise ValueError("unsupported_symbol")
            groups.setdefault(item["through_session"], []).append(item)
        except (ValueError, TypeError) as exc:
            results[plan_id] = {"version": VERSION, "status": "unsupported", "symbol": item.get("symbol"),
                                "observed_at": observed_at, "coverage_from": item.get("original_session"),
                                "through_session": item.get("through_session"), "source_ids": [],
                                "events": [], "reasons": [str(exc)], "sources": [],
                                "scope": "price_basis_events_only"}
    for through, plans in sorted(groups.items()):
        earliest = min(item["original_session"] for item in plans)
        bulk = fetch_tw_price_action_coverage({item["symbol"] for item in plans}, earliest, through,
                                             now=now, clock=clock, fetch_json=fetch_json)
        for item in plans:
            evidence = deepcopy(bulk[item["symbol"]])
            evidence["plan_original_session"] = item["original_session"]
            evidence["events"] = [event for event in evidence["events"]
                                  if item["original_session"] < event["effective_session"] <= through]
            evidence["status"] = ("blocked" if evidence["events"] else
                                  "unsupported" if evidence["reasons"] else "clear")
            results[item["plan_id"]] = evidence
    return results
