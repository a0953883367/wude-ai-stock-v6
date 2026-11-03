# Prediction checkpoint close validation

New checkpoints and archive bootstrap require a verified session in the existing
`reports/official_market_calendar.json` cache and a close at or before the
report's original `updated_at`. Missing/unverified calendars, holidays, missing
US close times, mixed/missing official session dates, malformed timestamps and
pre-close reports fail closed. There are no new provider calls or credentials.
The existing morning-US/evening-TW and intraday restrictions still apply.

Alpaca supplies per-session US open/close times, including early closes:
https://docs.alpaca.markets/us/v1.1/reference/getcalendar-1
The existing calendar uses America/New_York and Asia/Taipei, so UTC offsets vary
with DST rather than a fixed UTC cutoff. TW regular sessions end at 13:30; the
existing TWSE/TPEx holiday/exceptional-closure calendar must confirm the date:
https://www.twse.com.tw/en/about/company/service.html

NIST confirms 2026 DST ends November 1:
https://www.nist.gov/pml/time-and-frequency-division/popular-links/daylight-saving-time-dst
November 2's 16:00 New York close is November 3 05:00 Taipei (21:00 UTC).
The reported November 3 02:38:25 Taipei checkpoint is November 2 13:38:25 New
York, before close in either season. The fixed morning cron slots correspond
to 05:35/05:45/05:55 Taipei, after regular close in both seasons. However, the
silent-refresh selector can label an earlier run `morning`; the new engine
check validates the report timestamp independently of that label. Schedules
are unchanged.

## Existing records and current availability

This change does not rewrite, delete, refreeze or certify existing forecasts,
prices, settled outcomes or restored private backups. Public policy metadata
explicitly says existing frozen records were not revalidated and may predate
this check. Archive rejection counts disclose why new imports were refused.
A later legal checkpoint cannot retroactively validate an earlier record.

In e7f06f637c55693722b733036d896a72a51e9502, the 04:57:36 batch has 380 analysis
identities but 3790 frozen forecasts: US:LEU lacks all ten horizons. The preceding
02:38:25 morning batch had no LEU history; later evening analysis recovered LEU,
but the US checkpoint did not run. The delivery census continues to reject this
batch. Guard tests and a successful code deployment are not evidence that this
historical data or the current public delivery has been repaired.
