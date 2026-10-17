"""Bounded report-backed stock research. No model/provider calls or state writes.

A request re-reads the existing report and calendar. Safe replay after an
interrupted connection recomputes freshness; it never reuses stale completion.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from market_calendar import OfficialMarketCalendar
from shadow_stock_conclusion import _timing, _news_observation_valid, source_snapshot

VERSION = 'STOCK-AGENT-REPORT-V1'
MAX_REPORT_BYTES = 16 * 1024 * 1024
LIMITS = {
    'planner': 'deterministic_single_symbol_report_reader',
    'general_natural_language_planner': 'blocked_budget_not_approved',
    'decision_needed': '若需通用自然語言規劃，須明確核准模型、每次及每日費用上限與呼叫頻率。',
    'paid_model_calls': 0, 'provider_calls': 0, 'formal_v6_changed': False,
    'decision_eligible': False, 'orders_placed': False,
    'macro_alert_service_implemented': False,
}


def _positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def _stamp(value):
    try:
        stamp = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return stamp if stamp.tzinfo else stamp.replace(tzinfo=ZoneInfo('Asia/Taipei'))
    except (TypeError, ValueError):
        return None


def _resolve(question, rows):
    """Recognize exactly one existing symbol/name, never claim semantic planning."""
    if not isinstance(question, str) or not 1 <= len(question.strip()) <= 240:
        return []
    text = question.strip().upper()
    found = set()
    for row in rows:
        symbol = str(row.get('symbol') or '').upper()
        name = str(row.get('name') or '')
        aliases = [symbol]
        if symbol.endswith(('.TW', '.TWO')):
            aliases.append(symbol.split('.')[0])
        if name and len(name) >= 2 and name.upper() in text:
            found.add(symbol)
        if any(alias and re.search(r'(?<![A-Z0-9.\-])' + re.escape(alias) + r'(?![A-Z0-9.\-])', text) for alias in aliases):
            found.add(symbol)
    return sorted(found)


def verify_result(result, row):
    """Check output invariants against the actual row, not a self-reported flag."""
    chart = result.get('chart')
    if chart and any(chart.get(k) != row.get('official_' + k + '_price') for k in ('open', 'high', 'low', 'close')):
        return False
    if chart and any(level['value'] not in [row.get(k) for k in ('ma20', 'ma60', 'support1', 'resistance1')]
                     for level in chart['levels']):
        return False
    sources = result.get('sources') or []
    daily_ok = any(s['id'] == 'daily' and s['status'] == 'usable_research' for s in sources)
    news_ok = any(s['id'] == 'news' and s['status'] == 'scan_available' for s in sources)
    horizons = result.get('horizons') or []
    if len(horizons) != 2 or {h['id'] for h in horizons} != {'short', 'longer'}:
        return False
    for horizon in horizons:
        if horizon['status'] == 'conditional_research' and not (daily_ok and news_ok):
            return False
        if any(horizon.get(k) is not None for k in ('probability', 'target_price', 'target_date')):
            return False
    return bool(result.get('source_fingerprint') and result['limits'] == LIMITS
                and result.get('actual_work_performed') is True)


def analyze_stock(question, *, root=Path('.'), now=None, calendar=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('timezone-aware clock required')
    result = {
        'version': VERSION, 'status': 'running', 'actual_work_performed': False,
        'generated_at': now.isoformat(), 'limits': dict(LIMITS), 'progress': [],
        'sources': [], 'missing_data': [], 'chart': None, 'horizons': [],
        'verified': False, 'payload_stored': False,
    }
    path = root / 'reports' / 'all_analysis.json'
    try:
        with path.open('rb') as stream:
            raw = stream.read(MAX_REPORT_BYTES + 1)
        if len(raw) > MAX_REPORT_BYTES:
            raise ValueError('report_too_large')
        report = json.loads(raw)
        rows = report['data']
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ValueError('invalid_rows')
    except (OSError, ValueError, KeyError, TypeError):
        result.update(status='tool_error', status_label='報表讀取失敗，可安全重試',
                      summary='未取得有效的既有報表，未完成分析。')
        return result
    result['actual_work_performed'] = True
    result['progress'].append({'step': 'read_existing_report', 'status': 'completed'})
    symbols = _resolve(question, rows)
    if len(symbols) != 1:
        result.update(status='waiting_input', status_label='請指定一個現有報表代號或名稱',
                      summary='此版本只辨識單一股票；未執行通用自然語言規劃。')
        return result
    symbol = symbols[0]
    matches = [row for row in rows if row.get('symbol') == symbol]
    if len(matches) != 1:
        result.update(status='insufficient_data', status_label='重複股票資料，停止分析', summary='無法驗證唯一來源。')
        return result
    row = matches[0]
    digest = hashlib.sha256(raw).hexdigest()
    result.update(symbol=symbol, name=row.get('name'), market=row.get('market'),
                  task_key=hashlib.sha256((VERSION + symbol + digest).encode()).hexdigest(),
                  source_fingerprint=digest)
    result['progress'].append({'step': 'resolve_single_symbol', 'status': 'completed'})
    published = _stamp(report.get('updated_at'))
    snapshot = source_snapshot(row)
    session = row.get('official_session_date')
    market = row.get('market')
    source = snapshot.get('source')
    accepted = {'TW': {'TWSE OpenAPI', 'TPEx OpenAPI', 'TWSE Open Data'},
                'US': {'Yahoo Finance daily bars', 'Alpaca SIP daily bars'}}
    prices = [row.get('official_' + key + '_price') for key in ('open', 'high', 'low', 'close')]
    valid_candle = (all(_positive(v) for v in prices) and prices[1] >= max(prices)
                    and prices[2] <= min(prices))
    provenance_ok = (source in accepted.get(market, set()) and snapshot.get('source_available') is True
                     and snapshot.get('ohlcv_complete') is True
                     and snapshot.get('source_session_date') == session
                     and snapshot.get('unit') == ('TWD/shares' if market == 'TW' else 'USD/shares')
                     and valid_candle and row.get('price') == prices[3])
    calendar = calendar or OfficialMarketCalendar(root / 'reports' / 'official_market_calendar.json',
                                                  auto_refresh=False, allow_network=False)
    timing = _timing(calendar, market, session, 1, now)
    report_fresh = bool(published and timedelta(0) <= now - published <= timedelta(hours=36))
    # Publication before the candle close is not evidence of a completed candle.
    from shadow_stock_conclusion import _close_at
    close_at = _close_at(calendar, market, session) if market in accepted and session else None
    source_observed = bool(published and close_at and close_at <= published <= now)
    usable = provenance_ok and source_observed and report_fresh and all(
        timing.get(key) for key in ('calendar_verified', 'completed', 'fresh'))
    result['sources'].append({'id': 'daily', 'path': 'reports/all_analysis.json',
        'source': source, 'source_session_date': session,
        'published_at': published.isoformat() if published else report.get('updated_at'),
        'retrieved_at': now.isoformat(), 'status': 'usable_research' if usable else 'insufficient_or_stale',
        'unit': snapshot.get('unit'), 'calendar': timing, 'sha256': digest})
    checks = {
        'daily_provenance': provenance_ok,
        'report_price_matches_attested_close': row.get('price') == prices[3],
        'publication_after_source_close': source_observed,
        'report_within_36_hours': report_fresh,
        'official_calendar_verified': timing.get('calendar_verified') is True,
        'source_session_completed': timing.get('completed') is True,
        'latest_completed_session': timing.get('fresh') is True,
    }
    result['sources'][0]['checks'] = checks
    result['sources'][0]['technical_feature_scope'] = 'existing report derived features; historical series not independently re-fetched'
    if not usable:
        result['missing_data'].append('價格驗證未通過：' + '、'.join(key for key, passed in checks.items() if not passed))
    result['progress'].append({'step': 'verify_source_and_freshness', 'status': 'completed' if usable else 'insufficient_data'})
    if provenance_ok:
        result['chart'] = {'kind': 'single_session_ohlc', 'label': '單一交易日價位圖（不是歷史走勢）',
                           'session_date': session, 'unit': snapshot.get('unit'),
                           'stale_or_unverified': not usable,
                           **dict(zip(('open', 'high', 'low', 'close'), prices)),
                           'levels': [{'label': label, 'value': row[key]} for key, label in
                                      [('ma20', '20日均線'), ('ma60', '60日均線'), ('support1', '既有支撐'), ('resistance1', '既有壓力')]
                                      if _positive(row.get(key))]}
    news_ok = (row.get('news_data_available') is True and not row.get('news_cache_stale')
               and _news_observation_valid(row.get('news_scanned_at'), now))
    articles = row.get('news_articles') if isinstance(row.get('news_articles'), list) else []
    result['sources'].append({'id': 'news', 'source': 'existing news_risk scan',
        'as_of': row.get('news_scanned_at'), 'status': 'scan_available' if news_ok else 'missing_or_stale',
        'summary': row.get('news_summary') if news_ok else None,
        'articles': [{k: article.get(k) for k in ('title', 'url', 'published_at', 'source')}
                     for article in articles[:5] if isinstance(article, dict)] if news_ok else [],
        'note': '未發現符合掃描規則的新聞，不等於沒有風險；摘要不是已驗證事件。'})
    if not news_ok:
        result['missing_data'].append('新聞掃描缺少有效18小時內時間戳或已過期')
    # A generic report publication timestamp cannot date a financial statement or index.
    result['missing_data'] += ['中長期財報期間與來源時間未獨立驗證', '總經事件日曆與市場指數時效未獨立驗證',
                               '放空借券成本、可借數量與反向ETF商品條款未取得']
    for key, horizon, label in [('ma20', 'short', '短線觀察：1～5個交易日'),
                                ('ma60', 'longer', '較長觀察：1～3個月')]:
        level = row.get(key)
        ready = usable and _positive(level)
        support, opposition = [], []
        if ready:
            item = {'source_id': 'daily', 'as_of': session,
                    'text': f'收盤 {prices[3]:g} ' + ('高於' if prices[3] > level else '低於或等於') + f' {key} {level:g}；同源技術證據，不累計成獨立票數。'}
            (support if prices[3] > level else opposition).append(item)
        status = 'conditional_research' if ready and news_ok and horizon == 'short' else 'insufficient_data'
        result['horizons'].append({'id': horizon, 'label': label, 'status': status,
            'scope': '僅技術條件，未校準、非買賣指令', 'bullish_evidence': support,
            'bearish_evidence': opposition,
            'bullish': {'trigger': f'後續完成收盤站上並守住 {key} {level:g}，且重新核對新聞及量價' if ready else None,
                        'invalidation': f'後續完成收盤跌回 {key} {level:g} 下方或來源過期' if ready else None,
                        'opposing_evidence': opposition},
            'bearish': {'trigger': f'後續完成收盤跌破並無法收復 {key} {level:g}，且重新核對新聞及量價' if ready else None,
                        'invalidation': f'後續完成收盤收復 {key} {level:g} 或來源過期' if ready else None,
                        'opposing_evidence': support},
            'missing_data': ([] if ready else ['缺少可用價格及均線']) +
                            ([] if news_ok else ['新聞缺漏或過期']) +
                            (['未驗證財報期間、長期價格序列與總經風險，不能形成完整中長期結論'] if horizon == 'longer' else []),
        })
    result['hedging'] = {'status': 'insufficient_data',
        'summary': '偏空條件可用於觀察避險需求，不能直接換成反向ETF或個股放空建議。',
        'risks': ['反向ETF通常每日重設；複利與追蹤誤差使多日報酬不是標的的簡單鏡像。',
                  '槓桿放大損失；必須核對個別商品倍數與公開文件。',
                  '放空涉及借券、利息、追繳、軋空及理論上無上限損失；未取得可借與成本資料。']}
    expiry = _stamp(timing.get('expires_at'))
    expiries = [now + timedelta(minutes=5)]
    if expiry:
        expiries.append(expiry)
    if published:
        expiries.append(published + timedelta(hours=36))
    news_stamp = _stamp(row.get('news_scanned_at'))
    if news_stamp:
        expiries.append(news_stamp + timedelta(hours=18))
    result['expires_at'] = min(expiries).isoformat()
    result['verified'] = verify_result(result, row)
    result['progress'].append({'step': 'verify_output', 'status': 'completed' if result['verified'] else 'failed'})
    if not result['verified']:
        result.update(status='verification_failed', status_label='結果驗證失敗',
                      summary='輸出與來源不一致，未交付結論。', chart=None, horizons=[])
        return result
    result.update(status='limited' if usable and news_ok else 'insufficient_data',
                  status_label='有限研究已產出；仍有資料缺口' if usable and news_ok else '資料不足；不給方向結論',
                  summary='已讀取既有報表並驗證來源；條件是觀察規則，不是漲跌機率、目標價或承諾。')
    return result
