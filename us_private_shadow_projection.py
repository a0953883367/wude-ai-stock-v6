"""Pure SIP-only, split-adjusted research projections; never a public artifact.

Frozen dataclasses intentionally are not JSON-ready report rows. No I/O, formal
model input, broker access or durable ledger belongs here. Adjustment knowledge
is only as observed now; `asof` is symbol mapping, not a historical knowledge date.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
import math
import re
from typing import Mapping
from zoneinfo import ZoneInfo

VERSION = 'US-PRIVATE-SIP-PROJECTION-V1'
SOURCE = 'Alpaca SIP historical daily bars'
ENDPOINT = 'https://data.alpaca.markets/v2/stocks/bars'
MIN_BARS = 60
NY = ZoneInfo('America/New_York')
Bar = tuple[float, float, float, float, float]


@dataclass(frozen=True)
class Provenance:
    version: str
    source: str
    endpoint: str
    feed: str
    interval: str
    adjustment: str
    currency: str
    price_basis: str
    volume_basis: str
    session_date: str
    observed_at: str
    symbol_mapping_asof: str
    input_sha256: str
    input_bar_count: int
    first_input_session: str
    historical_point_in_time: bool = False
    provider_finality_verified: bool = False
    corporate_actions_independently_verified: bool = False
    calendar_basis: str = 'verified_alpaca_cached_session_membership'
    calendar_membership_sha256: str = ''


@dataclass(frozen=True)
class Features:
    close: float
    ma5: float
    ma10: float
    ma20: float
    ma60: float
    rsi14_sma: float
    atr14_true_range_sma: float
    avg_volume_previous20: float
    volume_ratio_previous20: float
    support20: float
    resistance20: float
    daily_k9: float | None
    daily_d9: float | None
    weekly_k9: float | None
    weekly_d9: float | None
    macd12_26: float
    macd_signal9: float
    macd_histogram: float


@dataclass(frozen=True)
class ResearchPlan:
    version: str
    scope: str
    horizon_sessions: int
    entry_low: float
    entry_high: float
    stop: float
    target1: float
    target2: float
    minimum_reward_risk_target1: float
    minimum_reward_risk_target2: float
    source_input_sha256: str
    observed_at: str
    source_session_date: str
    plan_sha256: str
    execution_eligible: bool = False
    prospective_registered: bool = False
    investment_recommendation: bool = False
    return_forecast: bool = False


@dataclass(frozen=True)
class Projection:
    symbol: str
    requested_session_count: int
    observed_session_count: int
    full_window_complete: bool
    contiguous_tail_count: int
    status: str
    reasons: tuple[str, ...]
    provenance: Provenance | None = None
    features: Features | None = None
    plan: ResearchPlan | None = None
    affects_formal: bool = False
    private_only: bool = True
    durable_retention: bool = False
    instrument_category: str = 'unknown'
    company_fundamentals_included: bool = False
    quality_sessions: tuple[str, ...] = ()


def _hash(value) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _ema(values: list[float], span: int) -> list[float]:
    output = [values[0]]
    alpha = 2 / (span + 1)
    for value in values[1:]:
        output.append(alpha * value + (1 - alpha) * output[-1])
    return output


def _kd(bars: list[Bar]) -> tuple[float | None, float | None]:
    k = d = None
    for end in range(9, len(bars) + 1):
        window = bars[end - 9:end]
        low, high = min(b[2] for b in window), max(b[1] for b in window)
        if high <= low:
            k = d = None
            continue
        rsv = (window[-1][3] - low) / (high - low) * 100
        k = rsv if k is None else (rsv + 2 * k) / 3
        d = k if d is None else (k + 2 * d) / 3
    return k, d


def _weekly(bars: list[Bar], sessions: list[str], observed: datetime,
            verified_weeks: Mapping[str, list[str] | tuple[str, ...]] | None = None) -> list[Bar]:
    """Require every provider-calendar session in a completed calendar week.

    The full verified calendar, not the request cutoff or capture weekday,
    establishes a week's membership. Unproven weeks never become full bars.
    """
    if not verified_weeks:
        return []
    local_day = observed.astimezone(NY).date()
    groups: dict[str, list[tuple[str, Bar]]] = {}
    for session, bar in zip(sessions, bars):
        day = date.fromisoformat(session)
        monday = (day - timedelta(days=day.weekday())).isoformat()
        groups.setdefault(monday, []).append((session, bar))
    output = []
    previous = None
    for monday, pairs in sorted(groups.items()):
        expected = set(verified_weeks.get(monday) or [])
        monday_date = date.fromisoformat(monday)
        if (not expected or {day for day, _ in pairs} != expected
                or max(expected) >= local_day.isoformat()
                or any((date.fromisoformat(d) - monday_date).days not in range(7) for d in expected)):
            # An incomplete latest week is discarded; a gap before a later
            # complete week resets that later week's rolling history below.
            continue
        if previous is not None and (monday_date - previous).days != 7:
            output = []
        rows = [b for _, b in pairs]
        output.append((rows[0][0], max(r[1] for r in rows), min(r[2] for r in rows), rows[-1][3], sum(r[4] for r in rows)))
        previous = monday_date
    return output


def _features(bars: list[Bar], sessions: list[str], observed: datetime, verified_weeks=None) -> Features | None:
    closes = [b[3] for b in bars]
    changes = [b - a for a, b in zip(closes, closes[1:])][-14:]
    gains = sum(max(v, 0) for v in changes) / 14
    losses = sum(max(-v, 0) for v in changes) / 14
    rsi = 100 - 100 / (1 + gains / losses) if losses else (100 if gains else 50)
    tr = [max(b[1] - b[2], abs(b[1] - previous[3]), abs(b[2] - previous[3]))
          for previous, b in zip(bars, bars[1:])][-14:]
    avg_volume = sum(b[4] for b in bars[-21:-1]) / 20
    if avg_volume <= 0:
        return None
    k, d = _kd(bars)
    wk, wd = _kd(_weekly(bars, sessions, observed, verified_weeks))
    macd = [a - b for a, b in zip(_ema(closes, 12), _ema(closes, 26))]
    signal = _ema(macd, 9)
    return Features(closes[-1], *(sum(closes[-n:]) / n for n in (5, 10, 20, 60)),
                    rsi, sum(tr) / 14, avg_volume, bars[-1][4] / avg_volume,
                    min(b[2] for b in bars[-20:]), max(b[1] for b in bars[-20:]),
                    k, d, wk, wd, macd[-1], signal[-1], macd[-1] - signal[-1])


def project_symbol(symbol: str, records: Mapping[str, Bar], calendar_sessions: list[str], *,
                   observed: datetime, adjustment: str, verified_weeks=None, instrument_category: str = 'unknown') -> Projection:
    """Freeze one independent research projection, not a formal-model overlay."""
    if (not isinstance(symbol, str) or not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,15}', symbol)
            or observed.tzinfo is None or not calendar_sessions or adjustment != 'split'
            or instrument_category not in {'stock', 'etf', 'unknown'}):
        raise ValueError('verified split-adjusted observation context required')
    parsed = [date.fromisoformat(s) for s in calendar_sessions]
    if (parsed != sorted(set(parsed)) or parsed[-1] >= observed.astimezone(NY).date()
            or (parsed[-1] - parsed[0]).days > 400):
        raise ValueError('invalid completed-session calendar window')
    expected = set(calendar_sessions)
    full = False
    def blocked(reason, tail=0, quality_sessions=()):
        return Projection(symbol, len(expected), len(records), full, tail, 'blocked', (reason,),
                          instrument_category=instrument_category, quality_sessions=tuple(quality_sessions))
    if set(records) - expected:
        return blocked('unexpected_bar_session')
    for bar in records.values():
        if (not isinstance(bar, tuple) or len(bar) != 5
                or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in bar)
                or min(bar[:4]) <= 0 or bar[4] < 0
                or bar[2] > min(bar[0], bar[3]) or bar[1] < max(bar[0], bar[3])):
            return blocked('invalid_ohlcv')
    full = set(records) == expected
    # Missing early history is different from an interior gap in the feature
    # window. Only the contiguous tail can enter any calculation.
    tail = []
    for day in reversed(calendar_sessions):
        if day not in records:
            break
        tail.append(day)
    tail.reverse()
    if len(tail) < MIN_BARS:
        missing = [day for day in calendar_sessions if day not in records]
        relevant = ([missing[-1]] if missing else []) + (tail[:1] if tail else [])
        return blocked('insufficient_contiguous_indicator_history', len(tail), relevant)
    bars = [tuple(float(v) for v in records[day]) for day in tail]
    # Even provider-adjusted data can contain unresolved anomalies or genuine
    # large events. Do not guess another adjustment factor.
    discontinuities = [tail[i] for i in range(1, len(bars))
                       if not 0.65 <= bars[i][3] / bars[i - 1][3] <= 1.5]
    if discontinuities:
        return blocked('adjusted_price_discontinuity_requires_review', len(tail), discontinuities[:16])
    stamp = observed.astimezone(timezone.utc).isoformat()
    relevant_weeks = {(date.fromisoformat(day) - timedelta(days=date.fromisoformat(day).weekday())).isoformat()
                      for day in tail}
    calendar_identity = {'source': 'Alpaca Market Calendar', 'status': 'verified_alpaca',
                         'requested_sessions': calendar_sessions,
                         'weekly_membership': {key: sorted(set((verified_weeks or {}).get(key) or []))
                                               for key in sorted(relevant_weeks)}}
    calendar_digest = _hash(calendar_identity)
    lineage = {'version': VERSION, 'symbol': symbol, 'source': SOURCE, 'endpoint': ENDPOINT,
               'feed': 'sip', 'interval': '1Day', 'adjustment': adjustment, 'currency': 'USD',
               'observed_at': stamp, 'symbol_mapping_asof': calendar_sessions[-1],
               'instrument_category': instrument_category,
               'sessions': tail, 'ohlcv': bars, 'calendar_membership_sha256': calendar_digest}
    digest = _hash(lineage)
    proof = Provenance(VERSION, SOURCE, ENDPOINT, 'sip', '1Day', adjustment, 'USD',
                       'split-adjusted-as-observed-now', 'split-adjusted shares; may be fractional',
                       calendar_sessions[-1], stamp, calendar_sessions[-1], digest, len(tail), tail[0], calendar_membership_sha256=calendar_digest)
    features = _features(bars, tail, observed, verified_weeks)
    if features is None:
        return blocked('insufficient_volume_history', len(tail))
    if any(value is not None and not math.isfinite(value) for value in (getattr(features, f.name) for f in fields(features))):
        return blocked('invalid_indicator', len(tail))
    reasons = []
    if not full:
        reasons.append('requested_window_incomplete')
    if features.weekly_k9 is None or features.weekly_d9 is None:
        reasons.append('insufficient_completed_weekly_history')
    plan = None
    atr = features.atr14_true_range_sma
    if atr <= 0 or features.daily_k9 is None or features.daily_d9 is None:
        reasons.append('research_geometry_unavailable')
    else:
        low, high = features.close - 0.25 * atr, features.close + 0.25 * atr
        stop = low - 1.5 * atr
        risk = high - stop
        target1, target2 = high + 2 * risk, high + 3 * risk
        if stop <= 0 or not all(math.isfinite(v) for v in (low, high, stop, target1, target2)):
            reasons.append('research_geometry_unavailable')
        else:
            identity = {'version': 'US-SIP-ATR-GEOMETRY-V1', 'scope': 'illustrative_instrument_long_only_geometry',
                        'horizon_sessions': 5, 'entry_low': low, 'entry_high': high, 'stop': stop,
                        'target1': target1, 'target2': target2, 'source_input_sha256': digest,
                        'observed_at': stamp, 'source_session_date': calendar_sessions[-1]}
            plan = ResearchPlan(identity['version'], identity['scope'], 5, low, high, stop, target1, target2,
                                2.0, 3.0, digest, stamp, calendar_sessions[-1], _hash(identity))
    return Projection(symbol, len(expected), len(records), full, len(tail), 'projected', tuple(reasons),
                      proof, features, plan, instrument_category=instrument_category)
