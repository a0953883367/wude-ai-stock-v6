from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta, timezone
import json
import math
import pytest

from us_private_shadow_projection import project_symbol, _weekly

NOW = datetime(2026, 10, 10, 8, tzinfo=timezone.utc)


def sessions(n=100, end=date(2026, 10, 9)):
    result = []
    while len(result) < n:
        if end.weekday() < 5:
            result.append(end.isoformat())
        end -= timedelta(days=1)
    return list(reversed(result))


def records(days, trend=0.1, volume=1000.5):
    return {day: (100 + i * trend, 102 + i * trend, 98 + i * trend,
                  100 + i * trend, volume) for i, day in enumerate(days)}


def project(rows=None, days=None, now=NOW):
    days = days or sessions()
    return project_symbol('AAPL', rows if rows is not None else records(days), days, observed=now, adjustment='split')


def test_complete_projection_independent_frozen_and_non_actionable():
    days = sessions()
    original = records(days)
    before = dict(original)
    p = project(original, days)
    assert original == before
    assert p.status == 'projected' and p.full_window_complete
    assert p.contiguous_tail_count == 100 and p.features and p.plan and p.provenance
    assert p.provenance.adjustment == 'split'
    assert p.provenance.volume_basis == 'split-adjusted shares; may be fractional'
    assert p.provenance.historical_point_in_time is False
    assert p.provenance.provider_finality_verified is False
    assert p.plan.execution_eligible is p.plan.prospective_registered is p.affects_formal is p.durable_retention is False
    assert p.plan.stop < p.plan.entry_low < p.plan.entry_high < p.plan.target1 < p.plan.target2
    risk = p.plan.entry_high - p.plan.stop
    assert (p.plan.target1 - p.plan.entry_high) / risk == pytest.approx(2)
    assert (p.plan.target2 - p.plan.entry_high) / risk == pytest.approx(3)
    assert p.plan.source_input_sha256 == p.provenance.input_sha256
    with pytest.raises(FrozenInstanceError):
        p.features.close = 1
    with pytest.raises(FrozenInstanceError):
        p.plan.stop = 1
    with pytest.raises(TypeError):
        json.dumps(p)
    original[days[-1]] = (1, 1, 1, 1, 1)
    assert p.features.close != 1


def test_recent_listing_can_have_valid_60_bar_tail_without_full_window():
    days = sessions()
    p = project(records(days[-60:]), days)
    assert p.status == 'projected' and p.contiguous_tail_count == 60
    assert p.full_window_complete is False
    assert 'requested_window_incomplete' in p.reasons
    assert p.features and p.plan


@pytest.mark.parametrize('where', [-1, -10, -59])
def test_recent_calendar_gap_never_filled(where):
    days = sessions()
    rows = records(days)
    del rows[days[where]]
    p = project(rows, days)
    assert p.status == 'blocked' and p.features is p.plan is None
    assert p.reasons == ('insufficient_contiguous_indicator_history',)


def test_old_gap_uses_only_contiguous_tail_never_bridges_it():
    days = sessions()
    rows = records(days)
    del rows[days[10]]
    p = project(rows, days)
    assert p.contiguous_tail_count == 89 and p.provenance.first_input_session == days[11]
    assert not p.full_window_complete


def test_raw_split_discontinuity_is_blocked_provider_adjusted_basis_succeeds():
    days = sessions()
    raw = {d: (100, 102, 98, 100, 1000) if i < 50 else (50, 51, 49, 50, 2000)
           for i, d in enumerate(days)}
    assert project(raw, days).reasons == ('adjusted_price_discontinuity_requires_review',)
    # Synthetic provider split adjustment transforms BOTH prices and volumes.
    adjusted = {d: (50, 51, 49, 50, 2000) for d in days}
    p = project(adjusted, days)
    assert p.status == 'projected' and p.features.ma60 == 50
    assert p.features.avg_volume_previous20 == 2000
    assert p.features.volume_ratio_previous20 == 1


def test_formula_goldens_and_no_zero_loss_rsi_fallback():
    import pandas as pd
    days = sessions()
    rows = records(days)
    p = project(rows, days)
    close = pd.Series([r[3] for r in rows.values()])
    macd = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    signal = macd.ewm(span=9, adjust=False).mean()
    assert p.features.rsi14_sma == 100
    assert p.features.atr14_true_range_sma == pytest.approx(4)
    assert p.features.ma60 == pytest.approx(close.tail(60).mean())
    assert p.features.macd12_26 == pytest.approx(macd.iloc[-1])
    assert p.features.macd_signal9 == pytest.approx(signal.iloc[-1])
    assert p.features.macd_histogram == pytest.approx((macd - signal).iloc[-1])
    assert p.features.avg_volume_previous20 == 1000.5


