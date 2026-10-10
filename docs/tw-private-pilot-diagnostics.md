# Taiwan private pilot diagnostics (Stage B)

## Purpose and activation boundary

This stage evaluates the already verified Fubon daily-history source with a private consumer. It is an opt-in extension of the existing `tw_daily_history_status` request and existing source-only stock-briefing principal. It creates no authentication grant, device route, trading action, scoring, ranking, plan enrollment, or public market-value feed. The flag is off by default and is available only with the source-validation-only workflow path. Diagnostic mode requires an already initialized SDK session; it returns no-session without invoking the legacy ownership warmup or login. The default source probe keeps its previously reviewed behavior. That path does not generate reports, send Telegram, or commit market data.

A successful diagnostic run is not decision readiness. Corporate-action completeness, non-price feature completeness, and model integration remain separate. The frozen full Taiwan report membership is retained in the accounting; only 2330.TW and 6290.TWO are acquired. The current report has 192 Taiwan members, so a two-member pilot leaves 190 unacquired. ETF taxonomy remains unchanged. Missing venue metadata for other members is not invented from ticker suffixes, and there is no partial-universe ranking.

## Request and memory budget

One enabled invocation has a maximum of:

- Two historical-candle SDK calls, under the existing fixed pilot guard.
- Two corporate-action SDK calls: one `corporate_actions.dividends(start_date, end_date)` and one `corporate_actions.capital_changes(start_date, end_date)`. These endpoints return date-range market-wide data and have no documented symbol filter. Only pilot records are retained after validation.
- Two reads of the existing approved official latest-price OpenAPI feeds, if matching trusted snapshots are not already supplied. These are current daily feeds, not restricted historical scraping.

No automatic retry, alternative endpoint, date splitting, pagination discovery or broader history acquisition is allowed. Corporate-action quotas are not documented separately; the historical-candle quota is not represented as a corporate-action quota. Rate-limit and permission errors stop the dependent sequence.

All work shares the existing caller waiting budget and single-flight guard. Deadline/cancellation is checked before and after each operation. A caller timeout does not cancel an SDK socket or free the guard for a second worker. A late response is discarded and no further request is admitted. SDK payload validation happens after the SDK receives the response; it cannot bound the SDK's internal transport allocation. Official HTTP reads use explicit streaming byte and row limits and bounded timeouts. No response body, arbitrary provider error, raw event, price, indicator, response hash or corporate-action amount is included in public diagnostic output.

## Evidence and comparison

The consumer checks exact history identities, raw/unadjusted request provenance, shares volume, complete source sessions, post-response observation time and settlement timing. Every daily primitive is computed from those private bars. The official raw snapshot is independently reparsed and must have matching source URL, source session, digest and observation provenance. OHLCV agreement is exact; an official close never replaces the history close.

The corporate-action window covers the entire actual consumed history, including indicator warmup and both boundaries. It is not truncated to the last 60 bars. The provider does not document the 60-day limit used by a separate exchange-report wrapper, so that unrelated limit is not applied here.

Dividends use the documented `date`, `exchange`, `symbol`, `dividendType`, `cashDividend` and `stockDividendShares` fields. Explicit cash-only events can be classified; zero stock-dividend shares alone does not prove cash-only because rights/capital-increase events can also have zero stock shares. Capital changes use `actionType`, `resumeDate`, `haltDate` and documented raw split details. Unknown or contradictory target events remain unknown. Positive structural events justify a hold; no guessed adjustment factor is constructed.

## Coverage diagnostics rather than unsupported completeness claims

The response contract documents a data array but does not expose documented pagination, total-count, historical-retention or inclusive-boundary guarantees. The pilot records concrete observed row counts, target counts, rejected/duplicate/conflicting rows, field-presence/schema results, date bounds and recognized range/pagination metadata. Unknown pagination or truncation signals are reported without issuing guessed follow-up calls.

Successful arrays, including empty arrays, remain `provider_coverage_unverified`. This is a specific unresolved coverage contract, not a claim that the provider is unreliable or that all future data must be guaranteed accurate. Actual diagnostics should guide the next narrowly scoped check. Returned positive events may be classified independently of completeness; absent rows are not certified as no events.

## Primary contracts

- [Fubon historical candles](https://www.fbs.com.tw/TradeAPI/docs/market-data/http-api/historical/candles/)
- [Fubon dividends](https://www.fbs.com.tw/TradeAPI/docs/market-data/http-api/corporate-actions/dividends/)
- [Fubon capital changes](https://www.fbs.com.tw/TradeAPI/docs/market-data/http-api/corporate-actions/captital-changes/)
- [Fubon rate limits](https://www.fbs.com.tw/TradeAPI/docs/market-data/rate-limit/)

The current implementation and tests are the authority for exact numerical limits. Offline fixtures are synthetic. Real runtime success must be reported only after a separately coordinated source-only invocation completes after deployment; merging this code is not proof of runtime coverage or decision completion.
