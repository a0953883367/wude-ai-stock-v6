# Shadow evidence linkage audit (2026-10-18)

Read-only evidence base: main 0598c022dc733b191a2472aa0047509f28c6e850.
No report data, frozen picks, invalid historical outcomes, ranking weights,
strategy thresholds, schedules or model budgets are changed by this repair.

## Existing rotation A/B

The entire 8,017,821-byte `reports/market_rotation_shadow.json` was decoded;
SHA256: `ed52037fc45205bc1c3e251637f1d1b6feef4f9ac76fd97bd1be50c102a17e3e`.
Report timestamp: 2026-10-18 06:30:12 Taipei. Recomputing `_summary` from all
outcomes and positions exactly matched both stored market summaries.
Outcome dates span 2026-08-28 through 2026-10-16.

| Market | Valid / invalid days | Baseline net TWD | Rotation net TWD | Incremental TWD | Baseline / rotation positions |
| --- | --- | --- | --- | --- | --- |
| TW | 32 / 1 | -467,627.12 | -421,494.86 | +46,132.26 | 319 / 319 |
| US | 28 / 0 | -200,567.53 | -197,689.92 | +2,877.61 | 280 / 280 |

Both are preliminary review, below 60 days. These are sums of daily paired
trials, not compounded account returns. Relative loss reduction does not imply
profitability. TW baseline/rotation win rates are 24.7649%/27.2727%; US
38.9286%/38.2143%. US average loss is slightly smaller while win rate is lower.
TW additional strict5/5 and practical4/5 groups net -12,091.47 and -77,147.79
with only 4 and 43 positions respectively; these are separate comparators.

`model_learning._shadow_execution_audit` previously read only the missed-strength
baseline diagnostic (TW36 / US29 sessions), omitting the executed rotation report.
Its statement that no fixed rotation A/B existed was too broad.
The existing experiment compares formal V6 TOP10 with an 85% original-score +
15% same-market rotation-score ranking. The coach's `missed_strength_rotation`
hypothesis instead asks whether volume/breakout/breadth/flow invalidate a bearish
prediction. No identity mapping establishes that these are the same candidate.

Repair: attach the related experiment with its timestamp, recomputed summaries,
summary-consistency flag, explicit `same_candidate=false`, and no promotion.
Keep the bearish-reversal candidate's `paired_rule_registered=false`.
The normal briefing already updates rotation before model learning, so no cadence
or model-call change is required. Generated model_learning.json will gain this
mapping on its next ordinary producer run; this patch does not hand-edit reports.

## Exact-date gap evidence

The price audit confirms only these frozen picks have target-session bars:

- 2026-10-05: 3265.TWO (1/10), captured 18:55:13.
- 2026-10-12: 3713.TWO, 6841.TWO, 8234.TWO (3/10), captured 19:25:10.

Historical `tw_official_cache.json` records establish the exchange/date split:

| Git commit | Taipei fetch time | TWSE rows/date | TPEx rows/date |
| --- | --- | --- | --- |
| 3325b7e3e | Oct5 18:50:31 | 152 / Oct2 | 38 / Oct5 |
| 0e4202738 | Oct5 18:56:46 | 152 / Oct2 | 38 / Oct5 |
| 290661c1b | Oct5 19:03:34 | 152 / Oct2 | 38 / Oct5 |
| c0b448180 | Oct12 19:19:30 | 152 / Oct8 | 38 / Oct12 |
| 223ff8e68 | Oct12 19:27:30 | 152 / Oct8 | 38 / Oct12 |
| d032ff78a | Oct12 19:36:24 | 152 / Oct8 | 0 |

All missing frozen picks are TWSE-listed: nine on Oct5 and seven on Oct12.
The TWSE snapshot dates were stale, not a ticker suffix or ROC-date conversion
mistake. The evidence cannot establish why the upstream endpoint served stale
bars; no provider outage cause is invented. Scheduled retries already occurred,
and repeated stale data cannot satisfy exact-date checks. TPEx succeeded twice
on Oct12 then disappeared from the final snapshot.

The gap evaluator previously looked only at the latest invocation rows. Its next-
day invalidation therefore listed all ten missing even though the price audit
retained partial same-day bars. The audit stores prices/hash, not complete hash-verified
proofs, so it is not used to reconstruct historical forward results.

Repairs:

1. Accept the already-integrated official `TWSE Open Data` source in the shadow
   price evidence contract, retaining calendar/date/unit/positive-price gates.
   Preserve TWSE identity when persisting its audit bar (previous binary mapping
   would mislabel any non-OpenAPI source as TPEx). No new fetch or subscription.
2. Preserve full validated target-day proofs separately from the immutable frozen
   receipt across existing same-day retries. Accumulate only observations received
   on the execution date, with capture time on that date and not in the future.
   Keep first observed proof; retain partial evidence and missing symbols on
   expiry. Do not import late historical bars, rewrite receipts, or revive invalid
   days. Same-day persistence repairs future collection/diagnostics; it could not
   have made the historical 1/10 or 3/10 samples eligible.

Oct5 and Oct12 remain invalid; current Oct16 signal for Oct19 remains normally
pending. The 2% rule, 9/10 gate, 0.685% cost and 20/60 review gates are unchanged.
The official fallback can still fail or lack bars; missing data remains missing.
