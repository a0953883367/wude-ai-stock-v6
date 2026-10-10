# Single-stock shadow conclusions

This read-only layer reuses `decision_hub.py` and `trade_plan_shadow.py`. It does
not fit a new model, infer probability from score, add correlated votes, alter
formal V6 scores/ranks/weights, fetch credentials, place orders, or adopt models.

Each stock/horizon has one `conclusion`: `eligible`, `wait`, `avoid`, or
`insufficient`. `eligible` means the existing **shadow** entry plan passed its
current data/geometry/quality gates, not that a production trade is authorized.
The existing buy zone, no-chase limit, stop, staged exits and holding horizon are
retained. Blocked cards label these levels as references. Missing prices stay
missing and are never displayed as zero.

## Gates and clocks

- Hub rows carry an explicit `source_snapshot` from their input rows. Generic
  `official_*` field names alone do not prove source identity.
- TW accepts TWSE/TPEx OpenAPI with a matching official date, availability flag,
  and `TWD/shares`. US requires explicitly attested `Alpaca SIP daily bars`, a matching daily
  date/availability and `USD/shares`. Existing Yahoo daily ingestion is labeled
  honestly for diagnostics only: it is NOT an eligible source for this new
  conclusion. No new Yahoo acquisition, provider substitution, or volume of
  requests is introduced. Intraday SIP or OPRA cannot substitute for daily
  provenance. The official US calendar is independently required.
- Existing legacy Yahoo daily responses now carry a fingerprint/date/source
  annotation into
  `us_daily_source`, `us_daily_session_date`, `us_daily_price_available`, and
  `us_daily_price_unit`. An annotation proves the actual legacy source, not
  permission to use it for entry eligibility. Yahoo, older or unattested reports
  remain `insufficient`. No approved Alpaca daily-bar ingestion currently exists
  in this pipeline, so the US daily-source gap remains open.
  This patch does not change credentials or calendar acquisition. At the
  2026-10-09 16:29 UTC compatibility check, the existing Railway relay had
  restored the official US cache to `verified_alpaca`; the runtime blocker
  was `source_session_not_closed` for October 9. The new conclusion gate
  accepts that verified cache directly, independently of direct credentials.
  No provider flags substitute for calendar proof.
- `source_daily_ohlcv_complete` must identify genuine complete same-session
  OHLCV, not the strategy fallback that fills missing O/H/L with close and
  missing volume with zero. A mixed or promoted candle cannot inherit an old
  provider attestation. Formal V6 fallback behavior is unchanged.
- Only the existing official calendar cache is read (`allow_network=False`).
  TW and US are separated; missing US close times, cross-year coverage, or
  unverified calendars fail closed. There is no weekday fallback.
- The row must match the latest completed official session. The price must be
  positive, finite, and match the claimed closing baseline. Incomplete daily
  candles or an intraday price cannot become a completed-close conclusion.
- Every adopted evidence item must have matching source/provenance, symbol,
  market, and as-of date; full timestamps must also have been observed by the
  evaluation clock (naive repository timestamps mean Asia/Taipei); the horizon's original model evidence must be present.
  Repeated evidence is listed once, and derived model groups never add votes.
- Hub index and all declared chunk names/timestamps must agree. Duplicate stock
  rows fail closed rather than issuing competing conclusions.
- Expiry is absolute and timezone-qualified: the next verified session close
  invalidates the current snapshot, even if its theoretical buy window is
  longer. The nominal last buy-window session is also shown. Fresh official
  prices, added risks, or invalidated price levels require recalculation sooner.
- The browser invalidates expired eligibility at the boundary, on tab resume,
  and on re-render. Legacy reports without conclusions or valid expiry are
  insufficient. Filters and counters follow the selected horizon.

The layer does not require company financial statements for ETFs or apply TW
institutional fields to US stocks. Existing models retain their own applicability
and quality rules; this layer adds no substitute neutral/fabricated facts.

## Validation and rollout boundary

Conclusion validation remains pending. Existing 60-day model readiness does not
validate this new contract. Separate out-of-sample and forward cohorts by market,
asset type and horizon, with costs, slippage and drawdown checks, are prerequisites
for any adoption decision. No percentage here claims calibrated win probability.

Insufficient/avoid conclusions cannot start new shadow validation entries.
Valid waiting plans retain the existing prospective waiting-entry validation
behavior; they are not presented as currently eligible buys. Existing recorded
signals and their settlement history are unchanged.

Run `python -m pytest -q tests/test_shadow_stock_conclusion.py
 tests/test_trade_plan_shadow.py tests/test_trade_plan_validation.py
 tests/test_decision_hub.py` (one command line), then
`node tests/test_trade_plan_shadow_ui.js` (also runs the VM behavior suite).
The standard full CI suite remains required before publishing/merging.

## Input evidence card

Collapsed categories expose the existing latest OHLCV/K-line snapshot, daily
RSI/MA/ATR, isolated daily/weekly KD and MACD, applicable financial/valuation
fields, sector/market context, Taiwan institutional flows and news-risk summary.
This is an inventory of model inputs, not an additional scoring model. All
categories have `additional_weight: 0`; incomplete candles are omitted, missing
values stay missing, and ETF company accounts / US Taiwan flows are inapplicable.

Daily/weekly KD and MACD remain isolated pattern research, not newly adopted V6
factors. Weekly candles may be unfinished; weekly RSI/MA are not wired. Financial
period dates and mixed Yahoo/SEC/official sources must not be presented as fresh
same-day financial evidence. The latest candle snapshot is not a full historical
chart. These limits are visible on the card.

