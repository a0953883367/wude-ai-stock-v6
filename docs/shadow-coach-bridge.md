# Saved coaching connected to deterministic shadow practice

The saved 2026-10-24 OpenAI direction proposal is now executable as the prospective
strength/horizon audit it actually requested. It is not silently converted to a
65-point abstention intervention. The event-gap and reversal proposals have
explicit reviewed blocked definitions with their missing inputs and thresholds.
Infrastructure acceptance is not market execution or predictive effectiveness.

## Reviewed definitions for the existing three proposals

`shadow_coach_reviewed.json` is a byte-identical copy of the actual coaching report
at commit `8366f9ee4d3e07415eee50d52245656a6d7297a6` (21,064 bytes, raw SHA256
`18eed2c8a940ecd6e651bf8c0875267958fdee381150bad34c9dc8cfb8e741c5`). Its canonical
SHA256 is `42601eada3e3d45a9d38b026bebf1bcc10603b2f97cbf9b704803d0eddc2b3ff`.
It is source evidence, not a generated result. Keeping it with the implementation
means a later weekly report cannot erase the accepted source before first ingestion.

`shadow_coach_definitions.py` contains implementation-reviewed definitions under
`saved-coach-20261024-v1`, keyed by **both exact report and proposal hashes**.
Changed prose, cohort, response ID, or source timestamp cannot inherit the mapping.
Original proposal text remains unchanged in every experiment; the separate frozen
contract records the definition, rationale, source commit, hashes, and limitations.
This is a reviewable code definition, not a claim of independent human approval.

| Saved intention | Deterministic definition | Readiness |
| --- | --- | --- |
| Direction errors by confidence and holding period, versus current baseline | `direction_strength_horizon_audit_v1`: preserve frozen full-day direction; group by market/asset cohort, strength bands `[0,50)`, `[50,65)`, `[65,80)`, `[80,100]`, and 1/2/3/5 official trading sessions | Implemented and accepted from the actual saved proposal; awaits new eligible forecasts |
| US premarket events/abnormal volatility reduce direction harm | No execution until event source/types, pre-forecast availability timestamps, abnormal-volatility measure/threshold, exact action, abstention cap, and frozen source signal are specified | `blocked_missing_event_definition_and_frozen_signal` |
| TW simultaneous breakout volume, sector breadth, and attack volume identify reversal | No execution until breakout lookback/volume threshold, sector universe/breadth threshold, attack-volume definition/threshold, simultaneity window, frozen timestamps, and reversal/delay reference are specified | `blocked_missing_reversal_signal_definition` |

The direction parameters are fixed analysis choices, not fitted to old outcomes.
Horizon values match the existing training horizons; strength bands only describe
the existing 0–100 heuristic. The audit measures coverage, positive directional
hit rate, non-hit percentage (`100 - hit rate`, including flat outcomes), average
and worst directional return, and each band's hit-rate difference from the same
cohort/horizon baseline. It never pools horizons into one performance number.
Review readiness requires at least 20 distinct completed sessions and 30 directional
rows **within a stratum**; it confers no promotion or statistical-significance claim.

Probability-calibration error is explicitly unavailable: the existing strength
score is not a calibrated probability and the proposal supplies no probabilistic
target contract. That part of the requested measurement remains a genuine limit.
The related TW gap-entry and 15% rotation experiments are not these hypotheses and
supply neither execution credit nor outcomes to these definitions.

## Future weekly coaching and daily execution

The existing weekly response schema now requests an allowlisted `execution_template`:

- `direction_strength_horizon_audit_v1` for the diagnostic intention above.
- `direction_strength_abstention_65_v1` for an explicitly requested one-session
  fixed abstention comparison: retain baseline at strength >=65, otherwise abstain.
- `unsupported` for intentions that do not match either definition.

Both executable templates require `rule_id=direction_calibration`; the weekly
prompt describes their exact semantics. Other prose-only responses remain blocked.
No number, instruction, source path, or code is interpreted from model prose.
The weekly call schedule, budget, credentials, and cadence are unchanged.

The ordinary `model_learning.update_model_learning` daily producer invokes
`build_reviewed_bridge`, which admits the pinned saved source and the latest weekly
report idempotently. It writes only the separate shadow experiment artifact and
its compact daily audit. The briefing already calls learning and persists `reports`;
no workflow or schedule is added. The daily bridge has no model or network client.

