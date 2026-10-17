import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from stock_agent import analyze_stock
from agent_runtime import execute_safe_task
from agent_control import UsageLedger

NOW = datetime(2026, 10, 17, 7, tzinfo=timezone.utc)


class Calendar:
    def session_status(self, market, session):
        day = datetime.fromisoformat(session)
        return {'available': True, 'status': 'verified_alpaca' if market == 'US' else 'verified_twse_tpex',
                'is_session': day.weekday() < 5, 'close': '16:00'}


@pytest.fixture
def report(tmp_path):
    row = {'symbol': 'MSFT', 'name': 'Microsoft', 'market': 'US', 'price': 100,
           'official_session_date': '2026-10-16', 'official_open_price': 99,
           'official_high_price': 102, 'official_low_price': 98, 'official_close_price': 100,
           'source_daily_ohlcv_complete': True, 'source_daily_ohlcv_session_date': '2026-10-16',
           'us_daily_source': 'Yahoo Finance daily bars', 'us_daily_session_date': '2026-10-16',
           'us_daily_price_available': True, 'us_daily_price_unit': 'USD/shares',
           'ma20': 99, 'ma60': 101, 'news_data_available': True,
           'news_scanned_at': NOW.isoformat(), 'news_cache_stale': False,
           'news_summary': 'No matching news', 'news_articles': [],
           'us_market_vix': 10, 'tw_market_index': 12345,
           'tw_market_context_session_date': '2026-09-17'}
    payload = {'updated_at': NOW.isoformat(), 'data': [row]}
    (tmp_path / 'reports').mkdir()
    def write():
        (tmp_path / 'reports' / 'all_analysis.json').write_text(json.dumps(payload))
    write()
    return tmp_path, payload, row, write


def run(report, **kwargs):
    return analyze_stock('MSFT 偏空與避險', root=report[0], now=NOW, calendar=Calendar(), **kwargs)


def test_real_executor_success_is_limited_and_chart_first_contract(report):
    r = run(report)
    assert r['status'] == 'limited' and r['verified']
    assert r['actual_work_performed']
    assert r['chart']['close'] == 100
    assert r['chart']['kind'] == 'single_session_ohlc'
    short, longer = r['horizons']
    assert short['status'] == 'conditional_research'
    assert short['bullish_evidence'] and short['bearish']['opposing_evidence']
    assert short['bearish']['trigger'] and short['bearish']['invalidation']
    assert longer['status'] == 'insufficient_data'
    assert longer['bearish_evidence'] and longer['bullish']['opposing_evidence']
    assert r['hedging']['status'] == 'insufficient_data'
    assert len(r['hedging']['risks']) == 3
    assert r['limits']['paid_model_calls'] == r['limits']['provider_calls'] == 0
    assert r['limits']['decision_eligible'] is False
    assert '12345' not in json.dumps(r)  # stale index never appears as current
    assert not {'probability', 'target_price', 'target_date'} & r.keys()
    assert r['sources'][0]['source'] == 'Yahoo Finance daily bars'


@pytest.mark.parametrize('mutation', [
    {'official_session_date': '2026-10-15'}, {'price': 101},
    {'us_daily_source': 'SIP realtime'}, {'us_daily_price_available': False},
    {'source_daily_ohlcv_complete': False}, {'official_close_price': float('nan')},
    {'official_high_price': 90}, {'us_daily_price_unit': 'TWD/shares'},
    {'news_scanned_at': '2026-09-17T00:00:00Z'}, {'news_cache_stale': True},
])
def test_stale_invalid_or_missing_source_blocks_direction(report, mutation):
    report[2].update(mutation); report[3]()
    r = run(report)
    assert r['status'] == 'insufficient_data'
    assert all(h['status'] == 'insufficient_data' for h in r['horizons'])
    assert r['status'] != 'completed'


def test_report_publication_cannot_make_old_session_fresh(report):
    for key in ('official_session_date', 'source_daily_ohlcv_session_date', 'us_daily_session_date'):
        report[2][key] = '2026-09-17'
    report[3]()
    r = run(report)
    assert r['sources'][0]['calendar']['fresh'] is False
    assert r['chart']['stale_or_unverified']
    assert all(h['bearish']['trigger'] is None for h in r['horizons'])


