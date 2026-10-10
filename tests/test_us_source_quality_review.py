from copy import deepcopy
from urllib.parse import urlparse
import pytest
from us_source_quality_review import CONTEXT, REVIEWS, annotate_reviewed_holds


def status(symbol, reason, day):
    return {**CONTEXT, 'status': 'partial_in_memory', 'indicator_complete_count': 181,
            'decision_eligible': False, 'affects_formal': False,
            'quality_diagnostics': [{'symbol': symbol, 'reason': reason, 'sessions': [day]}]}


@pytest.mark.parametrize('key', REVIEWS)
def test_review_adds_evidence_without_mutating_hold(key):
    original = status(*key)
    before = deepcopy(original)
    annotated = annotate_reviewed_holds(original)
    assert original == before
    review = annotated['quality_diagnostics'][0].pop('reference_reviews')[0]
    assert annotated == original
    assert review['hold_preserved'] is True
    assert review['eligibility_override'] is review['source_bars_verified'] is False
    assert review['flagged_session'] == key[2]
    assert review['reviewed_at'] == '2026-10-10'
    assert review['source_context'] == CONTEXT
    assert review['verified_event_fact'] and review['interpretation']
    assert all(urlparse(url).scheme == 'https' and urlparse(url).hostname in {
        'www.sec.gov', 'www.otcmarkets.com', 'docs.alpaca.markets', 'www.honeywell.com',
        'www.nasdaqtrader.com', 'www.poet-technologies.com', 'investor.synopsys.com'
    } for url in review['sources'])


@pytest.mark.parametrize('field', CONTEXT)
def test_changed_source_context_never_reuses_review(field):
    original = status('HON', 'source_continuity_review', '2026-06-29')
    original[field] = 'different'
    assert annotate_reviewed_holds(original) == original


@pytest.mark.parametrize('key', [('HON', 'source_continuity_review', '2026-06-30'),
                               ('OTHER', 'source_continuity_review', '2026-06-29'),
                               ('HON', 'history_window_insufficient', '2026-06-29'),
                               ('HNHPF', 'history_window_insufficient', '2026-10-12')])
def test_unreviewed_symbol_reason_or_date_retains_generic_hold(key):
    original = status(*key)
    assert annotate_reviewed_holds(original) == original


def test_mixed_old_and_new_dates_only_annotates_exact_reviewed_event():
    original = status('HON', 'source_continuity_review', '2026-06-29')
    original['quality_diagnostics'][0]['sessions'].append('2026-10-09')
    result = annotate_reviewed_holds(original)
    item = result['quality_diagnostics'][0]
    assert item['sessions'] == ['2026-06-29', '2026-10-09']
    assert [r['flagged_session'] for r in item['reference_reviews']] == ['2026-06-29']
    assert result['indicator_complete_count'] == 181


def test_identity_and_feed_limitations_remain_explicit():
    wolf = REVIEWS[('WOLF', 'source_continuity_review', '2025-09-29')]
    assert 'Do not stitch canceled and new equity' in wolf['interpretation']
    otc = REVIEWS[('HNHPF', 'history_window_insufficient', '2026-10-09')]
    assert 'does not mean the issuer ceased trading' in otc['interpretation']
