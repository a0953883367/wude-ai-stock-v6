# Existing stock judgment, outcome and review linkage

This is an additive read-only presentation of the existing prediction engine.
It does not run an LLM or another forecast, alter weights or V6 ranks, settle from
queries, create credentials, widen access, schedule notifications or place orders.
The existing authorized Agent Control research endpoint remains unchanged.

## Data contract

The identity is `(market, symbol, session_date, horizon_code)`, matching the
storage immutable-answer unique index; model version is also retained on every
answer and review. `INSERT OR IGNORE` and `settle_matured` semantics are unchanged.
A query reads all declared group/horizon parts from `prediction_engine.json` and
uses `predictions`, never TOP10 `rankings`, so unranked symbols remain queryable.
Each part must match the manifest generation, contract, group and horizon. Missing
parts, duplicates, future publication/creation dates and unsafe paths fail closed
for that horizon. Sources carry file hashes and publication timestamps. Limits:
4 MiB per file, 48 MiB total, maximum eight parts per group/horizon. All_analysis
keeps its original separate 16 MiB limit and existing price/news freshness checks.

Frozen model direction is the sign of `expected_return_pct`: positive UP,
negative DOWN, zero FLAT. `target_side` is the event whose probability is modeled,
not this direction. `probability_pct` is an uncalibrated event probability, never
a demonstrated win rate. Neither dimension authorizes a trade. No target price,
one-year extrapolation, or missing trigger/invalidation rule is invented.
Current MA-based report conditions remain a separately expandable section; they
are not attached retrospectively as the frozen forecast's rules or causes.

The normal existing prediction producer now retains `trade_blocked` in frozen
`evidence_json` for newly inserted answers. True in either top-level or evidence
wins; a false field cannot clear a recorded block. Legacy rows are never updated.
Old published false values without evidence provenance are displayed as unknown,
not as verified permission. This repair does not change model scores or policy.

## Outcomes and feedback

During ordinary report generation `latest_predictions` reads at most three most
recent matured rows per market/symbol/horizon for review. Compact published rows
include their own frozen date, version, expected return, evidence contributions,
outcome date, actual return and legacy event-result field. Pruned factor evidence
stays missing. Latest answers also retain their own per-horizon dates rather than
inheriting a different horizon's symbol-level date. No private database or feature
vectors are published; only the existing compact forecast/outcome evidence fields.
Querying does not initialize, restore, open for writing or settle a database.

`direction_correct` retains its historic event-target meaning: for an UP target,
a negative actual return is false even when the frozen expected return was
negative. The view separately computes `expected_direction_correct` from the
signs of frozen expected and realized returns and `return_error_pp = actual -
expected`. It does not rewrite that legacy label or any training/settlement logic.
A review from another symbol, market, asset group or horizon is not attached.
Different historical model versions are explicitly displayed with their own keys.

The same group/horizon challenger state is shown as report-time aggregate trial
information, not as stock-specific improvement or frozen-time evidence. Existing
performance/model_learning error clusters have different prediction identities;
they are separate dated historical diagnostics and never matched to this engine
just by ticker or a similarly named horizon. Post-hoc classification is not
proven causation. Missing reports/outcomes remain explicit gaps.

## Presentation and verification

A compact signed-return chart leads the answer, followed by each horizon's
frozen direction, event probability, trading limitation and available result.
Factor contributions, historical errors, trial status and source fingerprints
expand on demand. These immutable historical judgments remain readable after
current technical conditions expire, explicitly as dated model snapshots.

On main base 93fd47210, IREN's 2026-10-16 NEXT_1D and UP_5D records have expected
returns -0.35% and -1.04%. Their UP event target does not make them bullish.
News and volume contribute upward; trend, sector and positioning contribute
downward. This is the existing short-horizon forecast, not a year-long claim.
The old export lacks recorded guard evidence, so the view marks it unknown.

Regression coverage exercises real storage insertion and settlement, conflicting
block flags, immutable duplicate inserts, legacy event label preservation, signed
direction, actual-minus-expected errors, all-parts/unranked lookup, wrong-generation
or future records, unrelated historical diagnoses, escaped UI rendering, and
query replay with unchanged report/database bytes. The normal producer test
publishes a pending answer plus the correct earlier matured result end-to-end.
Existing authorization, origin, free-data and formal safety suites remain active.

Deployment and data refresh are separate: the new reader can show existing
forecasts immediately, while retained guard evidence and recent outcomes appear
through the next ordinary producer run. Missing historical evidence is not
reconstructed. No manual workflow dispatch is needed or performed. Passing tests
verifies integration and semantics, not prediction accuracy or profitability.

## The two intended capital horizons

The intended 2027 quarterly-income and 5–10-year growth research paths remain
separate. A quarter can reference existing UP_60D/UP_63D outputs, with exact
60/63-session horizons and their own evidence/results; these are not proof of
stable income. NEXT_1D/UP_5D are not converted to quarterly forecasts. UP_126D
is still 126 sessions, not a 5–10-year forecast. No long-term forecast/validation
model is implemented by this change.

Existing financial/revenue/cash-flow/debt inputs, valuation and industry-lifecycle
research, dated filings/news and corporate events can support later investigation
subject to period/source/completeness checks. The quarterly capital purpose,
acceptable losses and evaluation criteria, and the long-term competitive moat,
durability, capital allocation/dilution, valuation scenarios and validation design
require separate planning. Amounts and risk tolerance are not assumed and no
allocation or trading rule is added. The output displays these support limits.