@pytest.mark.parametrize('stamp', ['2026-10-16T19:00:00Z', '2026-10-18T00:00:00Z', 'bad'])
def test_preclose_future_or_invalid_publication_fails_closed(report, stamp):
    report[1]['updated_at'] = stamp; report[3]()
    assert run(report)['status'] == 'insufficient_data'


def test_missing_calendar_is_insufficient(report):
    r = analyze_stock('MSFT', root=report[0], now=NOW)
    assert r['status'] == 'insufficient_data'


def test_missing_and_broken_report_are_tool_error(tmp_path):
    assert analyze_stock('MSFT', root=tmp_path)['status'] == 'tool_error'
    (tmp_path / 'reports').mkdir()
    (tmp_path / 'reports' / 'all_analysis.json').write_text('{broken')
    assert analyze_stock('MSFT', root=tmp_path)['actual_work_performed'] is False


def test_ambiguous_unrecognized_and_duplicate_symbols(report):
    for question in ('幫我賺錢', '', 'MSFTX', 'x'*241):
        assert analyze_stock(question, root=report[0], now=NOW, calendar=Calendar())['status'] == 'waiting_input'
    report[1]['data'].append({**report[2], 'symbol': 'NVDA', 'name': 'Nvidia'}); report[3]()
    assert analyze_stock('MSFT NVDA', root=report[0], now=NOW, calendar=Calendar())['status'] == 'waiting_input'
    report[1]['data'].append(copy.deepcopy(report[2])); report[3]()
    assert run(report)['status'] == 'insufficient_data'


def test_repeat_revalidates_after_interrupted_response_without_writes(report):
    before = (report[0] / 'reports' / 'all_analysis.json').read_bytes()
    # Losing a response never leaves a persistent completion/reservation.
    first = run(report)
    second = run(report)
    assert first['task_key'] == second['task_key']
    later = analyze_stock('MSFT', root=report[0], now=NOW + timedelta(days=3), calendar=Calendar())
    assert later['task_key'] == first['task_key']
    assert later['status'] == 'insufficient_data'
    assert before == (report[0] / 'reports' / 'all_analysis.json').read_bytes()
    assert len(list(report[0].rglob('*'))) == 2


def test_runtime_analyze_calls_executor_and_repeat_is_not_fake_duplicate(report, monkeypatch):
    import stock_agent
    import shutil
    shutil.copytree("agent_workspaces", report[0] / "agent_workspaces")
    original = stock_agent.analyze_stock
    monkeypatch.setattr(stock_agent, 'analyze_stock', lambda q, root: original(q, root=root, now=NOW, calendar=Calendar()))
    ledger = UsageLedger()
    for _ in range(2):
        r = execute_safe_task('stock_shadow', 'analyze', '研究', {'question': 'MSFT'}, ledger, root=report[0])
        assert r['status'] == 'limited'
        assert r['actual_work_performed']
        assert r['capability_plan']['executor_loaded']


def test_endpoint_requires_existing_owner_and_origin(monkeypatch):
    import live_api
    responses = []
    handler = SimpleNamespace(path='/api/agent/stock-analysis',
        _owner_authorized=lambda: False, _origin=lambda: 'https://existing.example',
        _send=lambda status, body: responses.append((status, body)),
        rate_limiter=SimpleNamespace(allow=lambda: True),
        _read_json=lambda: {'question': 'MSFT'})
    live_api.LiveRequestHandler.do_POST(handler)
    assert responses[-1][0] == 401
    handler._owner_authorized = lambda: True
    handler._origin = lambda: None
    live_api.LiveRequestHandler.do_POST(handler)
    assert responses[-1][0] == 403
    handler._origin = lambda: 'https://existing.example'
    handler._read_json = lambda: {'question': 'MSFT', 'order': True}
    live_api.LiveRequestHandler.do_POST(handler)
    assert responses[-1][0] == 400
    import agent_runtime
    monkeypatch.setattr(agent_runtime, 'execute_safe_task', lambda *a, **k: {'status': 'limited', 'verified': True})
    handler._read_json = lambda: {'question': 'MSFT'}
    live_api.LiveRequestHandler.do_POST(handler)
    assert responses[-1][0] == 200
    assert responses[-1][1]['data']['status'] == 'limited'


def test_output_verifier_rejects_invented_prices_and_probabilities(report):
    from stock_agent import verify_result
    result = run(report)
    result['chart']['close'] = 999
    assert not verify_result(result, report[2])
    result = run(report)
    result['horizons'][0]['probability'] = 0.9
    assert not verify_result(result, report[2])
