# Taiwan exceptional exchange closures

Annual TWSE/TPEx holiday tables do not necessarily include emergency closures announced later. A provider missing a bar does not establish a holiday. The calendar accepts only reviewed, exact-date exchange notices in its bounded exception registry.

## 2026-07-10: Typhoon Bavi

Both exchanges published notices on 2026-07-09:

- TWSE, concentrated market full-day closure: https://www.twse.com.tw/staticFiles/news/news/tsecnews/8a8216d69ef76943019f46cb86ae0110.pdf
- TPEx, OTC securities including emerging stocks and derivatives full-day closure: https://www.tpex.org.tw/www/zh-tw/news/detail?id=22926&response=json
- TPEx human-readable notice: https://www.tpex.org.tw/zh-tw/about/company/press/detail.html?22926

Verified on 2026-10-10. These primary notices explicitly close July 10 because of the typhoon; settlement moves to the next business day. The exception applies to TWSE, TPEx main-board and emerging stock venues. It does not alter US sessions.

## Cache treatment and validation

The exact closure is removed from both newly fetched TW calendars and previously persisted annual calendar rows when loaded. Existing annual verification status, sources and fetch timestamps are preserved; the separate exceptional-closure metadata records its own announcement/verification dates and primary URLs. Unverified data never becomes verified merely because an exception is known. The overlay cannot add trading sessions.

Read-only cache consumers receive the correction in memory without writing files. The normal refresh/save path persists the migrated row; repeated loads are idempotent. No immutable prediction record, past decision, model weight or market-price row is rewritten, and this does not establish historical point-in-time receipt of the announcement.

Actual Fubon run 38035862636 returned 81 dates for each pilot symbol from June 12 through October 8; the stale annual calendar expected 82, with July 10 the sole difference. The exchange-backed correction produces 81 expected sessions. Exact completeness, OHLCV checks and all other gates remain unchanged; live revalidation is still required.
