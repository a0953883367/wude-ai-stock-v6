# Taiwan prospective runtime

The original V4 card stays plan-only. `tw_prospective_registry.json` is a separate,
public, compact research ledger of actual registrations, original immutable
levels and subsequent official-close observations. It never supplies orders,
execution fills, predictive probabilities, or validated performance.

## Publication and normal refresh

The ordinary briefing writer uses `update_registry=False`: its legacy report
rebase cannot overwrite registrations computed by another run. After the formal
report commit succeeds, stock-briefing invokes the existing fresh-main shadow
publisher. That publisher fetches the latest main, generates in a detached
worktree and normally fast-forward pushes all allowlisted dependent reports,
including the registry. On a proven concurrency conflict it starts over from the
new main and regenerates. No force push or ledger rebase is used.

This hooks the existing normal briefing schedule; no new scheduler, credentials,
OIDC grants, permissions, or broker integration is added. Code-triggered shadow
refresh uses the same publisher. A bare-Git regression verifies that a stale
briefing report commit cannot replay the ledger and erase a concurrent update.

## Observation contract

Registration requires current qualified TW original plans and same-session
verified official TWSE-listed/TPEx-mainboard daily source records. The first
later session is strictly after actual registration. Existing official bulk
feeds are read, with raw responses held only in memory. Current risk must come
from the coherent current hub batch for that same later session. Fetching an old
hub today does not renew its observation timestamp.

News needs its actual scan timestamp and an independent 18-hour validity. The
frozen buy window uses future official sessions independently of old news/card
expiry. Price-basis corporate actions require full bounded official event
coverage. Identity, announcements, halt/merger warnings are checked separately
from the existing corporate-actions report, observed after the evaluated close.
Missing/stale coverage pauses the observation with a specific reason; a zero
legacy event count alone never certifies no dividend, split or reduction.

The current ledger can honestly finish in `enrolled_pending`. The next actual
close cannot be tested today. Local fixtures simulate boundaries, while the first
production evaluation requires a real future close and all current risk inputs.
Original V6 features, ranking, weights and source history are never rewritten.

## Remaining limits

- Close-only tests do not infer intraday touches, fills or execution quality.
- Full out-of-sample/forward costs and drawdown proof remains outstanding.
- US remains outside this ledger. Connected Alpaca sample access does not prove
  deployed historical-feed entitlement or coherent history/indicator readiness.
- A missed intervening session cannot be silently backfilled into a successful
  prospective sequence; price-basis uncertainty is quarantined.

## Official price-action verification (2026-10-10)

The bounded adapter uses four TWSE and five TPEx public historical report
endpoints. A live nine-feed check over 2026-10-01 through 2026-10-08 verified that
2614.TW's October 6 ex-right/dividend event and 4527.TWO's October 7 capital
reduction block plans frozen beforehand. Plans frozen after those effective
sessions do not inherit that earlier price-basis block. All nine responses passed
schema and requested-range checks. These checks demonstrate parser/source
contracts, not investment returns or future signal accuracy.

At the development observation time, a read-only official-price fetch plus the
published V4 batch yielded 16 enrollable horizons across 11 TW stocks, with first
future official session 2026-10-12. That local test was not published as a
historical registration. Production registers anew using its actual execution
time; its future outcomes must be observed and cannot be prefilled from fixtures.
