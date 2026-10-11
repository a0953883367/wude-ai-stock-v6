# Plan eligibility and data-status contract

`public_research_briefing_contract.py` is a pure, standard-library-only projection
of frozen JSON inputs. It imports no application runtime, reads no files or quote
cache, calls no providers/private API, and changes no evaluation or ledger.
It is not enabled as a publication path merely by adding this module.

## Boundaries

The output identifies itself as `contract_kind: plan_eligibility_status_only` and
`quote_cache_replacement: false`. This contract does not replace or invalidate the
separate source-attested official Taiwan close/volume cache. Null values here
mean this status contract does not release numerical market or plan data.
They do not mean that all report quotes are missing.

Contract v1 has **no enabled numerical or actionable release path**. The trusted
code constant `PUBLIC_RELEASE_POLICY` is immutable and denies all markets.
Every market-value/geometry field is null, every action is null, and every
`decision_eligible` is false. US numerical values are additionally hard-blocked.
Positive permission flags, official-source strings, SIP/Yahoo labels, active
plans, or passing data gates in upstream JSON cannot change that policy. A future
release adapter would require separate design, redistribution evidence, and
review; there is no speculative permission API here.

The module preserves the original artifact, formal V6 outputs, model weights,
legacy plan geometry, rankings, evaluations, raw ledger, and official quote cache
by never writing or mutating any input. It does not calculate a new ranking.

## Pure entry points

Public-candidate canonical scope:

```python
briefing = build_canonical_public_research_briefing(
    canonical_manifest, trade_plan_shadow_artifact,
    as_of="2025-01-02T10:00:00Z",
)
text = render_research_status_preview(briefing)
```

The canonical input uses the public watchlist-manifest schema. No task supplement
is invented, read, or included. Output scope is `canonical_watchlist_only`.
Provenance includes the full canonical-object digest, its identity-universe digest,
and full frozen artifact digest; no task/prompt provenance is emitted.

A separate **local-only** union entry point is also supported:

```python
briefing = build_public_research_briefing(
    frozen_union, trade_plan_shadow_artifact,
    canonical_manifest=canonical_manifest,
    task_supplement=frozen_task_supplement,
    as_of="2025-01-02T10:00:00Z",
)
```

The union is reconstructed exactly from both separately supplied inputs, including
canonical-first overlap resolution and fixed origin tags. It requires the
currently supported `noon_only` readback scope and never infers authorization for
other tasks. Private union outputs and task-derived digests must remain local;
they are not public integration candidates. Do not check real supplement lists,
task IDs, prompt hashes, real cohort fixtures, or generated private unions into
this repository. Persisted tests use synthetic identities and provenance only.

These are trusted, frozen input boundaries: self-consistent hashes prove byte
identity/drift detection, not genuine watchlist ownership, task authorization,
source quality, or redistribution rights. The caller must obtain/attest the
manifest inputs through the established process. An arbitrary upstream object
cannot authorize publication. The code independently checks versions, strict
identities, roles, counts, digests, exact union reconstruction, and task-readback
time. It returns fixed `ContractError` codes without echoing input text.

## Identity accounting versus research qualification

There is exactly one output row per validated manifest identity, ordered by
`market:symbol`, not score or original ranking. Canonical and task origins use
fixed enum tags. Reference-only identities stay reference-only.

Counters have separate meanings:

- `identity_rows_accounted` and `identity_accounting_complete`: identity census
  only, even when research rows are missing or duplicated.
- `matched_research_rows`, `missing_research_rows`, and
  `duplicate_research_identities`: mutually exclusive identity outcomes.
- `upstream_data_gate_passed_plans`: only the existing strict Boolean gate results;
  this does not assert source attestation, entry evaluation, or qualification.
- `qualified_plan_inputs`: existing data gates plus valid/coherent provenance,
  version, timestamps/expiry, active/evaluated plan state, global evaluated state,
  and non-reference role. A non-eligible existing conclusion, unresolved risk/conflict,
  or failed/missing existing plan-quality check also blocks this count. It cannot
  make the public action eligible.
- `suppressed_numeric_plans`: existing per-horizon plan records whose numbers were
  suppressed, not a claim that those numbers were valid or qualified.
- `numeric_release_rows` and `eligible_actions`: always zero in contract v1.

Missing or malformed artifacts retain the valid identity census with explicit
blockers. Duplicate research identities block that identity rather than selecting
one arbitrarily. Artifact rows outside the manifest are counted and ignored.
Malformed identities are counted, never echoed. Invalid manifests reject the
whole call because coverage cannot then be claimed.

