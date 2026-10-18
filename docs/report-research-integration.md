# Full-pool report research integration

## Root cause and scope

`public_plan_status.json` is `plan_eligibility_status_only`. Its immutable default-deny policy means `eligible_actions=0` is an execution/publication status, not a count of research opportunities. `upstream_data_gate_passed_plans` counts upstream gate passes, not buy signals. This fix adds fixed semantic labels and leaves all public action/numeric/US-numeric switches closed. It neither broadens that sanitized contract nor replaces the official quote cache.

The stock Agent already uses `stock_prediction_evidence.load_catalog/build_evidence` to read every manifest shard, signed expected returns, dates/versions, factors and matched outcomes. The earlier `notifier.render_markdown/save_report` ran before `_update_prediction_engine_safely`, so the normal text report did not receive this evidence. The Agent is a deterministic report-backed reader; this does not claim to add a general LLM planner or model analysis call.

## Existing output, complete identity accounting

`report_research.integrate_research` adds `research` to every row in the **existing** `reports/all_analysis.json`, in unchanged order. It never imports the smaller watchlist. `research_integration` records row count, exact identity digest, market counts, available/missing horizon counts, prediction identities outside the analysis pool, manifest/shard digests and batch times. Pending candidates remain unchanged. Duplicate/malformed identities or mismatched analyzed counts fail integration rather than silently select/drop rows. Original scores, ranks and fields are preserved.

Every identity has all ten existing horizon slots, including explicit missing entries. Available forecasts reuse the existing verified reader. Support/opposition is relative to the signed direction: downward factors support a negative forecast even in an `UP_*` event model. Event target is not predicted direction. Compact output keeps freeze/model dates, expected returns, published frozen data-quality score, factors, missing trigger/invalidation fields, outcome/error, and recent matched review examples. Recent example counts are not total validation samples or win probabilities. Group/horizon learning metrics are shared once at the top level and retain their different scope. Unrelated-system diagnostics remain available through the existing Agent; this compact projection does not copy them as matched outcomes.

Current report conditions are separately dated/scoped. They are not inserted into frozen model rules. Same-session/same-horizon direction disagreements are explicit; legacy model votes and outlook keep their original period/date and are not pooled as independent votes. Cross-horizon variation is not automatically a model conflict. No new price targets, timing promises or probabilities are calculated. Public-plan release remains closed regardless of research/data state.

The projection uses already published forecast fields; it does not import private supplement lists, private provider caches, raw trading plans, or release suppressed public-plan geometry. It preserves the existing publication scope rather than creating a new authorization adapter.

## Generation order and failure protection

1. The original report and snapshots are still saved early. Carried research is removed before this fallback render, and a batch-specific pointer to the full pool is included.
2. The existing prediction engine runs once. Its success result and exact manifest batch must match the analysis batch; engine failure cannot reuse a same-looking older success. Chunk verification remains in the existing reader.
3. The full-pool projection is attached and `all_analysis.json` is atomically published through the existing path. Failed integration preserves core analysis with an explicit integration failure, removes carried research, and cannot claim complete coverage.
4. Only `latest.md` is atomically enriched from the same-batch full pool. Render/write/replace failure returns the original saved Markdown. No duplicate JSON archive, report, delivery or notification is created. Existing `latest.json`/period snapshots contain the explicit full-pool pointer; they do not duplicate the large research payload.

Complete-projection status means identity projection completed, **not** that every horizon has usable research. Consumers must inspect horizon status, issues, source dates and missing counts. The 15 MiB projection budget reserves room under the stock Agent's existing 16 MiB input bound; overflow fails explicitly rather than breaking the existing reader or truncating symbols. File/JSON errors never grant trade eligibility.

## Consumer acceptance remains separate

Program integration and existing automatic deployment do **not** edit or verify a ChatGPT native task. Before declaring that native evening report corrected, its owner must independently verify that the task reads same-batch `all_analysis.json.research_integration` and every required identity's `research`, and keeps research/data/execution distinct. No native task prompt, schedule, credentials, paid calls, orders or notifications were changed or triggered here.

For the 2026-10-18 16:31:42 source batch, the read-only local preview accounts for 380/380 identities and 3,800/3,800 forecasts, with zero forecast identities outside the full analysis pool. IREN's 2026-10-16 NEXT_1D −0.35% and UP_5D −1.04% remain historical DOWN forecasts, not newly generated predictions. Preview files are under `/tmp`, not checked-in production reports. Production enrichment awaits the existing scheduled report producer; no manual report dispatch is used.
