"""Dated official-reference annotations, never an eligibility override.

No provider requests, market values, credentials or account identifiers here.
A match describes only the exact reviewed source observation context.
"""
from copy import deepcopy

REVIEWED_AT = '2026-10-10'
CONTEXT = {
    'source': 'Alpaca SIP historical daily bars', 'feed': 'sip',
    'interval': '1Day', 'adjustment': 'split',
    'corporate_action_basis': 'provider_split_adjusted_as_observed_now',
    'projection_version': 'US-PRIVATE-SIP-PROJECTION-V1',
}
# Facts are from public primary references. Inferences are explicitly separate.
REVIEWS = {
    ('AAOI', 'source_continuity_review', '2026-02-27'): {
        'classification': 'earnings_adjacent_continuity_unverified',
        'verified_event_fact': 'Issuer reported quarterly results on 2026-02-26.',
        'interpretation': 'The flagged session follows earnings; the announcement does not verify the source bars.',
        'sources': ['https://www.sec.gov/Archives/edgar/data/1158114/000168316826001315/aaoi_ex9901.htm'],
    },
    ('HNHPF', 'history_window_insufficient', '2026-10-09'): {
        'classification': 'unsupported_current_sip_path',
        'verified_event_fact': 'OTC Markets identifies HNHPF; Alpaca documents a separate OTC feed and subscription requirement.',
        'interpretation': 'Unavailable through the current SIP-only path. This does not mean the issuer ceased trading or recently listed.',
        'sources': ['https://www.otcmarkets.com/stock/HNHPF/overview',
                    'https://docs.alpaca.markets/us/docs/market-data-faq'],
    },
    ('HON', 'source_continuity_review', '2026-06-29'): {
        'classification': 'compound_corporate_action_basis_review',
        'verified_event_fact': 'Issuer and Nasdaq confirm Aerospace separation and reverse split effective 2026-06-29, with changed HON security identifier.',
        'interpretation': 'Split-only adjustment does not establish continuity across the distributed business; vendor normalization is unverified.',
        'sources': ['https://www.honeywell.com/us/en/news/press-releases/2026/06/honeywell-technologies-launches-independent-pure-play-automation-company-following-honeywell-aerospace-spin-off',
                    'https://www.nasdaqtrader.com/TraderNews.aspx?id=ECA2026-399'],
    },
    ('POET', 'source_continuity_review', '2026-04-27'): {
        'classification': 'business_event_adjacent_continuity_unverified',
        'verified_event_fact': 'Issuer announced cancellation of Celestial AI purchase orders on 2026-04-27.',
        'interpretation': 'A business event coincides with the flag; this does not verify the source bars or establish an adjustment factor.',
        'sources': ['https://www.poet-technologies.com/news/poet-technologies-provides-purchase-order-update'],
    },
    ('SNPS', 'source_continuity_review', '2025-09-10'): {
        'classification': 'earnings_adjacent_continuity_unverified',
        'verified_event_fact': 'Issuer released quarterly results on 2025-09-09 describing Design IP underperformance.',
        'interpretation': 'The next-session flag is earnings-adjacent; reviewed records do not establish a same-day split or verify source bars.',
        'sources': ['https://www.sec.gov/Archives/edgar/data/883241/000119312525199178/d56931d8k.htm',
                    'https://investor.synopsys.com/news/news-details/2025/Synopsys-Posts-Financial-Results-for-Third-Quarter-Fiscal-Year-2025/default.aspx'],
    },
    ('UMAC', 'source_continuity_review', '2026-05-28'): {
        'classification': 'continuity_unverified_no_confirming_primary_event',
        'verified_event_fact': 'The dated issuer SEC filing concerns a management-services agreement amendment.',
        'interpretation': 'That filing does not explain or validate the flagged source continuity; no confirming primary event was established in this review.',
        'sources': ['https://www.sec.gov/Archives/edgar/data/1956955/000168316826004358/umac_8k.htm'],
    },
    ('WOLF', 'source_continuity_review', '2025-09-29'): {
        'classification': 'security_identity_transition',
        'verified_event_fact': 'SEC issuer and NYSE records confirm cancellation of old common shares and issuance of new common shares at Chapter 11 emergence.',
        'interpretation': 'Same ticker does not establish the same security. Do not stitch canceled and new equity or invent a split factor.',
        'sources': ['https://www.sec.gov/Archives/edgar/data/895419/000089541925000132/wolf-20250928.htm',
                    'https://www.sec.gov/Archives/edgar/data/876661/000087666125000713/ruleprovisionnotice.htm'],
    },
}


def annotate_reviewed_holds(sanitized_status):
    """Enrich already-sanitized diagnostics without changing any original field.

    Unknown dates/reasons/contexts remain unexplained holds. Never use this
    metadata to clear gates, alter formulas, or infer verified OHLCV.
    """
    result = deepcopy(sanitized_status)
    if any(result.get(key) != value for key, value in CONTEXT.items()):
        return result
    for item in result.get('quality_diagnostics', []):
        matches = []
        for session in item.get('sessions', []):
            key = (item.get('symbol'), item.get('reason'), session)
            if key not in REVIEWS:
                continue
            matches.append({
                'symbol': key[0], 'quality_reason': key[1], 'flagged_session': session,
                'reviewed_at': REVIEWED_AT, 'source_context': dict(CONTEXT),
                **deepcopy(REVIEWS[key]), 'hold_preserved': True,
                'source_bars_verified': False, 'eligibility_override': False,
            })
        if matches:
            item['reference_reviews'] = matches
    return result