Each experiment freezes provenance, parameters, registration cutoff, excluded
initial snapshot IDs, pre-open decision receipts, and accepted outcome receipts.
Identity hashes the original proposal plus executable contract. Identical inputs
never restart a version; changed inputs get a separate prospective version. Existing
blocked versions remain preserved. Capacity is 312 versions with no silent pruning.
Corrupt state and clock regressions block instead of resetting evidence. The daily
audit exposes exact definition readiness and missing fields.

## Evidence gates and no lookahead

- Initial snapshots are excluded permanently. Newly observed forecasts must be
  captured after registration, with verified snapshot schema/hash, unique IDs and
  symbols, matching cohort, valid direction, and finite strength/prices.
- Future prediction snapshots freeze existing price-source attestations as
  `coach_price_evidence`. No historical snapshots, outcomes, formal directions,
  scores, rankings, weights, or source eligibility are rewritten.
- Both source and target prices require complete daily OHLCV, matching official
  and source dates, correct units, and TWSE/TPEx OpenAPI or Yahoo daily-bar
  attestations. TW Open Data, quote-only fallbacks, missing provenance, and
  inconsistent adjusted/raw closes remain excluded. No missing evidence is repaired
  by silently broadening the source allowlist.
- Use the existing verified official-calendar cache, with network disabled. The
  source must be complete at capture, and **all horizons** must be registered before
  the first next-session open, before any outcome is visible. US uses official
  opening times/DST; TW uses 09:00 Asia/Taipei. Naive producer timestamps mean Taipei;
  weekly UTC timestamps are compared as aware instants. No weekday approximation.
- Each horizon settles only on its exact nth official session, after verified close,
  using a unique, hash-verified, source-attested target snapshot captured on that
  market's target date. Return must match the frozen source and target closes.
- Missing/ambiguous evidence is excluded. Changed decision/calendar or accepted
  outcome receipts are quarantined rather than overwritten. Metrics are recomputed
  from currently valid evidence, not trusted cached historical performance.

Counts distinguish unique registered forecasts, forecast-horizon rows, actual
execution, and completed outcomes. Rows and overlapping horizons are correlated,
not independent events. Directional returns are not net trading P&L. No template
has an automatic promotion, trading, merge, or deployment path.

## Reproducible acceptance evidence

Run `python tools/shadow_coach_bridge_check.py --output /tmp/coach-acceptance.json`.
The check shares the actual daily ingestion function, reads saved sources and
compressed history, checks before/after input hashes, and refuses to write its
output inside production `reports`. It performs no API calls or historical replay
of outcomes. The committed result is
`docs/audits/shadow-coach-integration-20261025.json`.

The saved report and reviewed source are accepted. The existing direction intention
is registered with its executable fixed definition; the two other intentions show
the specific blockers above. All 81 historical snapshots are excluded; all three
have zero market decisions/outcomes and no effectiveness claim. A separate
synthetic-forward regression feeds the **actual saved proposal** through registration,
24 forecast-horizon decisions from six new forecasts, and 24 validated outcomes;
it verifies bin boundaries, same-horizon comparison, abstention coverage, immutable
inputs, exact dates, idempotency, and unavailable probability calibration. Synthetic
prices/calendar data are test-only and are never written to production reports.

Base main inspected: `3cfc8c5330161cab1dbce3737c4a6b3eced338a7`. PR #372's carried
financial-field work remains untouched; no shadow/coaching/learning overlap was
found. The selected checkout and `/workspace/.agents` had no AGENTS.md or skill
files. No old task or its artifacts were accessed. PR #373 stays draft; no merge or
deployment is authorized in this task. Independent PR review, natural producer
acceptance, and future effectiveness remain distinct from offline acceptance.

## Review correction: required calendar prefix

Independent review found that the original fixed `origin + 32 days` lookup
unnecessarily required a 2027 cache for December 2026 forecasts. The bridge now
expands a verified prefix one calendar day at a time, stopping at the nth official
session. It crosses a year boundary only when that target requires it. The existing
32-day bound remains; missing/unverified required coverage blocks immediately,
without guessing sessions or fetching another calendar.

Thirteen regressions cover TW/US December 1 horizons 1/2/3/5 with a 2026-only cache,
actual bridge registration of all four horizons, required year crossing, missing or
unverified 2027 coverage, and an unavailable intermediate prefix. Twelve fail on
the prior implementation; all thirteen pass with the fix. Existing frozen targets
and data gates are unchanged; no lost historical decisions are backfilled. Remaining
independent review findings must be resolved before any merge decision.
