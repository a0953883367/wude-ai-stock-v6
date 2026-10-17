# Report-backed stock agent, first version

This adds a narrow deterministic executor to the existing central agent runtime.
It is not a general natural-language LLM planner. No model or provider calls,
new credentials, access grants, subscriptions, notifications, orders, ranking
changes, weight changes or shadow graduation are introduced.

## Request and actual execution

The entry point is `https://a0953883367.github.io/wude-ai-stock-v6/agent-control.html`
after frontend and Railway deployment. This PR does not update the separately
deployed Vercel Next app. A browser already paired on these GitHub Pages can reuse
its normal read-only login. The existing `live-flow.html` pairing page is linked
for the original login flow; the form never requests a master secret. Tokens in
Vercel localStorage are not transferred across origins. Actual production
authenticated acceptance has not been performed.

The existing Agent Control page accepts one current report symbol or company
name within a short question. `POST /api/agent/stock-analysis` accepts exactly
`{"question":"2313.TW 偏空與避險"}`. It accepts the existing explicit owner token or a valid already-paired read-only
device token, using the existing private-research token validator, plus the existing
allowed origin and rate limiter. Public/site tokens, public-read mode and trusted
proxy-header fallback are rejected. Device tokens do not become owner grants;
trading and owner-only routes retain their original checks. No CORS exceptions
are added. This limited integration authorizes only the existing report reader;
it must not be reused for future paid calls, private-source access or mutations.
The endpoint must be deployed alongside the page; an older backend returns an
explicit HTTP error, not a completed task. No deployment is part of this PR.

`execute_safe_task(stock_shadow, analyze, ...)` invokes `stock_agent.analyze_stock`.
It reads at most 16 MiB from `reports/all_analysis.json`, selects exactly one
identity, reads the existing official calendar with networking disabled, verifies
data, integrates existing news scan evidence and builds short/longer scenarios.
Report data remains a research snapshot, not formal output or a live quote. The
executor does not refresh provider data. If upstream reports are missing, stale,
inconsistent or unavailable, that remains visible and may prevent a conclusion.

Tasks expose actual work, progress receipts, fixed error states, a report SHA256,
source timestamps, missing inputs and an output verification result. `verified`
means the output contract matches the evidence; it never means a prediction was
validated or the whole requested analysis is complete. Successful stock requests
remain `limited`, not `completed`; longer fundamental analysis and instrument
selection remain insufficient. Unknown/multiple symbols need input.

No query or result is durably stored. After a lost response or process restart,
resubmission safely re-reads the snapshot and rechecks freshness. Identical symbol
and snapshot have the same task key; this is safe replay, not a durable background
job or resume from a saved provider call. Browser cancellation stops waiting, not
an already-running server read. Concurrent/new requests cannot render an older
browser response. No automatic retry, model cadence or provider spending exists.

## Evidence and chart gates

- Source identity, units, complete same-session OHLC, positive finite prices,
  valid candle geometry and the report price's match to the attested close are
  checked. Yahoo daily bars retain their actual label and are only diagnostic
  research; they do not become SIP or formally eligible evidence.
- Calendar verification, completed session, latest completed session, report
  publication after close, no future timestamps and a 36-hour publication limit
  all apply. Naive repository publication times are Asia/Taipei. A fresh report
  cannot make an old candle current. No weekday fallback is added.
- News uses the existing scan and 18-hour observation policy. Missing scan dates,
  stale caches or future dates block short directional interpretation. No matching
  headlines does not establish absence of risk or independently verify an event.
- The chart is explicitly a **single-session OHLC/level snapshot**, not an invented
  price-history series. Source fields supply every price and MA20/MA60 level.
- Short (1–5 trading days) and longer (1–3 months) are observation horizons, not
  target dates. Supporting/opposing technical evidence, conditional bullish and
  bearish triggers, and invalidation are included. Same-source derived signals
  are not counted as independent votes. No calibrated probabilities or target
  prices are produced. Longer technical conditions remain references because
  financial-statement periods and long history are not independently verified.
