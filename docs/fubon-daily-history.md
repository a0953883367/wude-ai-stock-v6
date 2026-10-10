# Existing-session Fubon daily source pilot

Version: `TW-FUBON-DAILY-PILOT-V1`.

The internal OIDC relay accepts only `tw_daily_history_status` with one or both fixed pilot symbols, `2330.TW` and `6290.TWO`. It uses the existing `LiveDataService._fubon_sdk`; it never initializes a login, requests credentials, mutates authentication, calls order endpoints, selects arbitrary URLs, or expands the OIDC principal. A missing session blocks the probe.

## Source and validation

The sole SDK operation is `sdk.marketdata.rest_client.stock.historical.candles`, with symbol, from, to, timeframe `D`, adjusted `false`, fields `open,high,low,close,volume`, and sort `asc`. There is no undocumented `volume_unit` request parameter. [Official Fubon historical-candles documentation](https://www.fbs.com.tw/TradeAPI/docs/market-data/http-api/historical/candles/) defines daily volume in shares and adjusted as a string request parameter. The returned `adjusted` field is not promised by this contract: request evidence is recorded separately from a verified response echo. An unexpected true/nonboolean echo blocks.

Maximum range is 120 calendar days and maximum requests are two, with no pagination or retries. Only an exact calendar verified jointly by TWSE and TPEx is accepted, and returned dates must cover every requested session once. The requested end is the last official session available before today's documented 16:30 Taipei publication cutoff, or today after that cutoff. A missing session, extra/future date, duplicate, wrong symbol/venue/timeframe, malformed or nonfinite OHLCV, fractional/negative volume, or invalid OHLC ordering blocks that symbol. This does not establish immutable finality or historical point-in-time availability.

At least 60 validated sessions are required. The pilot does not compare with official latest-day prices, certify corporate-action completeness, infer adjustment factors, generate candidate decisions, or change formal scoring. These downstream gates remain explicitly false. Cash dividends and denomination-changing events require distinct later analysis rather than guessed adjustment.

## Runtime bounds and privacy

`DailyPilot` allows one in-flight worker and returns within a 20-second caller deadline. No supported per-call SDK socket-timeout argument is documented. Therefore this is not a hard upstream cancellation: a stuck SDK call retains the in-flight guard, preventing another pilot. A late response is discarded, and a timed-out worker starts no second symbol. No SDK session or transport settings are changed. Authentication/entitlement/rate-limit failures stop the batch and are mapped to fixed safe reasons without provider exception text.

Normalized bars and the canonical-bar digest exist only in the internal `collect_private` result. The route discards them and returns fixed operational counts/statuses. Neither prices, volume observations, raw responses, exception details, credentials, nor account identifiers are sent through the pilot route, written to files, cached, logged, or uploaded as artifacts. Later same-process recomputation can consume this private function only after the actual pilot and downstream eligibility/rights checks. No raw-history transport extension or new derived-data publication is enabled.

The existing stock-briefing workflow runs the two-symbol probe after its report commit and normal ownership refresh, using unchanged OIDC trust. It prints allowlisted metadata only and writes no reports. No new schedule, notification, workflow principal, login, bulk universe mode, or paid entitlement is created. The health marker confirms deployed code, not market-data entitlement; actual runtime metadata is still required.
