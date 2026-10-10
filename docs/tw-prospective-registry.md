# Taiwan prospective frozen-plan registry

`tw_prospective_registry.build_tw_prospective_registry` is a pure, TW-only shadow
ledger builder. It neither fetches data nor writes files, changes formal V6,
connects a broker, or sends orders. Its output is JSON; the existing report
publisher must retain the previous ledger and write the result atomically.

## Runtime contract

```python
payload = build_tw_prospective_registry(
    current_trade_plan_report["plans"],
    previous_registry,  # None only for genuinely first registration
    calendar=official_calendar,
    now=actual_aware_runtime_clock,
    evaluation_context={
        "official_records": {symbol: parsed_official_daily_record},
        "risk_snapshots": {symbol: current_risk_snapshot},
        "risk_snapshots_by_plan_id": {plan_id: plan_specific_risk_snapshot},
    },
)
```

Production uses actual current time (the default is UTC `datetime.now`), never
the source report timestamp. Clock injection exists for deterministic tests,
not historical reconstruction. Do not replace an unreadable/corrupt ledger with
`None`: preserve the file, report the failure, and stop dependent publication.
Existing registry timestamps cannot move backward. Existing records are retained
even when absent from the current plan report.

Read-only attachment/publication must call
`validate_tw_prospective_registry(previous, now=actual_aware_runtime_clock)`
first. It returns a validated deep copy without evaluating, registering,
updating timestamps, or adding audit events. A digest/chain failure raises
`ValueError`; attaching the unchecked original is not a fallback.

An official record is the existing
`tw_daily_shadow_attestation.parse_official_price_rows` result, including raw
record evidence and the source payload digest. Only the already supported TWSE
STOCK_DAY_ALL and TPEx mainboard daily-close datasets are accepted. No Yahoo, US
raw records, alternative feed, new credential, intraday quote, or synthetic bar
is consumed. The registry reparses raw evidence, checks the normalized metadata,
provider URL, symbol/exchange identity, TWD/share unit, interval, OHLCV, record
digest, official calendar, close time, and aware retrieval time. Raw input is
never published. The payload-level digest is an opaque provider-retrieval audit
reference; the full bulk payload is not rehashed from a single record.

## Enrollment

- Only TW horizons with `conclusion.code == "wait"`, passed source/data gates,
  an active entry plan, and `plan_quality.entry_eligible == True` are candidates.
- Identity is checked against the existing conclusion's canonical plan-snapshot
  ID. Levels must match and satisfy `stop < entry_low <= entry_high < target1`.
- Original official-session price and provider observation must be consistent
  with the frozen report. A source whose next official close has already
  completed cannot be registered retroactively.
- Registration time is the runtime clock. The immutable record freezes plan ID,
  symbol, horizon, original batch/session/price, original levels, compact source
  provenance, buy-window policy, and its computed official sessions.
- A new record is `enrolled_pending`. The registration call never evaluates it,
  even if an input close happens to be inside its entry band.
- Repeated registration preserves the original record. A changed ID/level/window
  for the same symbol, horizon, and source batch is rejected as an amendment.
  Identical duplicate candidates in one batch are ambiguous and are rejected.
- This is enrollment for observation, not a buy recommendation.

## Two independent kinds of expiry

`data_freshness_expires_at`, `original_price_freshness_expires_at`, and
`original_news_expires_at` preserve the original assessment's data deadlines.
They are not extended or relabeled as newly observed facts. An expired original
news scan does not prohibit keeping a pending frozen plan; no later trigger is
possible without currently valid risk/news evidence.

`valid_sessions` contains the requested number of future verified TW sessions
whose closes are strictly after registration. `valid_through_session` and
`last_valid_close_at` identify the final eligible close. `evaluation_expires_at`
is the following official close, exclusively. This publication grace permits a
daily result to arrive shortly after the final eligible close, while the
freshest-completed-session rule prevents later historical backfill. A newer
daily close refreshes the observation; it does not regenerate the frozen band.
Official calendar coverage is required throughout. Weekdays are never guessed.

## Current risk context

A risk snapshot has this explicit shape (timestamps include an offset):

```json
{
  "market": "TW",
  "symbol": "2330.TW",
  "source_session_date": "2026-10-12",
  "observed_at": "2026-10-12T06:00:00Z",
  "source_validity": "verified",
  "risk_blocks": [],
  "news": {
    "status": "verified",
    "observed_at": "2026-10-12T05:45:00Z",
    "expires_at": "2026-10-12T23:45:00Z",
    "source_ids": ["existing_verified_news"]
  },
  "corporate_actions": {
    "status": "clear",
    "observed_at": "2026-10-12T06:00:00Z",
    "coverage_from": "2026-10-08",
    "through_session": "2026-10-12",
    "source_ids": ["existing_official_corporate_action_source"]
  }
}
```

