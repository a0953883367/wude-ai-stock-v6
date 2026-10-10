# Private Taiwan daily-feature reconstruction (Stage A)

## Delivered boundary

`tw_private_cohort.py` is a pure, offline, in-process consumer. It reconstructs daily primitives from one verified Fubon raw-price history per supported member of a frozen Taiwan manifest. It is not wired to an HTTP handler, workflow, scorer, ranking, enrollment, or report writer. No acquisition or output persistence occurs. Plans remain null and decision eligibility remains false, even when every input validates.

The live source prerequisite was demonstrated separately in [stock-briefing run 432](https://github.com/a0953883367/wude-ai-stock-v6/actions/runs/38037850742): 2330.TW and 6290.TWO each returned all 81 expected sessions, 2026-06-12 through 2026-10-08. That validates source access and bar shape, not corporate-action coverage, official-price agreement, performance, or decision readiness. Tests here use synthetic, explicitly offline fixtures rather than captured market bars.

The inspected 2026-10-10 15:52:34 report contains 192 Taiwan members: 159 stocks and 33 ETFs; 152 TWSE and 38 TPEx mainboard members are supported, while two emerging-market members remain held. The builder checks the supplied manifest against the complete frozen report rather than hardcoding these counts or treating ETFs as stocks.

## Trusted input contracts

Inputs must come from trusted in-process collectors. This module is not a validator for arbitrary client-supplied attestations and must not be exposed as one.

- `formal`: frozen report with a parseable batch timestamp, unique Taiwan symbols, asset taxonomy, and common official session. A canonical JSON SHA-256 before and after proves that this object was not modified. No formal score, rank, old price, indicator, or target enters reconstructed features. Formal strategy files are untouched.
- `manifest`: exact Taiwan membership, canonical symbol, market, venue, and original stock/ETF type. Emerging venues are held by venue predicate. Explicit frozen-row venues must agree. Where the report lacks a venue field, the trusted official-universe collector must establish the manifest venue; ticker suffix alone cannot distinguish TPEx mainboard from emerging listings.
- `calendar_evidence`: exact joint TWSE/TPEx verified session list, 60–120 sessions within a sub-120-calendar-day span. Latest session must have passed 16:30 Taipei. It must match the frozen report. Calendar freshness must be established by the source collector; this module does not fetch a current calendar.
- `histories`: private `collect_private` output keyed by canonical symbol. Required evidence includes source identity, venue, raw/unadjusted request, shares volume, request and post-response observation timestamps, exact requested range/session list, valid finite OHLCV geometry, integer volume, and canonical bar digest. Missing histories hold only their rows but never permit subset scoring.
- `official_records`: existing official raw snapshot envelopes, with permitted official endpoint, matching session, raw-record hash and observation timestamp. The existing official parser revalidates OHLCV. An exact mismatch is reported; it never overwrites a Fubon price.
- `nonprice`: explicit source batch, symbol/market, observation time, per-group source, as-of and observation times, and whitelisted measured numeric facts. News additionally needs expiry. Missing, malformed or unknown facts stay unavailable. Group presence does not imply full strategy-feature completeness.

Daily primitives include moving averages, measured daily volumes, support/resistance, close-to-close returns, RSI, and the existing strategy's mean daily range convention for ATR14. Vendor `change` is not used: Fubon documents that it can reference an adjusted prior close even for raw bars. Intraday participation, opening attack, volume pace and attack volume remain unavailable. No neutral zero/50 fills are passed to a scorer. The flat-series RSI value of 50 is the explicit mathematical flat-price case, not a missing-data fallback.

## Corporate-action policy

Stage A specifies and tests a typed collector contract; it does not establish live action coverage. The future trusted collector must prove both `corporate_actions.dividends` and `corporate_actions.capital_changes` coverage for the entire consumed history, including warmup and range boundaries. Each family needs matching dates, an observation timestamp, and a response digest; each event needs explicit identity, effective date, method, raw-record digest, verified classification and `TW-RAW-ACTION-TYPES-V1`.

Cash-only classification requires type `息`, a positive measured cash dividend, exactly zero stock-dividend shares, and zero rights subscription amount/ratio if those fields are present. Missing required fields, null optional rights fields, contradictory classifications, unknown types and incomplete coverage fail closed. Zero stock-dividend shares alone does not prove a cash-only event: the provider documents cash capital increases with type `權`.

A verified cash-only event is retained as a research warning: raw-price momentum, moving averages and patterns contain an ex-dividend gap and are not total-return measures. No dividend addback or guessed adjustment factor is applied. Stock dividends, rights, splits/reverse splits, capital reductions, par changes and capital increases crossing the consumed window are structural holds. A known event effective on the first consumed bar is recorded but not a crossed transition because all seeds begin after that event; an unknown first-bar event still blocks. Overlapping source windows must be deduplicated without dropping intermediate boundary dates. Structural holds can later clear only with sufficient coherent post-event history or an independently verified adjusted-basis policy; they are not permanent issuer exclusions. Existing frozen-plan action rules are unchanged.

The documented `adjusted=true` option is not sufficient evidence of adjustment factors, current-close anchoring, or volume treatment, so this module does not synthesize adjusted histories.

Primary source contracts:
- [Fubon historical candles](https://www.fbs.com.tw/TradeAPI/docs/market-data/http-api/historical/candles/)
- [Fubon dividends](https://www.fbs.com.tw/TradeAPI/docs/market-data/http-api/corporate-actions/dividends/)
- [Fubon capital changes](https://www.fbs.com.tw/TradeAPI/docs/market-data/http-api/corporate-actions/captital-changes/)
- [TWSE ex-right/dividend report scope](https://wwwc.twse.com.tw/zh/announcement/ex-right/twt49u.html)

## Output and later stages

The private return contains derived daily values, provenance and explicit missing-input reasons, but no raw bars. It must remain server-side memory. Only `safe_status`'s fixed counts/reason codes may cross the diagnostic boundary; it excludes prices, symbols, hashes, indicators, plans and arbitrary strings. This projection does not grant redistribution rights for raw or derived market data.

Stage B, separately reviewed: re-run the existing fixed two-symbol collector in the existing authorized source-only workflow; consume bars in Railway memory; compare existing authorized official snapshots; validate actual SDK action response shapes and full-window coverage. Publish only bounded readiness counts. Do not call this successful until actual evidence passes.

Stage C design only: freeze the full manifest and source session, acquire sequentially with a shared budget at most 50 historical calls/minute under the documented 60/minute ceiling, a manifest-sized call cap and an eight-minute deadline. Corporate-action calls require their own documented shared endpoint budgets and an explicit total request budget before activation. Use one private asynchronous job because full-cohort acquisition exceeds the current synchronous HTTP lifetime. No automatic retry or replay after deployment; restart destroys raw-memory state and marks the job incomplete. No partial-cohort promotion. This requires separately reviewed job lifecycle/auth integration; Stage A adds no route, persistent access or background acquisition.

Full scoring remains a later coherent integration. Existing formal scoring assumes intraday numeric inputs, so passing absent values or fabricated neutral values would be wrong. The next scoring integration must define explicit daily-only availability and validate every non-price dependency, preserve formal weights/ranks, and give the shadow model its own immutable identity and future-only enrollment. Stage A makes no prediction-efficacy or trade-readiness claim.