## Private Alpaca sample preservation and paired research

The existing SIP snapshot request can optionally retain its already-returned
`dailyBar` / `prevDailyBar` OHLCV and timestamps through a separate authenticated
relay envelope. It adds no provider request or access grant. The normalized
live rows, formal history, indicators and published analysis rows do not receive
these raw fields. Original observation time is retained, so a bar observed while
forming is never retroactively called closed. Official session close still does
not prove immutable daily-volume finality.

Raw samples are saved only in the run-local `.alpaca_daily_shadow/` directory,
outside the existing `.prediction_engine` Actions cache. Git and Docker exclusions
prevent accidental source/image inclusion; no cache or public report upload is
added. An ephemeral workflow can lose this local archive when it ends. This is
not a durable approved history or validated indicator dataset. Every sample stays
`history_ready=false`, `decision_eligible=false`, `bar_finality_verified=false`.

`point_in_time_events.py` exposes a compact timing audit in the real hub/card.
Only events published and first observed by the cutoff can qualify for research;
date-only publications, unknown timestamps and conflicting current amendments
remain excluded. Older superseded values cannot be revived by rejecting a
conflicted latest amendment. This registry neither collects new external feeds
nor adds scoring weights.

`shadow_evidence_ablation.py` is called by the real trade-plan report builder.
It reads explicitly prospective paired records from private
`.prediction_engine/shadow_evidence_ablation_records.json`, freezes registration
state privately, and publishes aggregate diagnostics only. Existing records are
excluded at first registration; matching source-ledger outcomes must arrive
later. Events, market and liquidity comparisons remain separate. No pair is
reconstructed from historical scores or already-known outcomes. Missing cost
assumptions or paired data produce insufficient results; matched returns remain
descriptive, never proof of accuracy improvement or automatic model promotion.
Portfolio maximum drawdown is unavailable without an actual marked-to-market
portfolio path; entry-relative adverse excursion is labeled separately.

## Runtime interpretation correction

A legacy frozen row can contain OHLC while lacking the newly introduced completeness
attestation. This is `source_attestation_pending`, not proof that the price data is
absent. Unverified reported OHLC remains visible as reference; synthesized/unaudited
volume and derived candle labels cannot establish eligibility. The original quoted
price and completed-session close are displayed separately. A mismatch still blocks
eligibility until the model/plan basis is coherently recalculated; neither price is
silently substituted into the frozen formal result.

News evidence is a scan-time observation, not a candle-session observation. Its precise
timestamp must be no later than evaluation, no older than the existing news module's
18-hour fresh-cache window, and not explicitly marked as stale. Holiday/weekend scans
are permitted after the last completed candle. Future, date-only, stale, identity-
mismatched and missing/unverified risk evidence still fail closed. An accepted neutral
scan is not positive evidence of no risk and adds no weight.

The public summary distinguishes insufficient/pending verification from genuine avoid
results. The four decision codes and strict eligibility gates remain unchanged.

### Official TW snapshot reconciliation

`decision_hub.py --refresh-shadow-inputs-only --attest-tw-official` reads only the
existing two TWSE/TPEx bulk price endpoints, with bounded provider retries. It compares
source, symbol, session, every frozen OHLC value and any already-present volume.
Only an exact match attests a shadow-only copy and supplies the actual official volume.
The original all_analysis, formal rankings, prices and model outputs remain untouched.
Provider failure, missing symbol, mismatched session/candle, invalid values or units
remain noneligible. No Yahoo acquisition or full-history claim is introduced.

The public proof is a whitelist of source/session/retrieval metadata and hashes; raw
provider records stay in memory. News eligibility expires at the earlier of its 18-hour
freshness boundary and the next official-session close.

Live replay on 2026-10-09 used the published 380-row batch plus the actual official
TWSE/TPEx Oct8 records: 190/192 TW candles matched; two were unavailable. Summary became
34 waiting, 37 existing-model avoid, 309 pending verification, zero eligible. Pending
includes genuine US source/unfinished-session gates and differing quote/close bases,
not a blanket declaration that every stock is dangerous or every price is missing.

## V4: plan assessment is not an immediate entry trigger

The existing short/medium generators deliberately put pullback zones below their own
creation price. Comparing that same snapshot price with its newly generated range
therefore cannot measure later entry opportunities. V4 removes this same-snapshot
eligibility test entirely: every horizon is explicitly `plan_only`, with
`entry_evaluation.status = not_evaluated` and `summary.candidate = null`, never a
claim that the market has zero opportunities. Source/risk failures still apply.

The exact original levels, source batch, symbol, horizon and source price are exposed
with a deterministic content ID. This freezes the content within this report for
audit; it is not a claim of prior prospective registration or cross-batch tracking.
Current formal prices, weights, ranks and generated zones are unchanged. Even a
same-snapshot price already inside a range cannot become an immediate candidate.

A future entry evaluator requires a separately registered immutable plan and a
properly sourced later quote: observation strictly after plan creation, no later
than evaluation, correct market/session/source, and within all expiry/risk limits.
No such quote pipeline is introduced here. The pre-existing forward research ledger
is not repurposed as a verified current-entry feed or proof of predictive efficacy.

Report batch time and assessment time are shown separately. Market closure descriptions
refer to the assessment time, not necessarily the time someone opens a cached page.
