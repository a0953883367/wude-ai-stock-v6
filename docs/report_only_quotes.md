# Report-only official quotes

`fetch_taiwan_official_data` now saves a separate top-level
`report_only_quotes` section in `reports/tw_official_cache.json`. It captures only:

- `2412`: TWSE ordinary stock, from the existing `STOCK_DAY_ALL` response
- `00998A`: TPEx ETF, from the existing `tpex_mainboard_daily_close_quotes` response

No separate requests or retries are added. A market is observed only when the
existing formal universe already causes that market's parser to run. The formal
five-element parser returns, fetch return, `data` cache payload, universe,
ranking/model inputs, financial/institution/credit/ownership data, watchlist,
search data, and morning/intraday freeze schedule are unchanged. This does not
add ESB `7415`/`7815` or US OTC `HNHPF` coverage.

## Availability and provenance

The schema declares `scope: report_only`, `formal_model_input: false`, and
`ranking_eligible: false`. `records` is keyed by exact string code, preserving
`00998A`'s leading zeros and uppercase A. Only surrounding whitespace is
normalized; numeric codes, alternate case, padded IDs, suffixes, and partial
matches are rejected.

`status: available` means **a validated positive official close and a valid
source session date are present**. It does not promise a complete OHLCV bar.
`quote.open`, `high`, `low`, and `volume` remain null if absent or invalid; they
are never synthesized from close or another field. Explicit zero volume is
valid. Volume is parsed exactly and must be a nonnegative integer. Implausibly
large numeric representations (over 128 characters or native integers over
256 bits) are rejected to keep validation and JSON serialization bounded. NAV is never
used. Contradictory supplied OHLC values invalidate the quote.

`raw_fields` records the original source field, whether that key was present,
a JSON-safe raw scalar value, and validity for each mapped field. Unsupported
raw structures/nonfinite numbers, out-of-range integers, and non-UTF-8 raw
strings are represented only by an invalid-type marker.
Conflicting duplicates, including uncertain duplicate raw evidence, fail closed;
identical scalar-field duplicates are accepted and counted. Incomplete rows are
never merged into a synthetic bar. An invalid date or close yields a null quote.

`session_date` is parsed only from the row's `Date`, accepting real calendar
Gregorian/ROC dates. Timestamps are rejected. `observed_at` is the collector's
observation time, not a trading session or assurance of freshness. Different
records may have different source dates. Consumers must check the intended
completed session using their existing market calendar and must not assume
that today's date is the completed session.

`response_completeness` and per-market `completeness` are always `not_verified`.
The existing list API supplies no completeness guarantee; a valid but shortened
list cannot be detected reliably. Missing means absent from the received rows,
not delisted or absent from the exchange. An empty list also cannot distinguish
an empty response from an unavailable/malformed-JSON source because the existing
adapter already converts those failures to an empty list. Malformed list
members fail closed; no extra recovery request is attempted.

## Refresh failures and cache compatibility

A new collector is created on every fetch; previous report-only values are
never inputs. Successful refreshes overwrite this section even when quotes are
missing. If a refresh fails and a previous sidecar exists, only that sidecar is
invalidated with `refresh_status: failed`, null quotes, and `reason:
refresh_failed`. The previous formal `data` and its `updated_at` are preserved.
The invalidation uses a temporary file and atomic replacement, protecting the
old cache against a partial auxiliary write. If writing/replacing fails, a
warning is logged and the previous file remains an explicitly dated snapshot;
a reader must not infer refresh success from its presence. A first-ever failed
fetch does not create a cache or alter the existing formal failure behavior.

Existing readers of `payload['data']` and the fetch return require no changes.
No consumer is switched to using this section by this patch. A future report
consumer can use this pattern, without changing the canonical universe:

```python
# expected_session comes from the report's existing completed-session calendar.
# freshness_cutoff is the report's permitted observation-time lower bound.
from datetime import datetime
import math

# Treat malformed/missing cache sections as unavailable.
def as_dict(value):
    return value if isinstance(value, dict) else {}

sidecar = as_dict(as_dict(payload).get("report_only_quotes"))
record = as_dict(as_dict(sidecar.get("records")).get("00998A"))
raw_fields = as_dict(record.get("raw_fields"))
close_provenance = as_dict(raw_fields.get("close"))
session_provenance = as_dict(raw_fields.get("session_date"))
observed = sidecar.get("observed_at")
try:
    recently_observed = bool(observed) and datetime.fromisoformat(observed) >= freshness_cutoff
except (TypeError, ValueError):
    recently_observed = False
usable = (
    sidecar.get("schema_version") == 1
    and sidecar.get("scope") == "report_only"
    and sidecar.get("formal_model_input") is False
    and sidecar.get("ranking_eligible") is False
    and sidecar.get("refresh_status") == "completed"
    and recently_observed
    and record.get("code") == "00998A"
    and record.get("market") == "TPEx"
    and record.get("instrument_type") == "ETF"
    and record.get("price_basis") == "official_close"
    and record.get("status") == "available"
    and record.get("session_date") == expected_session
    and close_provenance.get("source_field") == "Close"
    and close_provenance.get("valid") is True
    and session_provenance.get("source_field") == "Date"
    and session_provenance.get("valid") is True
)
quote = as_dict(record.get("quote")) if usable else {}
close = quote.get("close")
try:
    valid_close = (
        not isinstance(close, bool) and isinstance(close, (int, float))
        and math.isfinite(close) and close > 0
    )
except OverflowError:
    valid_close = False
if not valid_close:
    close = None
# Display missing when close is None. Retain the session, units, source, and
# attribution. A null O/H/L/volume is still missing, never an assumed full bar.
```

Use timezone-aware datetimes for `freshness_cutoff`. Observation recency cannot
prove an attempted refresh succeeded if a failed filesystem write left an old
snapshot. For reports coupled to a particular refresh, also require that
`observed_at` falls within that refresh's successful collection interval.

## Attribution

Preserve the data-providing agency, exchange, dataset title/link, source endpoint,
source session/attribution year, and license notice when displaying or
redistributing the report data:

- Government metadata agency: 金融監督管理委員會證券期貨局
- TWSE / Taiwan Stock Exchange: 上市個股日成交資訊,
  https://data.gov.tw/dataset/11549,
  https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL
- TPEx / Taipei Exchange: 上櫃股票行情,
  https://data.gov.tw/dataset/11370,
  https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes
- Open Government Data License, version 1.0: https://data.gov.tw/license

The row API does not supply a dataset revision: `dataset_version` remains null,
not a fabricated version. `attribution_year` comes from the validated source
session, not the observation timestamp. All test prices/volumes are synthetic.
No market-price cache artifact is included in this change.

## Offline tests

With normal repository dependencies installed:

```sh
python -m unittest discover -s tests -p test_tw_report_only_quotes.py -v
python -m pytest -q tests/test_tw_official_data.py tests/test_tw_report_only_quotes.py
python -m py_compile tw_official_data.py tw_report_only_quotes.py
```

The new tests block HTTP/socket access and mock all official responses. They
cover exact symbols, dates/provenance, null fields, exact volume, duplicates,
malformed/truncated input, request parity, cache isolation, stale invalidation,
and atomic-write failures. Running these tests does not refresh actual prices.
