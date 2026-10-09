# Point-in-time events: shadow registry v1

`point_in_time_events.py` adds a pure, read-only event registry. It makes no requests,
requires no credentials, persists nothing, and never changes scores, formal V6
weights, ranks, or orders. `build_event_snapshot(row, cutoff=aware_datetime)` is the
row-level integration contract. Only compact audit metadata should be published
in cards; raw revision histories can be large and belong in internal research.

## Adapter contract

`row.point_in_time_events` is a list of observed immutable revision records:

- Required strings: `event_id`, `symbol`, `source`, `revision`
- Explicit `event_type`: `earnings`, `guidance`, `filing`, or `news`
- `published_at`: original publication, complete ISO timestamp with timezone
- `first_seen`: when this exact revision was first actually observed; never backdate
- Optional `revision_published_at`: precise amendment publication; otherwise original publication
- Optional `period_end`, `payload`, `url`: supporting metadata, never availability timestamps

The cutoff also requires a timezone. A caller converting existing naive report
wall times must explicitly use the report's established Asia/Taipei convention.
Events require publication <= revision publication <= first seen <= cutoff.
Date-only, naive, missing or inconsistent timestamps are excluded with reason
counts. This deliberately sacrifices coverage rather than inventing midnight.

Filtering occurs before deduplication and revision selection. Future corrections
cannot replace, conflict with or appear in the history of an earlier cutoff.
Repeated identical revisions retain the earliest recorded observation. Conflicting
content under one revision ID quarantines the entire event identity at that
cutoff, retaining unambiguous old revisions only in history. This avoids reviving
an older value after a conflicted amendment. No reconciliation is inferred.
Ambiguous equally-timed latest revisions also fail closed. Revision identity includes source and symbol; distinct publishers are not
counted as independent confirmations. Trust is a fixed exact official-source
allowlist; caller-provided booleans and source substrings cannot upgrade it.
Trust does not establish authenticity or independently verify event content.

## Existing repository sources and limitations

- `news_risk.classify_news` currently reduces publication to YYYY-MM-DD. The row
  adapter includes these articles only for exclusion auditing. It does not recover
  missing precision from `news_scanned_at`, the title, report time or market close.
- `tw_official_data` returns MOPS announcement dates without publication times.
  These likewise cannot qualify for point-in-time event evidence.
- `sec_edgar` latest fundamentals consolidate repeated fiscal periods and revisions.
  Their `financial_report_date` is not an event publication timestamp.
- `sec_companyfacts_events` can consume the raw Company Facts shape used by
  `sec_edgar`, retaining accession, filing date, period ends and original facts.
  Its date-only filing dates are excluded by the registry. Each accession is a
  separate filing; no amendment relationship or earnings surprise is inferred.

No production timestamped earnings/guidance feed, forecast calendar or consensus
estimates were connected. No historical event archive is created by this module.
Replay callers must supply all captured immutable revisions including actual
first-seen timestamps; a latest-only snapshot cannot establish historical coverage.
Outputs explicitly mark production event coverage as not validated. Empty eligible
events means unavailable evidence, not absence of events or a safe trading window.

## Verification

`tests/test_point_in_time_events.py` covers exact cutoff boundaries, late observation,
timezones, missing precision, inconsistent times, revisions, immutable inputs,
deduplication, conflicting/ambiguous versions and conservative source trust.
It calls the actual existing news classifier without network access to verify its
current date-only output schema, and tests synthetic SEC Company Facts payloads.
These checks validate adapter behavior, not live vendor coverage or investment
performance. Integration tests belong to the decision-hub/trade-plan pipeline.
