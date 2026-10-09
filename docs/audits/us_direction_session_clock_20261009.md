# US direction ledger clock repair (2026-10-09)

Root cause: performance.update_performance selected US only for morning. Official
US close rows could advance in other reports while prediction_history stayed at
2026-10-02. The 60-day monitor uses a different ledger and cannot certify this one.

Change: morning/noon/evening may freeze one US receipt per official source session,
only when all supplied US rows have the same session and market_contract_valid=True,
after its verified regular/early close and strictly before the next official open.
Existing validation quality/evidence gates remain intact. Uses existing Alpaca
calendar cache/credentials, one bounded missing-cache refresh, and preserves a failed
attempt without automatic repeated retries. Missing calendars block, never infer
weekdays or holidays. No new paid data calls or providers.

US future outcomes use official session distance rather than the count of recorded
snapshots: a five-session gap cannot become a one-day result. Prior forecast capture
must itself pass the window check; existing receipts/hashes/outcomes are preserved.
No retroactive forecast creation. Missing source-session dates and block reasons
are written to reports/us_direction_progress.json by the normal report workflow.

Tests cover noon/evening takeover, duplicate immutable receipts, before-close and
at/after-open blocks, weekend next-open dates, unavailable calendars, invalid price
contracts, bounded refresh failure, missing-date reporting and gap horizon accuracy.
Local Python suite: 918 passed. All 23 Node checks and CI compile command passed.

Production limitation: main's existing US official calendar cache was empty during
inspection. Runtime must verify the existing Alpaca calendar permission and create
a new future-only receipt before recovery can be claimed. At 2026-10-09 22:42 Taipei,
10/8 close -> 10/9 session is already open, so that forecast cannot be recreated.
No report sending is triggered by these tests. No formal V6/ranking/weight changes,
no raw historical backfill, no 20/60-day gate change, no orders.
