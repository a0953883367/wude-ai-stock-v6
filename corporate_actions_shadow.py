#!/usr/bin/env python3
"""Monitor official security identity and trading-status changes in shadow mode.

The monitor deliberately never edits ``search_data.json``, rankings, model
weights, historical prices, or broker settings.  Official identity changes are
recorded as reviewable events so a missing upstream row can never silently
delete a security or splice one company's history onto another.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import re
from typing import Any, Iterable
import xml.etree.ElementTree as ET

import requests


SOURCE_URLS = {
    "twse_registry": "https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
    "tpex_registry": "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O",
    "sec_registry": "https://www.sec.gov/files/company_tickers_exchange.json",
    "sec_registry_fallback": "https://www.sec.gov/files/company_tickers.json",
    "twse_announcements": "https://openapi.twse.com.tw/v1/opendata/t187ap04_L",
    "tpex_announcements": "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap04_O",
    # Nasdaq documents the pipe-delimited Symbol Directory as its machine
    # download surface.  The human-facing RSS endpoint can return an HTML
    # challenge to hosted CI runners, which is not valid XML.
    "nasdaq_halts": "https://www.nasdaqtrader.com/dynamic/SymDir/tradinghalts.txt",
}

MAX_EVENTS = 200
MAX_HISTORY = 104
MISSING_CONFIRMATION_RUNS = 2

POLICY = {
    "shadow_only": True,
    "updates_active_universe": False,
    "updates_display_names": False,
    "joins_price_history": False,
    "deletes_history": False,
    "changes_rankings": False,
    "changes_weights": False,
    "places_orders": False,
    "requires_manual_approval": True,
    "missing_row_is_not_delisting": True,
}

EVENT_LABELS = {
    "NAME_CHANGE": "公司更名",
    "SYMBOL_CHANGE": "股票代號變更",
    "IDENTITY_CONFLICT": "公司身分衝突",
    "MISSING_FROM_REGISTRY": "官方名單暫時缺少",
    "CONFIRMED_ABSENT": "連續缺少官方名單",
    "TRADING_HALT": "暫停交易",
    "TRADING_RESUMED": "恢復交易",
    "DELISTING": "下市／終止上市櫃",
    "MERGER_OR_SHARE_EXCHANGE": "合併／換股",
}


class SourceError(RuntimeError):
    """An official source could not be read or normalized."""


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _text(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _normalized_name(value: str) -> str:
    text = re.sub(r"[\s　]+", "", str(value or "")).upper()
    return re.sub(r"(?:股份有限公司|有限公司|CORPORATION|CORP\.?|INC\.?|LTD\.?)$", "", text)


def _tracked_stocks(active_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    tracked: dict[str, dict[str, Any]] = {}
    for row in active_payload.get("data") or []:
        if not isinstance(row, dict) or str(row.get("類型") or "個股") != "個股":
            continue
        symbol = str(row.get("代號") or "").strip().upper()
        market_text = str(row.get("市場") or "")
        if not symbol:
            continue
        if "台灣" in market_text and symbol.endswith((".TW", ".TWO")):
            market = "TW"
        elif "美國" in market_text and not symbol.endswith((".TW", ".TWO")):
            market = "US"
        else:
            continue
        tracked[symbol] = {
            "symbol": symbol,
            "display_name": str(row.get("股票") or symbol),
            "market": market,
            "type": "個股",
        }
    return tracked


def normalize_tw_registry(rows: Iterable[dict[str, Any]], *, exchange: str) -> list[dict[str, Any]]:
    suffix = ".TW" if exchange == "TWSE" else ".TWO"
    source = "twse_registry" if exchange == "TWSE" else "tpex_registry"
    normalized = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = _text(row, "公司代號", "SecuritiesCompanyCode", "公司代碼")
        business_no = _text(
            row,
            "營利事業統一編號",
            "營利事業統一編號.",
            "UnifiedBusinessNo",
            "UnifiedBusinessNo.",
        )
        if not code:
            continue
        symbol = f"{code.upper()}{suffix}"
        normalized.append({
            "symbol": symbol,
            "market": "TW",
            "exchange": exchange,
            "name": _text(row, "公司簡稱", "CompanyAbbreviation", "公司名稱", "CompanyName") or code,
            "legal_name": _text(row, "公司名稱", "CompanyName"),
            "entity_id": f"TW-BN-{business_no}" if business_no else "",
            "source": source,
        })
    return normalized


def normalize_sec_registry(payload: dict[str, Any]) -> list[dict[str, Any]]:
    fields = payload.get("fields") or ["cik", "name", "ticker", "exchange"]
    rows = payload.get("data") or []
    if not rows:
        # SEC's company_tickers.json fallback is keyed by row number rather
        # than using the columnar fields/data form.
        rows = [
            [row.get("cik_str"), row.get("title"), row.get("ticker"), ""]
            for row in payload.values()
            if isinstance(row, dict)
        ]
    normalized = []
    for values in rows:
        if not isinstance(values, list):
            continue
        row = dict(zip(fields, values))
        symbol = str(row.get("ticker") or "").strip().upper()
        cik = str(row.get("cik") or "").strip()
        if not symbol or not cik:
            continue
        normalized.append({
            "symbol": symbol,
            "market": "US",
            "exchange": str(row.get("exchange") or ""),
            "name": str(row.get("name") or symbol).strip(),
            "legal_name": str(row.get("name") or "").strip(),
            "entity_id": f"US-CIK-{cik.zfill(10)}",
            "source": "sec_registry",
        })
    return normalized


def _classify_announcement(text: str) -> str:
    compact = str(text or "")
    patterns = (
        ("TRADING_RESUMED", r"恢復(?:交易|買賣)"),
        ("TRADING_HALT", r"(?:暫停|停止)(?:交易|買賣)"),
        ("DELISTING", r"(?:終止上市|終止上櫃|下市|下櫃)"),
        ("SYMBOL_CHANGE", r"(?:股票|證券)?代號.{0,8}(?:變更|改為)"),
        ("NAME_CHANGE", r"(?:公司)?(?:名稱|簡稱).{0,8}(?:變更|更名|改為)"),
        ("MERGER_OR_SHARE_EXCHANGE", r"(?:合併|股份轉換|換股)"),
    )
    for event_type, pattern in patterns:
        if re.search(pattern, compact):
            return event_type
    return ""


def normalize_tw_announcements(
    rows: Iterable[dict[str, Any]], *, exchange: str
) -> list[dict[str, Any]]:
    suffix = ".TW" if exchange == "TWSE" else ".TWO"
    source = "twse_announcements" if exchange == "TWSE" else "tpex_announcements"
    events = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = _text(row, "公司代號", "SecuritiesCompanyCode", "CompanyCode")
        subject = _text(row, "主旨", "Subject")
        description = _text(row, "說明", "Description")
        event_type = _classify_announcement(subject + " " + description)
        if not code or not event_type:
            continue
        events.append({
            "type": event_type,
            "symbol": f"{code.upper()}{suffix}",
            "source": source,
            "source_date": _text(row, "發言日期", "Date", "DateOfAnnouncement", "事實發生日", "DateOfOccurrence"),
            "headline": subject,
        })
    return events


def _strip_html(value: str) -> str:
    decoded = html.unescape(str(value or ""))
    decoded = re.sub(r"<br\s*/?>", "\n", decoded, flags=re.I)
    return re.sub(r"<[^>]+>", " ", decoded)


def _xml_item_fields(item: ET.Element) -> dict[str, str]:
    """Read both ordinary RSS fields and Nasdaq namespaced fields."""
    values: dict[str, str] = {}
    for child in item:
        local_name = child.tag.rsplit("}", 1)[-1].lower()
        values[local_name] = str(child.text or "").strip()
    return values


def parse_nasdaq_halts(xml_text: str) -> list[dict[str, Any]]:
    if not str(xml_text or "").strip():
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise SourceError(f"invalid Nasdaq halt RSS: {exc}") from exc
    events = []
    for item in root.findall(".//item"):
        fields = _xml_item_fields(item)
        title = fields.get("title", "")
        description = _strip_html(fields.get("description", ""))
        body = f"{title}\n{description}"
        symbol = fields.get("issuesymbol", "").upper()
        if not symbol:
            match = re.search(
                r"(?:Issue\s+Symbol|Symbol)\s*:\s*([A-Z0-9.\-]+)", body, re.I
            )
            if not match:
                match = re.search(r"(?:halt|pause)\s*[:\-]?\s*([A-Z][A-Z0-9.\-]{0,9})\b", title, re.I)
            symbol = match.group(1).upper() if match else ""
        if not symbol:
            continue
        resume_values = [
            fields.get("resumptiondate", ""),
            fields.get("resumptionquotetime", ""),
            fields.get("resumptiontradetime", ""),
        ]
        resumed_in_fields = any(value.strip().upper() not in {"", "N/A"} for value in resume_values)
        resumed_in_body = re.search(r"Resumption\s+(?:Date|Quote\s+Time|Trade\s+Time|Time)\s*:\s*([^\n<]+)", body, re.I)
        resumed = resumed_in_fields or bool(
            resumed_in_body and resumed_in_body.group(1).strip().upper() not in {"", "N/A"}
        )
        reason = fields.get("reasoncode", "").upper()
        event_type = "DELISTING" if reason == "D" else "TRADING_RESUMED" if resumed else "TRADING_HALT"
        events.append({
            "type": event_type,
            "symbol": symbol,
            "source": "nasdaq_halts",
            "source_date": fields.get("haltdate") or fields.get("pubdate", ""),
            "headline": title.strip() or f"Nasdaq trading halt: {symbol}",
        })
    return events


def parse_nasdaq_halt_directory(text: str) -> list[dict[str, Any]]:
    """Parse Nasdaq Trader's documented pipe-delimited halt directory."""
    lines = [line.strip("\ufeff\r") for line in str(text or "").splitlines() if line.strip()]
    if not lines:
        return []
    header = [value.strip().lower() for value in lines[0].split("|")]
    required = {"halt date", "issue symbol", "reason codes"}
    if not required.issubset(header):
        raise SourceError("invalid Nasdaq trading-halt directory header")
    events = []
    for line in lines[1:]:
        if line.lower().startswith("file creation time"):
            continue
        values = line.split("|")
        row = dict(zip(header, values))
        symbol = str(row.get("issue symbol") or "").strip().upper()
        if not symbol:
            continue
        resumed = any(
            str(row.get(key) or "").strip().upper() not in {"", "N/A"}
            for key in ("resumption date", "resumption quote time", "resumption trade time")
        )
        reason = str(row.get("reason codes") or "").strip().upper()
        event_type = "DELISTING" if reason == "D" else "TRADING_RESUMED" if resumed else "TRADING_HALT"
        events.append({
            "type": event_type,
            "symbol": symbol,
            "source": "nasdaq_halts",
            "source_date": str(row.get("halt date") or "").strip(),
            "headline": f"Nasdaq trading halt {reason}: {symbol}".strip(),
        })
    return events


