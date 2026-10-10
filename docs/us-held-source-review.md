# US held-source review — 2026-10-10

Quiet source-validation run [431](https://github.com/a0953883367/wude-ai-stock-v6/actions/runs/38037310216)
confirmed 188 requested instruments (153 stocks, 35 ETFs), 183 full-window
histories, and 181 private indicator projections. Seven holds remain unchanged.
The source is Alpaca SIP daily, split-adjusted as observed now. No private market
values are reproduced here.

The versioned annotations in `us_source_quality_review.py` preserve primary
reference URLs and distinguish confirmed event facts from our interpretation:

- HNHPF, October 9: unavailable in the current SIP path. OTC reference coverage
  is separate; this is not a claim that Hon Hai ceased trading or recently IPOed.
- HON, June 29: confirmed spin-off plus reverse split and identifier change.
  Split-only data does not establish a coherent distributed-business basis.
- WOLF, September 29, 2025: old equity canceled and new equity issued. Matching
  tickers do not authorize stitching different securities.
- AAOI, February 27, and SNPS, September 10, 2025: earnings-adjacent flags.
  Confirmed announcements do not independently verify exact source OHLCV.
- POET, April 27: issuer-confirmed purchase-order cancellation coincides with
  the flag, without establishing source normalization or verified OHLCV.
- UMAC, May 28: reviewed SEC filing concerns management services; it does not
  confirm a cause or validate the continuity observation.

Annotations bind symbol, original generic quality reason, flagged date, source,
feed, interval, adjustment, corporate-action basis and projection version.
Unknown/future events retain their generic hold. Existing diagnostics remain
visible. An annotation is never a whitelist or eligibility override.

## Why old flags cannot simply be ignored

The current gate checks the full contiguous input tail, up to the 276-session
acquisition window. MA uses up to 60 bars, RSI/ATR 15 input bars, volume 21, and
support/resistance 20. Daily KD, weekly KD, and MACD recursively seed earlier in
the same tail. Thus an old boundary still enters current recursive state and
its private input hash. A finite-window warm-up redesign would require explicit
new versioned seed rules and identities; it is a future candidate, not this fix.

## Remaining private output boundary

The private features and illustrative geometry remain memory-only and are not
user-facing yet. A possible future one-symbol research view must reject shared
site tokens and public-read fallback, store no values, send no notifications,
and label geometry unvalidated and non-actionable. Existing owner tokens and
already-paired read-only device tokens have different authority. Device token
payloads do not encode research-specific scopes; their validity alone does not
prove permission to widen access. No new route, grant or credential is added.
Durable prospective observations remain blocked pending a permitted retention
design. No data-source subscription or redistribution license is inferred.