def test_corrections_or_later_knowledge_create_new_hash_no_backdating():
    days = sessions()
    rows = records(days)
    first = project(rows, days)
    changed = dict(rows)
    o, h, l, c, v = changed[days[1]]
    changed[days[1]] = (o, h, l, c, v + 1)
    correction = project(changed, days)
    later = project(rows, days, NOW + timedelta(hours=1))
    assert len({first.provenance.input_sha256, correction.provenance.input_sha256, later.provenance.input_sha256}) == 3
    assert first.provenance.observed_at == NOW.isoformat()
    assert later.provenance.observed_at != first.provenance.observed_at
    assert first.plan.plan_sha256 != later.plan.plan_sha256


def test_current_week_is_never_mixed_into_weekly_indicator():
    observed = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)  # Thursday, cutoff Wednesday
    days = sessions(end=date(2026, 10, 7))
    rows = records(days)
    before = _weekly(list(rows.values()), days, observed)
    changed = dict(rows)
    for day in days:
        if day >= '2026-10-05':
            changed[day] = (1000, 1002, 998, 1000, 2000)
    assert _weekly(list(changed.values()), days, observed) == before


def test_unapproved_adjustment_or_future_session_cannot_be_certified():
    with pytest.raises(ValueError):
        project_symbol('AAPL', {}, sessions(), observed=NOW, adjustment='raw')
    with pytest.raises(ValueError):
        project_symbol('AAPL', {}, sessions(end=date(2026, 10, 12)), observed=NOW, adjustment='split')


@pytest.mark.parametrize('bad', [(True, 102, 98, 100, 1), (100, 102, 98, float('nan'), 1), (100, 102, 98, 100, -1)])
def test_invalid_prices_and_volumes_fail_closed(bad):
    days = sessions()
    rows = records(days)
    rows[days[-1]] = bad
    assert project(rows, days).reasons == ('invalid_ohlcv',)


def week_map(days):
    weeks = {}
    for value in days:
        day = date.fromisoformat(value)
        monday = day - timedelta(days=day.weekday())
        weeks.setdefault(monday.isoformat(), []).append(value)
    return weeks


def test_stale_partial_week_does_not_become_complete_on_saturday():
    full_calendar = sessions(110, end=date(2026, 10, 9))
    days = [d for d in full_calendar if d <= '2026-10-07']
    rows = records(days)
    weekly = _weekly(list(rows.values()), days, NOW, week_map(full_calendar))
    prior = [d for d in days if d < '2026-10-05']
    assert weekly == _weekly([rows[d] for d in prior], prior, NOW, week_map(full_calendar))
    assert len(weekly) >= 9


def test_holiday_week_can_be_complete_using_verified_calendar_membership():
    days = sessions(60)
    # Synthetic official calendar: Friday Oct9 holiday. Four actual sessions.
    known = [d for d in days if d != '2026-10-09']
    rows = records(known)
    weekly = _weekly(list(rows.values()), known, NOW, week_map(known))
    assert weekly[-1][4] == pytest.approx(4 * 1000.5)


def test_missing_week_resets_weekly_rolling_history():
    days = sessions(100)
    known = week_map(days)
    omitted = [d for d in days if not '2026-09-21' <= d <= '2026-09-25']
    rows = records(omitted)
    weekly = _weekly(list(rows.values()), omitted, NOW, known)
    assert len(weekly) == 2


def test_stochastic_recovers_after_old_undefined_flat_window():
    days = sessions()
    rows = records(days)
    for day in days[:9]:
        rows[day] = (100, 100, 100, 100, 1000)
    p = project(rows, days)
    assert p.features.daily_k9 is not None and p.features.daily_d9 is not None
    assert p.plan is not None


def test_current_undefined_stochastic_is_not_filled_with_stale_values():
    days = sessions()
    rows = records(days)
    for day in days[-9:]:
        rows[day] = (109, 109, 109, 109, 1000)
    p = project(rows, days)
    assert p.features.daily_k9 is p.features.daily_d9 is None
    assert p.plan is None and 'research_geometry_unavailable' in p.reasons


def test_provider_adjustment_is_not_independent_action_verification():
    p = project()
    assert p.provenance.adjustment == 'split'
    assert p.provenance.corporate_actions_independently_verified is False


def test_calendar_membership_is_part_of_feature_identity():
    days = sessions()
    rows = records(days)
    a = project_symbol('AAPL', rows, days, observed=NOW, adjustment='split', verified_weeks=week_map(days))
    b = project_symbol('AAPL', rows, days, observed=NOW, adjustment='split', verified_weeks={})
    assert a.features.weekly_k9 is not None and b.features.weekly_k9 is None
    assert a.provenance.calendar_membership_sha256 != b.provenance.calendar_membership_sha256
    assert a.provenance.input_sha256 != b.provenance.input_sha256
