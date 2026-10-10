# Private US SIP projection: source-coherent research only

## Boundary

V2 extends the existing in-memory daily collector. Default workflow behavior
still requests only the four-session AAPL probe. The existing explicit
`us_daily_history=true` dispatch flag can request the current US non-ETF
universe; this implementation has not executed that real bulk request.

No formal row, score, rank, source gate, order, notification rule, credential,
OIDC trust, or workflow is changed. The existing formal Yahoo-derived features
and plans are not relabeled as SIP data. No source values, derived indicator
values, plans, hashes, or per-symbol projections are returned through the relay.
Only operational counts, version/basis labels, dates and validated reason counts
can enter the existing public status report. No durable storage is added.

## Source and adjustment contract

The sole endpoint remains Alpaca `/v2/stocks/bars`; parameters explicitly select
SIP, 1Day, USD and **split** adjustment. This provider-defined adjustment applies
to both prices and volume. Fractional adjusted share volumes remain fractional.
Dividends are not adjusted; these are not total-return series. `asof` remains a
symbol-mapping date, not an adjustment-knowledge or historical point-in-time date.

Calendar-verified sessions preceding the current New York date are requested.
A full request is bounded by 200 symbols, 400 calendar days, 12 pages per phase,
10,000 bars/page, 3 MiB/response and a shared 75-second processing budget with
bounded socket-timeout overrun. The four-session AAPL probe must pass first.
401/403/429, incomplete pages and malformed responses remain fail-closed.

History is adjusted **as observed now**. The combined observation timestamp is
captured after history retrieval. Each frozen projection records that timestamp,
provider/endpoint/feed/interval/currency/adjustment, price and volume basis,
source session, input count, verified calendar membership digest and canonical
input hash. Weekly completeness uses the full official expected membership, not
the clipped request window; unproven cross-year weeks remain excluded. A correction, changed
adjustment or later observation produces a new identity. None of this claims
the adjusted series existed in the same form on an earlier historical date.

Unresolved large close discontinuities are blocked for review even after
provider adjustment. This heuristic is not a corporate-action certificate and
never invents an adjustment factor. Upstream revisions/finality are not proven.
`corporate_actions_independently_verified` remains false even when the provider
request explicitly applies split adjustment. Smaller missed actions are not
guaranteed to be caught by the discontinuity heuristic.

## Coverage and formulas

Full requested-window coverage is distinct from indicator readiness. Every
feature uses the contiguous calendar-session tail ending at the target session;
a missing recent session cannot be bridged. Recent listings or old gaps can have
at least 60 valid recent bars while remaining incomplete across the full window.

The core seven indicator count covers MA5/10/20/60, simple-average RSI14,
true-range simple-average ATR14 and current adjusted volume / preceding
20-session average. Features also contain support/resistance, daily KD9,
completed-week KD9 and MACD(12,26,9), with first-value EMA initialization.
Daily momentum and completed-week availability have separate counts. Partial
current weeks and an incomplete first input week are excluded from weekly KD.
Undefined zero-range stochastic values remain missing, never invented.

These formula versions are separate from formal V6. For example, formal V6's
existing `atr14` is an average high-low range; this research ATR uses true range.
No legacy field is overwritten to disguise that difference.

## Frozen illustrative plan

Valid private features can produce one immutable, five-session, long-only ATR
**geometry illustration**, not a selected investment or entry signal. The entry
band is close ± 0.25 ATR; stop is 1.5 ATR below its lower edge; targets are two
and three upper-entry risk units above the upper edge. Invalid geometry blocks
this object. Its identity binds source input hash, observation timestamp and
levels. It has no investment score, probability, ranking or portfolio action.

All plan and feature objects are frozen dataclasses, not report dictionaries.
Execution eligibility, formal effects, prospective registration and durable
retention remain false. The existing TW prospective registry is not reused.
Multi-run US observations and evaluation require a separately reviewed private
retention design and the necessary data rights first.

## Licensing boundary and next real test

A working endpoint/rate limit establishes technical access, not a redistribution
license. Alpaca's published terms support personal/non-commercial use and
restrict public/commercial distribution without written consent. Account-
specific subscriber/retention/derived-data permissions remain unverified.
The proposed next real test is one owner-only, in-memory coverage computation
with sanitized operational output; it must not publish derived prices/signals
or add a cache. No subscription, agreement or access setting is changed.

References:
- https://docs.alpaca.markets/us/reference/stockbars
- https://docs.alpaca.markets/us/docs/market-data-faq
- https://files.alpaca.markets/disclosures/library/TermsAndConditions.pdf
- https://files.alpaca.markets/disclosures/library/AcctAppMarginAndCustAgmt.pdf
