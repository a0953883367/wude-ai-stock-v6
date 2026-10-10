"""Owner-requested, transient US research; never a public/report/ledger writer."""
from __future__ import annotations
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import threading
import time

import requests
import us_daily_shadow_history as history
from us_private_shadow_projection import project_symbol

VERSION = 'US-PRIVATE-RESEARCH-VIEW-V1'
MAX_SECONDS = 30
MAX_PROVIDER_REQUESTS = 2
COOLDOWN_SECONDS = 30


class ResearchUnavailable(Exception):
    """Fixed operational reason only; never include provider text."""
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


class _RequestBudget:
    def __init__(self, client):
        self.client, self.count = client, 0

    def get(self, *args, **kwargs):
        if self.count >= MAX_PROVIDER_REQUESTS:
            raise history.HistoryBlocked('request_time_budget')
        self.count += 1
        return self.client.get(*args, **kwargs)


class PrivateResearchService:
    """One active request globally, no persisted market values or result cache."""
    def __init__(self, reports_path=Path('reports/all_analysis.json'), *,
                 clock=time.monotonic, session_factory=requests.Session):
        self.reports_path = reports_path
        self.clock = clock
        self.session_factory = session_factory
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def _category(self, symbol):
        if not isinstance(symbol, str) or not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,15}', symbol):
            raise ResearchUnavailable('invalid_symbol')
        try:
            payload = json.loads(self.reports_path.read_text(encoding='utf-8'))
            rows = payload.get('data') or []
            categories = set()
            for row in rows:
                if isinstance(row, dict) and row.get('market') == 'US' and row.get('symbol') == symbol:
                    categories.add('etf' if 'ETF' in str(row.get('type') or '').upper() else 'stock')
        except (OSError, ValueError, TypeError, AttributeError):
            raise ResearchUnavailable('universe_unavailable') from None
        if len(categories) != 1:
            raise ResearchUnavailable('symbol_not_in_current_us_universe')
        return categories.pop()

    def research(self, symbol, calendar, *, now=None):
        category = self._category(symbol)
        if not self._lock.acquire(blocking=False):
            raise ResearchUnavailable('research_busy')
        client = None
        try:
            started = self.clock()
            if started < self._next_allowed:
                raise ResearchUnavailable('research_cooldown')
            self._next_allowed = started + COOLDOWN_SECONDS
            observed = now or datetime.now(timezone.utc)
            credentials = history._credentials()
            if not credentials:
                raise ResearchUnavailable('existing_credentials_unavailable')
            weeks = {}
            sessions = history._sessions(calendar, observed, verified_weeks=weeks)
            deadline = time.monotonic() + MAX_SECONDS
            client = self.session_factory()
            bounded = _RequestBudget(client)
            counts = {'request_count': 0}
            probe = history._fetch(['AAPL'], sessions[-4:], bounded, credentials, deadline, counts)
            if set(probe['AAPL']) != set(sessions[-4:]):
                raise ResearchUnavailable('probe_incomplete')
            bars = history._fetch([symbol], sessions, bounded, credentials, deadline, counts)
            if time.monotonic() > deadline:
                raise ResearchUnavailable('request_time_budget')
            projection = project_symbol(symbol, bars[symbol], sessions,
                                        observed=now or datetime.now(timezone.utc), adjustment='split',
                                        verified_weeks=weeks, instrument_category=category)
            if time.monotonic() > deadline:
                raise ResearchUnavailable('request_time_budget')
            result = {
                'version': VERSION, 'status': projection.status, 'symbol': symbol,
                'instrument_category': category, 'decision_eligible': False,
                'affects_formal': False, 'durable_retention': False,
                'prospective_evaluation_started': False,
                'label': 'unvalidated_private_research_not_an_investment_instruction',
                'reasons': list(projection.reasons),
                'requested_session_count': projection.requested_session_count,
                'observed_session_count': projection.observed_session_count,
                'full_window_complete': projection.full_window_complete,
                'contiguous_tail_count': projection.contiguous_tail_count,
                'quality_sessions': list(projection.quality_sessions),
                'request_count': bounded.count,
                'features': asdict(projection.features) if projection.features else None,
                'plan': asdict(projection.plan) if projection.plan else None,
                'provenance': asdict(projection.provenance) if projection.provenance else None,
            }
            return result
        except history.HistoryBlocked as exc:
            raise ResearchUnavailable(exc.reason) from None
        finally:
            try:
                if client is not None:
                    client.close()
            finally:
                self._lock.release()
