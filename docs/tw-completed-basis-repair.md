# TW completed-price basis: verified scope and remaining dependency

Verified 2026-10-10. This diagnostic does not modify formal V6, strategy scores,
ranks, prices, targets, or existing prospective plans.

## Current inputs

The frozen `all_analysis.json` batch `2026-10-10 09:22:20` contains 192 Taiwan
rows. 63 have quote price exactly equal to their completed close; 129 differ.
This changes as new batches arrive and is not a permanent coverage number.
For 2330.TW, the quote is 2560, official 2026-10-08 close 2550, short score 46.8,
entry 2510–2535, stop 2485 and target 2610. Merely replacing 2560 with 2550
would not recompute those scores and levels or their intraday assumptions.

`strategy.build_features` uses historical bars plus optional intraday data.
`score_candidates` also uses cross-sectional theme/volume and peer context.
An isolated one-stock rerun cannot be described as the same formal model.
A future repair must use separately identified SHADOW copies and a documented
complete eligible cohort, without mutating formal outputs.

## Emerging-market rows are a different source scope

The existing official TPEx endpoint
https://www.tpex.org.tw/openapi/v1/tpex_esb_latest_statistics
returned both 7415 (元澄半導體) and 7815 (新特) for 2026-10-08.
Its latest transaction, average, high, low and volume fields do not include a
daily opening price. It must not be relabeled TPEx mainboard daily OHLCV.

The attestation adapter now performs this one additional bulk identity lookup
only when a .TWO symbol is absent from the normal daily candle feed. A unique,
same-session official emerging record gives `unsupported_emerging_market`,
with source URL, retrieval time and raw/payload hashes. Unknown, duplicate,
stale and unavailable records remain unclassified. Neither result creates an
OHLCV candle, changes prices, or grants entry eligibility. UI reasons explain
the actual venue limitation instead of implying that a quote can repair it.

## Historical source permission remains unverified

Small technical probes of TWSE STOCK_DAY and TPEx tradingStock returned monthly
records. HTTP 200 does not establish authorization for automated bulk use.
TWSE's official terms section 6 restrict automated downloads to approved means
or consent; section 8 treats government-released open datasets separately:
https://www.twse.com.tw/zh/terms/use.html

The government daily dataset documents the current bulk release:
https://data.gov.tw/dataset/11549
A separate public suggestion requests symbol/month historical access; it is
context, not an authoritative statement that every historical route is absent:
https://data.gov.tw/suggests/136936

No automated historical bulk download was started. Existing published current
OpenAPI use and working prospective plans remain intact. The separate local
history-parser prototype is not a production dependency or a source approval.

## Required history contract before recomputation

- Exact security identity and venue; never join predecessor/successor companies.
- At least 60 consecutive verified completed trading sessions, ending on the
  latest eligible official session (roughly 3–4 calendar months).
- Date, open, high, low, close, and genuine volume for each bar; TWD prices and
  shares, with provider rounding/precision documented. TPEx monthly lots are
  rounded; multiplying by 1000 does not establish exact share counts.
- Explicit source-access rights and source URL; raw response and row digests,
  retrieval timestamps, and revision identity. A download today cannot claim
  to have been known at an older plan's creation time.
- Calendar-verified coverage; missing/halted/suspended sessions are quarantined,
  not forward-filled. Latest OHLC must reconcile to the completed daily source.
- Price-basis action coverage across the whole lookback. Raw split, reverse
  split, capital reduction, par-value and distribution transitions must be
  quarantined or handled by an explicitly validated adjustment policy.
  Existing <=60-calendar-day action queries require contiguous bounded windows
  to cover 60 trading sessions, not an increased or bypassed limit.
- Real price-derived features/geometry recomputed together. Missing intraday
  attack/volume cannot be passed off as observed zero/default-one measurements.
  Short-term eligibility must remain unavailable without its required evidence.
- Fresh non-price/news/risk evidence, separate cohort/plan identity, and
  future-only enrollment. No old plan backfill, old performance attribution,
  automatic orders, promotion or predictive-efficacy claim.

An authorized provider export or user-provided licensed history may satisfy
this contract after verification. No account, subscription, permission or
license is assumed from mere availability of a website or installed tool.
