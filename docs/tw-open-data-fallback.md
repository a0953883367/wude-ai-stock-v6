# Taiwan completed-close fallback

The non-freeze briefing path opts into one additional TWSE batch only if its
primary official prices are incomplete or older than the most recent completed
Taiwan session. That session must come from the locally cached, verified official
calendar. No calendar network request or weekday inference is introduced.

## Source and authorization

- Dataset: https://data.gov.tw/dataset/11549
- Exact licensed resource: https://www.twse.com.tw/exchangeReport/STOCK_DAY_ALL?response=open_data
- License: Government Open Data License v1.0, as linked by the dataset page.
- Attribution in rows: `TWSE Open Data`, with the exact resource URL.
- Prices are TWD; `成交股數` is shares, not lots. No 1,000 multiplier.

This current-snapshot open-data resource is distinct from the OpenAPI release.
On 2026-10-14 UTC the resource supplied the 2026-10-13 session while the failed
briefing's OpenAPI snapshot was dated 2026-10-12. A bounded read established all
32 maintained listed ETFs were covered. The separate TPEx ETF is unchanged.
The license of this current CSV is not an authorization for bulk monthly history
or arbitrary dated website endpoints.

## Safety and failure behavior

Exact code, valid ROC/Gregorian date, exact completed session, positive finite
OHLC, consistent candle geometry and non-negative integral share volume are
required. Missing, duplicate, stale, future, malformed and incomplete rows are
rejected. Raw share values cannot round fractional values into integers. A
2 MB response cap and one request per briefing prevent unbounded fallback work.
Failures retain the original primary inputs; the existing report date/coverage
and individual regression guards still decide whether publication is allowed.

Only a supplied daily bar can replace/append a bar to already-observed history.
No price history is created from a snapshot, no missing value is filled with zero,
and a newer primary date is never replaced. OpenAPI credit dates and report-only
quote sidecars continue to use their original inputs. The new source does not
acquire private/shadow history or weight-evidence eligibility.

The official cache records aggregate `price_fallback_status`; an aborted run's
existing date-only diagnostic also separates the three known official sources.
No credentials, raw subscription data, or symbol prices enter that diagnostic.

## Verification and rollback

Tests cover schema/identity/date/units, invalid bars, duplicates, bounded fallback,
current primary no-request, closed-session calendar, error retention, no-history,
provenance, original report guard, and unchanged credit/sidecar boundaries.
Run the full Python and JavaScript CI suites. Real-source validation must be
reported separately from successful CI and from delivery of any scheduled report.

Rollback: revert this change, or remove the briefing's `use_open_data_fallback=True`
opt-in. The original OpenAPI/Yahoo and fail-closed report guards then remain active.
Rollback must not remove the guard or overwrite the last accepted formal report.