The caller must derive these fields from genuinely available current evidence.
It must not turn an absent event, stale report, missing source, or unsupported
corporate-action coverage into `verified` or `clear`. These are explicit adapter
attestations, not market data inferred by this ledger.

The optional `risk_snapshots_by_plan_id` dictionary overrides the symbol-level
risk snapshot when the frozen plan ID is explicitly present. This supports
different original-session coverage windows for plans in the same symbol.
An explicit unsupported or null override never falls back to symbol-level
evidence. The caller can fetch a bulk source once and evaluate each plan's
original coverage window separately.

- Current risk identity/session must match the evaluated official close. The
  observation must be at or after that close and no later than evaluation time.
- Newly blocking risk invalidates the plan; unsupported/unverified risk
  quarantines it.
- Corporate actions require explicit source IDs and coverage from at least the
  original source session through the evaluated session, observed after close.
  `blocked` invalidates the price basis; `unsupported`/missing coverage
  quarantines it. Do not assume an existing capital-change report covers
  dividends or splits if its adapter cannot establish that fact.
  Compact CA audit can preserve a `reason` and up to eight `reasons` strings,
  each bounded to 200 characters; nested/raw values are never retained.
- News requires source IDs, its actual observation and expiry, and at most the
  existing 18-hour freshness interval. `expired` or elapsed expiry produces
  `observed_wait` with `news_refresh_required`. A fresh risk/news observation may
  resume evaluation inside the same frozen buy window. Future observations,
  unsupported sources, and artificially extended expiry quarantine the plan.

## Evaluation, statuses, and audit

Evaluation requires a genuinely later official session than the original, with
`close_at > registered_at` and `close_at <= fetched_at <= now`. The quote must be
the freshest completed official session and inside the frozen allowed sessions.
Current generated plans never supply the comparison band.

Every earlier session in the frozen window must have a timely, verified close
observation already recorded in the ledger. A close is timely only while it is
the freshest completed session at actual evaluation time. Merely supplying a
historical record with an earlier `fetched_at` cannot reconstruct that timely
observation. A missed session records `missed_prospective_session` and its dates;
this continuity gap permanently prevents triggering that frozen plan. Repeating
or backfilling inputs cannot heal it. The plan can later expire normally.

A verified later official close at or below the frozen stop invalidates before
news/risk/CA gates. If risk or adjusted-price basis is uncertain, the conservative
reason is `observed_price_basis_or_stop_breach`; this still makes no execution or
return claim. A subsequent rebound cannot revive this plan. Timely price
observations are independently recorded when news/CA causes an evidence pause,
so a same-session evidence refresh can resume only without an earlier stop
breach or observation gap. An out-of-band observed close can legitimately be
followed by an in-band next close when the whole observation chain is present.

| Status | Meaning |
| --- | --- |
| `enrolled_pending` | Registered; no genuinely later usable close yet |
| `observed_wait` | Later close outside the original band, or news refresh due |
| `triggered_close_only` | Verified later close is inside the original band |
| `invalidated` | Frozen stop breached, new blocking risk, or changed price basis |
| `expired` | Frozen permitted-session observation window has elapsed |
| `quarantined` | Current evidence cannot be verified, or a prospective session was missed |

Triggered, invalidated, and expired records are terminal and never restart.
Ordinary evidence quarantine is a pause, not a terminal outcome; a genuine
current refresh can resume only before expiry and without changing the frozen
record. Quarantine caused by a missed prospective session is irreversible for
that frozen plan, even when current evidence subsequently improves.
Missing inputs never imply that a stop or target was hit.

Each actual evaluation appends a timestamped, hash-chained event, containing
reason, status, the frozen digest, and compact quote/risk provenance. The
events also distinguish `price_observation_valid` from entry eligibility and
preserve `missed_sessions` independently of later inputs. Repeating
identical input at the same runtime timestamp is a no-op. Terminal records do
not acquire pretend later evaluations. The frozen digest, event chain, and
registry digest are checked on load. They detect inconsistent edits/corruption;
they are not authenticated signatures or a substitute for protected storage.

`triggered_close_only` says only that the daily-close condition was met. It
does not establish intraday touch order, executable price, fills, transaction
costs, subsequent return, win rate, predictive accuracy, or formal adoption.
None of those outcomes are reconstructed or claimed by the registry.

## Verification

`tests/test_tw_prospective_registry.py` covers positive next-close/final-close
cases, regenerated current bands differing from originals, same-batch inputs,
official-source and ledger tampering, duplicate records, missing quotes,
unfinished/future/stale/holiday quotes, calendar gaps, news refresh/expiry,
current risk, CA coverage, TWSE/TPEx separation, excluded US data, registration
clock monotonicity, known-stop invalidation despite unavailable risk, permanent
observation-gap quarantine, valid consecutive-session progress, per-plan risk
coverage overrides, and terminal no-resurrection behavior.
