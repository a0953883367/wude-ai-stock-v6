# US same-source daily research: first private stage

This stage tests Railway's existing Alpaca account, not the connected assistant
plugin account. It does not modify formal V6, replace Yahoo model inputs, create
new credentials, change OIDC trust, subscribe to anything, or start prospective
scoring. The relay still accepts only the existing main-branch stock-briefing
schedule/manual workflow principal.

## Runtime boundary

The existing authenticated market-data relay accepts `daily_shadow_status` with
1–200 unique uppercase stock symbols. Its sole upstream URL is Alpaca's stock
bars endpoint. Feed is SIP, timeframe 1Day, USD, ascending, raw adjustment. There
is no caller-supplied URL, credential, feed, date range, page limit, or storage
path. Redirects are disabled. It uses only already-configured credentials.

AAPL's last four verified prior-local-day sessions are fetched first. Failure,
incomplete coverage, 401, 403, or 429 stops the operation; there is no retry,
credential fallback, subscription upgrade, IEX, or Yahoo substitution. The default workflow run ends after this four-session probe. A later explicit
workflow_dispatch with `us_daily_history=true` repeats the probe and then runs
full collection, bounded by 400 calendar days, 200
symbols, 12 pages per phase, 10,000 records per page and a shared 75-second processing budget. A blocked network read can overrun
that budget by at most the bounded socket inactivity timeout; the response is
then rejected. Streaming bodies are capped at 3 MiB and checked byte-by-byte.
The current New York date is excluded. Verified official calendar details are
required; the settlement buffer does not prove immutable provider finality.

## Computation and truthful status

Every requested session must have one valid OHLCV candle per symbol. Missing,
duplicate, malformed, unexpected, and out-of-calendar bars fail closed. At least
60 bars are needed. All seven experimental indicators use only this raw SIP
history: MA5/10/20/60, simple-average RSI14, true-range simple-average ATR14, and
latest volume divided by the preceding 20-session average. These definitions
are versioned separately from formal V6. A conservative close-ratio gate blocks
large raw-price discontinuities for review; it is not corporate-action proof.

`computed_in_memory` reports a completed technical calculation, not investment
readiness or validated performance. `decision_eligible`, `affects_formal`,
`bar_finality_verified`, and `prospective_evaluation_started` remain false.
Recent listings or missing sessions remain incomplete; no false 188/188 claim.

## Data rights and persistence

Raw bars and numeric indicators remain in request-local Railway memory and are
not persisted, logged, returned through the relay, or sent to any report. Public
output is only operational status, coverage counts, calendar dates, HTTP failure
categories and numeric provider rate-limit metadata. Actual subscription limits
are learned from Railway responses, never inferred from another account.

Durable retention and redistribution rights have not been verified. Therefore
no raw `/data` store, Actions cache, artifact, history backfill, or prospective
observation ledger is enabled. A later separately authorized stage must verify
rights before retaining observations; records may only be prospective from
actual capture time. Historical retrieval never proves earlier availability.

Official API references:
- https://docs.alpaca.markets/us/reference/stockbars
- https://docs.alpaca.markets/us/docs/about-market-data-api