def _event_id(event: dict[str, Any]) -> str:
    material = "|".join(str(event.get(key) or "") for key in (
        "type", "entity_id", "old_symbol", "new_symbol", "symbol", "source", "source_date", "headline"
    ))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]


def _event(event_type: str, *, level: str = "warning", **values: Any) -> dict[str, Any]:
    event = {
        "type": event_type,
        "label": EVENT_LABELS[event_type],
        "level": level,
        **values,
    }
    event["event_id"] = _event_id(event)
    event["requires_manual_approval"] = True
    return event


def build_shadow_report(
    active_payload: dict[str, Any],
    previous_registry: dict[str, Any],
    registry_sources: list[dict[str, Any]],
    announcement_events: list[dict[str, Any]],
    halt_events: list[dict[str, Any]],
    *,
    generated_at: str,
    event_source_health: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    tracked = _tracked_stocks(active_payload)
    previous_records = previous_registry.get("records") if isinstance(previous_registry.get("records"), dict) else {}
    previous_missing = previous_registry.get("missing_observations") if isinstance(previous_registry.get("missing_observations"), dict) else {}
    source_health = {}
    official: dict[str, dict[str, Any]] = {}
    for source in registry_sources:
        name = str(source.get("source") or "unknown")
        ok = source.get("ok") is True
        records = source.get("records") if isinstance(source.get("records"), list) else []
        source_health[name] = {
            "ok": ok,
            "record_count": len(records),
            "error": str(source.get("error") or ""),
        }
        if ok:
            for record in records:
                if isinstance(record, dict) and record.get("symbol"):
                    official[str(record["symbol"]).upper()] = dict(record)
    source_health.update(event_source_health or {})

    events: list[dict[str, Any]] = []
    current_records: dict[str, dict[str, Any]] = {}
    missing_observations: dict[str, int] = {}
    previous_by_entity = {
        str(row.get("entity_id")): row
        for row in previous_records.values()
        if isinstance(row, dict) and row.get("entity_id")
    }
    current_by_entity = {
        str(row.get("entity_id")): row
        for row in official.values()
        if isinstance(row, dict) and row.get("entity_id")
    }
    is_baseline = not bool(previous_records)

    for symbol, tracked_row in tracked.items():
        required_source = "sec_registry"
        if symbol.endswith(".TWO"):
            required_source = "tpex_registry"
        elif symbol.endswith(".TW"):
            required_source = "twse_registry"
        source_ok = bool((source_health.get(required_source) or {}).get("ok"))
        current = official.get(symbol)
        previous = previous_records.get(symbol) if isinstance(previous_records.get(symbol), dict) else None
        if current:
            current = {**current, "display_name": tracked_row["display_name"], "present": True}
            current_records[symbol] = current
            if previous:
                old_entity = str(previous.get("entity_id") or "")
                new_entity = str(current.get("entity_id") or "")
                if old_entity and new_entity and old_entity != new_entity:
                    events.append(_event(
                        "IDENTITY_CONFLICT", level="critical", symbol=symbol,
                        old_entity_id=old_entity, entity_id=new_entity,
                        source=current.get("source"),
                        action="凍結該代號的影子接續；核對公司識別碼與生效日，禁止拼接歷史價格",
                    ))
                elif _normalized_name(str(previous.get("name") or "")) != _normalized_name(str(current.get("name") or "")):
                    events.append(_event(
                        "NAME_CHANGE", level="info", symbol=symbol,
                        entity_id=new_entity or old_entity,
                        old_name=previous.get("name"), new_name=current.get("name"),
                        source=current.get("source"),
                        action="僅建立顯示名稱更新候選；歷史價格、預測與學習紀錄保持原代號接續",
                    ))
            continue

        if not source_ok:
            if previous:
                current_records[symbol] = {**previous, "present": None, "source_unavailable": True}
            continue

        count = int(previous_missing.get(symbol) or 0) + 1
        missing_observations[symbol] = count
        if previous:
            current_records[symbol] = {**previous, "present": False, "missing_observations": count}
        if not is_baseline:
            event_type = "CONFIRMED_ABSENT" if count >= MISSING_CONFIRMATION_RUNS else "MISSING_FROM_REGISTRY"
            events.append(_event(
                event_type, symbol=symbol,
                entity_id=(previous or {}).get("entity_id"),
                source=required_source,
                consecutive_observations=count,
                action="停止把缺值冒充今日價格並等待人工核對；不得刪除股票或歷史資料",
            ))

    if not is_baseline:
        tracked_symbols = set(tracked)
        for entity_id, previous in previous_by_entity.items():
            current = current_by_entity.get(entity_id)
            if not current:
                continue
            old_symbol = str(previous.get("symbol") or "").upper()
            new_symbol = str(current.get("symbol") or "").upper()
            if old_symbol in tracked_symbols and new_symbol and old_symbol != new_symbol:
                events.append(_event(
                    "SYMBOL_CHANGE", old_symbol=old_symbol, new_symbol=new_symbol,
                    symbol=old_symbol, entity_id=entity_id, source=current.get("source"),
                    action="建立舊代號到新代號候選對照；核對生效日後才可改抓新代號，舊歷史不得刪除",
                ))

    tracked_symbols = set(tracked)
    for raw in [*announcement_events, *halt_events]:
        if not isinstance(raw, dict) or str(raw.get("symbol") or "").upper() not in tracked_symbols:
            continue
        event_type = str(raw.get("type") or "")
        if event_type not in EVENT_LABELS:
            continue
        actions = {
            "TRADING_RESUMED": "只解除影子暫停標記；下一個有效交易日重新抓取，不回填不存在的成交價",
            "TRADING_HALT": "暫停該股影子買進資格並等待官方恢復訊息；正式名單與歷史資料不自動變更",
            "DELISTING": "停止該股影子即時價格與買進資格，移入人工審核；歷史資料不得刪除",
            "NAME_CHANGE": "只建立顯示名稱候選；代號、歷史價格、預測與學習紀錄保持不變",
            "SYMBOL_CHANGE": "建立新舊代號候選對照；核對公司識別碼與生效日後才可人工核准",
            "MERGER_OR_SHARE_EXCHANGE": "記錄承接公司、換股比例與生效日候選；禁止自動拼接或刪除歷史資料",
        }
        events.append(_event(
            event_type,
            level="info" if event_type in {"NAME_CHANGE", "TRADING_RESUMED"} else "warning",
            symbol=str(raw.get("symbol") or "").upper(),
            source=raw.get("source"), source_date=raw.get("source_date"),
            headline=raw.get("headline"),
            action=actions.get(event_type, "等待人工核對；正式名單、歷史資料與排名不自動變更"),
        ))

    unique_events = {event["event_id"]: event for event in events}
    events = sorted(
        unique_events.values(),
        key=lambda row: ({"critical": 2, "warning": 1, "info": 0}.get(str(row.get("level")), 0), str(row.get("event_id"))),
        reverse=True,
    )[:MAX_EVENTS]
    registry_source_failures = [
        name for name in ("twse_registry", "tpex_registry", "sec_registry")
        if name in source_health and not (source_health.get(name) or {}).get("ok")
    ]
    event_source_failures = [
        name for name in ("twse_announcements", "tpex_announcements", "nasdaq_halts")
        if name in source_health and not (source_health.get(name) or {}).get("ok")
    ]
    source_failures = registry_source_failures + event_source_failures
    event_level = max(
        ({"info": 0, "warning": 1, "critical": 2}.get(str(row.get("level")), 0) for row in events),
        default=0,
    )
    if event_level >= 2:
        status = "critical"
    elif event_level == 1 or source_failures:
        status = "warning"
    elif is_baseline:
        status = "baseline"
    else:
        status = "ok"

    matched = sum(bool(row.get("present")) for row in current_records.values())
    report = {
        "schema": "wude.corporate_actions_shadow.v1",
        "generated_at": generated_at,
        "status": status,
        "summary": {
            "tracked_stocks": len(tracked),
            "officially_matched": matched,
            "unmatched": len(tracked) - matched,
            "event_count": len(events),
            "warning_count": sum(row.get("level") == "warning" for row in events),
            "critical_count": sum(row.get("level") == "critical" for row in events),
            "source_failure_count": len(source_failures),
            "registry_source_failure_count": len(registry_source_failures),
            "event_source_failure_count": len(event_source_failures),
        },
        "events": events,
        "source_health": source_health,
        "policy": dict(POLICY),
    }
    registry = {
        "schema": "wude.corporate_actions_registry.v1",
        "updated_at": generated_at,
        "records": current_records,
        "missing_observations": missing_observations,
        "policy": dict(POLICY),
    }
    return report, registry


def _fetch_json(session: Any, url: str) -> Any:
    response = session.get(
        url,
        headers=_request_headers(url, accept="application/json"),
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def _fetch_text(session: Any, url: str) -> str:
    response = session.get(
        url,
        headers=_request_headers(url, accept="application/rss+xml, application/xml, text/xml"),
        timeout=30,
    )
    response.raise_for_status()
    return response.text


def _request_headers(url: str, *, accept: str) -> dict[str, str]:
    # SEC asks automated clients to declare an application and contact. Nasdaq
    # rejects some generic library user agents even for its public RSS feed.
    if "sec.gov" in url:
        user_agent = "wude-ai-stock-v6/1.0 314874808+a0953883367@users.noreply.github.com"
    elif "nasdaqtrader.com" in url:
        user_agent = "Mozilla/5.0 (compatible; wude-ai-stock-v6/1.0; +https://github.com/a0953883367/wude-ai-stock-v6)"
    else:
        user_agent = "wude-ai-stock-v6/1.0 (+https://github.com/a0953883367/wude-ai-stock-v6)"
    return {
        "User-Agent": user_agent,
        "From": "314874808+a0953883367@users.noreply.github.com",
        "Accept": accept,
        "Accept-Encoding": "gzip, deflate",
    }


def _fetch_sec_registry(session: Any) -> tuple[Any, str]:
    errors = []
    for key in ("sec_registry", "sec_registry_fallback"):
        try:
            return _fetch_json(session, SOURCE_URLS[key]), SOURCE_URLS[key]
        except Exception as exc:
            errors.append(f"{SOURCE_URLS[key]}: {type(exc).__name__}: {exc}")
    raise SourceError("; ".join(errors))


def fetch_official_sources(
    session: Any | None = None,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, dict[str, Any]],
]:
    session = session or requests.Session()
    registry_sources = []
    registry_specs = (
        ("twse_registry", lambda value: normalize_tw_registry(value, exchange="TWSE")),
        ("tpex_registry", lambda value: normalize_tw_registry(value, exchange="TPEx")),
        ("sec_registry", normalize_sec_registry),
    )
    for name, normalizer in registry_specs:
        try:
            payload, used_url = (
                _fetch_sec_registry(session)
                if name == "sec_registry"
                else (_fetch_json(session, SOURCE_URLS[name]), SOURCE_URLS[name])
            )
            records = normalizer(payload)
            if not records:
                raise SourceError("official registry returned no records")
            registry_sources.append({"source": name, "ok": True, "records": records, "url": used_url})
        except Exception as exc:
            registry_sources.append({"source": name, "ok": False, "records": [], "error": f"{type(exc).__name__}: {exc}"})

    announcements = []
    event_source_health: dict[str, dict[str, Any]] = {}
    for name, exchange in (("twse_announcements", "TWSE"), ("tpex_announcements", "TPEx")):
        try:
            payload = _fetch_json(session, SOURCE_URLS[name])
            if not isinstance(payload, list):
                raise SourceError("official announcement feed is not a list")
            events = normalize_tw_announcements(payload, exchange=exchange)
            announcements.extend(events)
            event_source_health[name] = {
                "ok": True,
                "record_count": len(payload),
                "event_count": len(events),
                "error": "",
            }
        except Exception as exc:
            # A temporary announcement outage is visible but must never
            # fabricate a lifecycle event.
            event_source_health[name] = {
                "ok": False,
                "record_count": 0,
                "event_count": 0,
                "error": f"{type(exc).__name__}: {exc}",
            }
    try:
        halts = parse_nasdaq_halt_directory(_fetch_text(session, SOURCE_URLS["nasdaq_halts"]))
        event_source_health["nasdaq_halts"] = {
            "ok": True,
            "record_count": len(halts),
            "event_count": len(halts),
            "error": "",
        }
    except Exception as exc:
        halts = []
        event_source_health["nasdaq_halts"] = {
            "ok": False,
            "record_count": 0,
            "event_count": 0,
            "error": f"{type(exc).__name__}: {exc}",
        }
    return registry_sources, announcements, halts, event_source_health


def run_shadow(
    *,
    universe_path: Path,
    reports_dir: Path,
    session: Any | None = None,
    generated_at: str = "",
) -> dict[str, Any]:
    active_payload = _read_json(universe_path, {})
    if not isinstance(active_payload, dict) or not active_payload.get("data"):
        raise RuntimeError("cannot read active stock universe")
    registry_path = reports_dir / "corporate_actions_registry.json"
    previous = _read_json(registry_path, {})
    if not isinstance(previous, dict):
        previous = {}
    registry_sources, announcements, halts, event_source_health = fetch_official_sources(session)
    timestamp = generated_at or datetime.now(timezone.utc).isoformat()
    report, registry = build_shadow_report(
        active_payload,
        previous,
        registry_sources,
        announcements,
        halts,
        generated_at=timestamp,
        event_source_health=event_source_health,
    )
    _write_json(reports_dir / "corporate_actions_shadow.json", report)
    _write_json(registry_path, registry)
    history_path = reports_dir / "corporate_actions_shadow_history.json"
    history = _read_json(history_path, [])
    if not isinstance(history, list):
        history = []
    history.append({
        "generated_at": timestamp,
        "status": report["status"],
        "summary": report["summary"],
        "event_ids": [row["event_id"] for row in report["events"]],
        "policy": dict(POLICY),
    })
    _write_json(history_path, history[-MAX_HISTORY:])
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe", type=Path, default=Path("search_data.json"))
    parser.add_argument("--reports-dir", type=Path, default=Path("reports"))
    args = parser.parse_args()
    report = run_shadow(universe_path=args.universe, reports_dir=args.reports_dir)
    summary = report["summary"]
    print(
        "corporate actions shadow: "
        f"status={report['status']} matched={summary['officially_matched']}/"
        f"{summary['tracked_stocks']} events={summary['event_count']}"
    )
    # Partial official-source outages remain visible as yellow.  If every
    # identity registry failed, fail the workflow after persisting diagnostics.
    return 1 if summary["registry_source_failure_count"] == 3 else 0


if __name__ == "__main__":
    raise SystemExit(main())
