# Deterministic shadow coach bridge

This feature adds executable **opt-in templates**, not general execution of coach
prose. It does not establish predictive effectiveness or activate the three
legacy proposals. No model call is made by the daily runner.

## Contract and execution

The existing weekly OpenAI response schema now requests `execution_template`.
The only executable value is `direction_strength_abstention_65_v1`, and only with
`rule_id=direction_calibration`. It means the existing fixed one-session,
full-day comparison: preserve the baseline direction when strength is at least
65; otherwise abstain. Cohort is one of the existing four cohorts or ALL. Strength
is a heuristic, not a probability. Other hypotheses must select `unsupported`.
The weekly prompt explicitly excludes multi-horizon calibration, event, and
reversal hypotheses from this template. Historical responses without the field
remain readable but cannot execute. No free-text number or code is interpreted.

`model_learning.update_model_learning`, already called by the ordinary briefing,
now invokes the bridge and links its audit. The bridge reads the latest successful
weekly report, prediction history (including compressed storage), and the existing
official calendar cache with network access disabled. The existing briefing
workflow already persists `reports`; schedules and workflow files are unchanged.

The separate `shadow_coach_experiments.json` artifact contains immutable proposal,
canonical SHA256 of the coaching report, response ID/model metadata, registration
time, excluded initial snapshot IDs, frozen rule parameters, and content-derived
experiment identity. Identical proposals are idempotent across repeated reports;
a changed proposal creates a separate prospective version and never inherits old
results. There is a hard 312-version cap, with explicit capacity status and no
pruning. Frozen-state or clock errors block instead of resetting evidence. A
corrupt artifact is preserved and surfaced in `model_learning.json`.

The implementation reuses the existing direction rule and directional metric
calculation. It intentionally does not reuse old decisions, completions, or the
unrelated rotation/gap experiments as coaching-candidate evidence.

## Data and time gates

- All initially present snapshots are excluded, and subsequent snapshots must be
  captured after experiment registration. Snapshot schema/hash, unique IDs and
  symbols, cohort, frozen direction, finite strength and prices are checked.
- New prediction snapshots freeze existing source attestations under
  `coach_price_evidence`. This is metadata only; no existing snapshot is backfilled
  and no formal score, direction, weight, ranking, or source allowlist changes.
- Both source and target prices require complete daily OHLCV, matching source and
  official session dates, the correct price unit, and the existing attested source:
  TWSE/TPEx OpenAPI for TW or Yahoo daily bars for US. TW Open Data, quote-only
  fallbacks, missing provenance, and inconsistent adjusted/raw closes are blocked.
  This deliberately does not expand eligibility to repair prior Taiwan results.
- Calendar sessions must be verified. The source session must be complete when
  captured; the decision must be recorded before the next official session opens.
  US hours use the official open and America/New_York (including DST); TW uses
  09:00 Asia/Taipei. Existing naive producer timestamps mean Asia/Taipei; weekly
  UTC timestamps are compared as aware instants. No weekday guess or new fetch.
- No decision can be registered after **any** outcome is visible. One-day outcomes
  must target the exact next official session, after its verified close. A unique,
  hash-verified target-session snapshot must independently supply the matching
  source-attested closing price and be captured on that market's target date.
  The reported return must match the two frozen prices. Missing or ambiguous
  evidence is excluded, never filled from later history.
- Decisions and accepted outcome receipts persist. Changed decision/calendar or
  outcome receipts are quarantined; an accepted outcome is never overwritten.
  Missing/revised data cannot continue contributing stale cached metrics.

Counts distinguish registration, currently verified execution, and verified
outcomes. Baseline/candidate coverage, abstention, directional returns and session
counts are cohort-specific. Empty performance metrics remain unknown. These are
correlated row observations, not independent events, calibrated probabilities,
net trading profit, or a promotion decision. `effectiveness` remains `unproven`;
there is no automatic promotion path or order interface.

## Initial integration evidence (offline)

Base main: `3cfc8c5330161cab1dbce3737c4a6b3eced338a7`. Open PR search found no
shadow/coaching/learning overlap; PR #372 concerns carried financial fields and
was left untouched. The selected checkout and `/workspace/.agents` contained no
AGENTS.md or skill files. No old task or task artifacts were consulted.

Read-only replay of the latest saved coaching report:

- Generated: `2026-10-24T21:21:25.081242+00:00`.
- Canonical report SHA256:
  `42601eada3e3d45a9d38b026bebf1bcc10603b2f97cbf9b704803d0eddc2b3ff`.
- Source accepted; all 81 existing history snapshots excluded.
- `direction_calibration/ALL`, `event_gap_risk/US_STOCK`, and
  `missed_strength_rotation/TW_STOCK` each remain
  `blocked_missing_execution_template`, with zero decisions and zero outcomes.

The direction proposal requests stratification, so mapping it to a 65-point
abstention intervention would misrepresent its hypothesis. Event-gap and reversal
still lack an executable matching template; the separate existing experiments
retain their previous honest audit. The next ordinary weekly run may select the
supported template only when appropriate. This task does not trigger that run.

Synthetic regressions exercise the complete mocked weekly response → stored
proposal → daily bridge → pre-open decision → paired next-session outcome path,
without API traffic. Live production execution and future effectiveness remain
unverified until ordinary post-merge runs collect eligible new evidence.

## Validation

- Complete Python suite: 2,875 passed, 468 subtests passed; 43 existing pandas /
  NumPy deprecation warnings.
- 57 focused new bridge cases; combined coaching, bridge, direction, and learning
  suite: 84 passed (rerun after the final non-executable contract refinement).
- All 25 Node checks specified by CI, canonical watchlist manifest, CI Python
  compilation list, and the new bridge module compilation passed.
- `git diff --check` passed. Report datasets and workflow files are unchanged.
