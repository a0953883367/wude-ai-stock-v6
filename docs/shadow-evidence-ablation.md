# Bounded prospective evidence comparison

`shadow_evidence_ablation.py` is called by the real trade-plan shadow report as
`evidence_ablation`. It is a read-only, prospective paired evaluator, not a new
trained model or price backtester. It does not alter formal scores, weights,
rankings, orders, or promotion. The current production report has no authentic
paired candidate ledger and must therefore show **insufficient**. Tests below
use synthetic methodology fixtures, not measured predictive improvement.

## What is compared

Each preregistered experiment adds exactly one of `events`, `market`, or
`liquidity` to its named frozen baseline rule. Inputs must already contain both
LONG/ABSTAIN decisions on the same stock, signal session, entry session and
holding horizon. This first version excludes shorting and does not invent
candidate decisions from current evidence or already-known returns. Different
rule versions need a new experiment, registered before prospective collection.

Separate summaries are produced for experiment, evidence addition, TW/US,
STOCK/ETF and horizon. Both arms use the identical complete-outcome opportunity
set, with abstentions assigned zero gross/net return and no trade cost. Missing,
changed, duplicated and invalid rows are reported; missing enrolled records
remain in the outcome-coverage denominator. This prevents selecting only the
surviving favorable records. Coverage/abstention must be reviewed alongside
returns; candidate abstention is not automatically superior stock selection.

## Point-in-time and persistence contract

The caller stores the returned `state` privately and passes that state back on
the next run. The public report must omit it. First invocation records the audit
registration timestamp and excludes every existing record ID. New pairs require:

- `id`, `experiment_id`, `added_evidence`, `baseline_rule_id`, `candidate_rule_id`
- `rule_registered_at` no later than audit registration; rule identity cannot
  change within the experiment
- `market`, `asset_type`, `symbol`, positive integer `horizon_sessions`
- `signal_session_date`, `entry_session_date`, `decision_at`, `entry_at`
- `source_snapshot_id`, `evidence_snapshot_id`, `evidence_available_at`
- `baseline` and `candidate`, each LONG or ABSTAIN

Full timestamps require offsets. Evidence must be available by the decision;
the decision is after registration and strictly before entry; the pair must be
first enrolled before entry and without an outcome. A hash freezes all these
fields. No backfilling of historical pairs is accepted. Rule and cost policy
must be frozen in advance; use a new prospective audit after a policy change.
This version supports forward evaluation only, not retroactively reconstructed
chronological train/test sets. Training or calibration is not performed here.

Later `outcome` payloads use existing forward-ledger conventions:
`status: valid`, `source_ledger_id`, `market`, `symbol`, `signal_session_date`,
`evaluated_at`, `entry_session_date`,
`outcome_session_date`, `horizon_sessions`, `close_return_pct`, and
`max_drawdown_pct`. Evaluation must be after entry and no later than the report
clock; dates/horizon must match. The first accepted result is hashed and later
revisions are excluded explicitly. The producer remains responsible for verified
session counting, price provenance, corporate actions and complete forward
prices, as implemented by the existing `PredictionStore.advance_forward_outcomes`.
An ID or valid flag is not independent proof of source authenticity. No adapter
currently converts that ledger into genuine paired candidate decisions.

The private input is `.prediction_engine/shadow_evidence_ablation_records.json`
with `records` and optional `round_trip_cost_pct`; private persisted state is
`shadow_evidence_ablation_state.json` in the same directory. The report writer
persists state atomically. Public output contains aggregate counts and metrics,
not private individual decision or evidence payloads.

## Metrics and limits

- Registered/completed paired samples, completed signal-session count and
  outcome coverage; per-arm trade coverage and abstentions
- Gross and net win rates among trades, mean gross/net return per matched
  opportunity, and paired candidate-minus-baseline net difference
- The round-trip cost is a user/configured assumption, never fabricated. It
  applies once per LONG opportunity to the gross return. Existing net return
  fields are not charged again. Missing cost leaves net metrics null and status
  insufficient; zero must be an explicit assumption. Costs should include the
  intended fees, spread and slippage. Liquidity-dependent fill realism is not
  modeled by this scalar first version.
- Existing forward `max_drawdown_pct` is actually worst low relative to entry,
  so it is exposed as `worst_entry_relative_excursion_pct`. Actual portfolio
  `max_drawdown_pct` remains null without a marked-to-market portfolio path and
  allocation policy. No compounded equity curve is invented across overlapping
  trades. These are opportunity-level diagnostics, not realizable portfolio P&L.

Rows, shared market shocks and overlapping horizons are dependent. Session counts
are not independent observations. No p-value, calibrated win probability,
automatic promotion threshold or accuracy-improvement claim is generated. Even
with completed pairs the status is only `descriptive_only`; selection effects,
full drawdown/cost realism and sufficient forward coverage require human review.

Run `python -m pytest -q tests/test_shadow_evidence_ablation.py` for methodology
and real-report empty-ledger integration tests. Full relevant repository checks
remain required before publication.