## Provenance, observation times, and existing gates

Accepted legacy artifact versions are schema 4, `TRADE-PLAN-SHADOW-V4`,
`CENTRAL-DECISION-HUB-V6`, and per-plan `SHADOW-STOCK-CONCLUSION-V2`.
The source batch must match the report batch. A per-plan source snapshot must
match its digest, identity, horizon, session, source batch, source price, and
frozen levels. Hashes bind the input; source-label text itself is never a grant.

The repository's exact legacy `YYYY-MM-DD HH:MM:SS` batch format is explicitly
interpreted in `Asia/Taipei`. Aware ISO timestamps retain their actual offset and
are normalized to UTC. Other naive evaluation times, malformed and overflowing
dates, future observations, or incoherent batch/evaluation ordering fail closed.
The explicit `as_of` argument is only generation/check time and never refreshes
underlying evidence.

No new market freshness threshold is invented. Existing `freshness` and `expiry`
gates are honored. Expiry and valid-through session must be present and coherent;
missing, malformed, or elapsed expiry blocks qualification. Gate codes are the
existing market, source, source_date, ohlcv, calendar, completed, freshness, price,
snapshot, core_data, evidence, levels, and expiry gates. Every gate must appear
exactly once with an actual Boolean value. Missing, duplicate, unknown, or
truthy non-Boolean values fail closed.

Global `not_evaluated` prevents qualification even if a row claims eligibility.
Inactive plans and per-plan `not_evaluated` also remain blocked. No output is an
execution instruction, even if all pre-existing gates are declared passed.

## Sanitizer and renderer

The contract constructs a new allowlisted object; it never copies raw rows,
conclusions, or free-text dictionaries. Names, summaries, source labels, arbitrary
origin/group text, recommendations, forecasts, ranks, scores, probabilities,
upstream reasons and labels are omitted. Diagnostics use fixed reason codes.
Only validated identity strings, dates, fixed enums, census counts, and digests
are retained. Digests provide traceability without echoing potentially
price-bearing upstream text.

The deterministic plaintext renderer labels scope as canonical or local-only
union and uses the heading `計畫資格與資料狀態`. It derives the visible census count
from validated identities and never prints numerical market/plan values, even
from a forged contract-shaped object. Unknown identity/reason/origin values fail
closed. Numerical values cannot leak through upstream names or reason strings.

## Offline tests

```sh
python -m unittest discover -s tests -p test_public_research_briefing_contract.py
# Or the repository's normal pytest environment:
python -m pytest tests/test_public_research_briefing_contract.py -q
```

Fixtures are synthetic and test gate failures, inactive/not-evaluated plans,
missing/duplicate research, source and permission spoofing, tainted text, version
and timestamp corruption, absent expiry, manifest drift/duplicates, reference and
overlap handling, immutable inputs, network/I/O prohibition, canonical/private
scope separation, and renderer injection. No production quotes, real grants,
provider requests, private API calls, or task changes are required.

## Same-batch writer and publisher integration

`public_plan_status_writer.write_public_plan_status(reports_dir, artifact)` builds
only the canonical manifest from the trusted repository checkout and writes
`reports/public_plan_status.json`. The existing trade-plan writer passes its
identical frozen report object after the legacy report/health writes. The
provenance digest can therefore be compared against that exact trade-plan JSON
batch. The adapter does not read private supplements or the quote cache and does
not itself advance the prospective ledger.

The status is first serialized to a temporary file and then atomically replaced.
On generation or write failure, the adapter removes the stale local status and
temporary file on a best-effort basis, then raises the fixed
`public_plan_status_generation_failed` error with upstream exception context
suppressed. This prevents price-bearing upstream errors from being printed as the
adapter error. Cleanup failure does not turn the batch into success.

The fresh-main shadow-report publisher requires the status path in its output
allowlist and stops on a failed generation command before staging. It never
allowlists local union/supplement artifacts. The integration depends on the
canonical-manifest module being present on the same baseline.

Failure does not delete or update previously published remote artifacts: they
remain visibly associated with their older source/evaluation/generated times.
Consumers must check those times and existing expiry/freshness blockers rather
than treating a stale remote file as a successful new batch. Adding a source
pointer alone also cannot force ChatGPT or another consumer to adopt or follow
this contract; consumer adoption requires a separately verified integration.