- The browser removes usable conditions on expiry or tab resume. The earliest
  next completed session, news expiry, report expiry or five-minute display
  lifetime controls validity; a repeated request must verify again.
- Bearish conditions do not select an inverse ETF or authorize a short. Missing
  instrument terms, borrow availability and cost are explicit. Daily reset,
  compounding, tracking, leverage, short squeeze and unbounded short-loss risks
  are labeled. No portfolio-specific sizing or order instructions are produced.

Macro context is explicitly unavailable in this executor: it does not adopt an
undated index or the unlimited-age StockQ fallback as current. The separate audit
found existing bearish reports and reactive VIX/yield/FX scoring, not a verified
scheduled CPI/PPI consensus-surprise alert pipeline. ChatGPT delivery was not
verified. This change does not implement or reschedule macro notifications, and
preserves the existing TW ranking session-mismatch safeguard.

## Truthfulness repairs

Unsupported executors are blocked instead of completed. Capability schema
selection and declared connectors no longer attest runtime execution/connection.
Tool errors and interruptions release their task reservation. Coach usage reads
`api.attempts`, retaining the legacy `request_count` fallback and preserving zero.
Missing business workspace inputs cannot complete a zero-value calculation; WT
input validation no longer claims profit calculation. Failed stock guard/safety
checks also make the aggregate runtime require attention.

## Budget decision still needed

A general natural-language planning loop remains blocked. Enabling it requires
explicit approval of the model, per-request and daily spending caps, and allowed
call frequency. Existing weekly frozen shadow explanations do not authorize a
new planner or additional model cadence.

## Review evidence

Regression coverage is in `test_stock_agent.py`, `test_stock_agent_ui.js`, and the
existing runtime/control/capability tests. It includes actual executor success,
missing executor, tool failure, stale/invalid/future data, unavailable calendars,
ambiguous identities, safe repeated requests, interruption, fabricated-output
rejection, owner/origin boundaries, chart ordering and browser expiration.
Desktop and 390px mobile browser checks used a locally intercepted endpoint with
an actual repository-report executor result; this is not a live deployment test.
The live report example 2313.TW was limited; MSFT was insufficient because its
report price (528.95) differed from its attested close (529.05). These are fixture
observations at review time, not current recommendations.

Local validation on base `8b5c7a921` (including PR361/362): full Python suite
**2,682 passed / 468 subtests passed**, 41 existing pandas/NumPy deprecation
warnings; **34 JavaScript test files passed**. The changed Python runtime and
stock tests were rerun after adding explicit source-check diagnostics (38 passed).
Canonical watchlist validation, Python compile and `git diff --check` passed.
Browser cancellation/overlap regressions also prove that an interrupted or older
response cannot replace the latest displayed result. These local checks do not
assert live API deployment, delivery or real-account entitlement.

## Independent-review corrections

The report-only route now reuses `_private_research_authorized()` for the existing
explicit owner or signed paired research device token. The normal existing
GitHub Pages device login can therefore pass this route without a master secret.
This is not a general role expansion: site/public-read/proxy fallback, expired,
tampered, unknown and revoked signatures are rejected, Vercel gets no new CORS
exception, and trading configuration/execution remain owner-only. Offline signed
fixtures exercise the actual verifier; no live authenticated acceptance is claimed.

Stock safety verification now checks the prediction-engine order and formal
promotion locks plus the model-learning formal-V6 lock. Missing or unsafe values
produce attention and false lock evidence; disabling controlled shadow promotion
alone remains safe. Policies themselves are never modified.

Each included news article's publication date is checked against observation and
scan time and the existing 14-day window. Malformed/future/old articles are
excluded and counted; incomplete article evidence blocks conditional short
interpretation and suppresses the original summary. Genuine empty scans remain
empty, never positive news. Original `publisher` and legacy `source` attribution
are preserved. Existing upstream date-only UTC publication values retain that
precision; an intraday publication time cannot be recovered from a date-only
value. This change does not modify upstream news classification or rankings.
