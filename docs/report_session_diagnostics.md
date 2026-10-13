# Report-session guard diagnostics

If the existing report-session guard rejects mixed or regressed source dates,
`briefing.py` saves a run-local `.run_diagnostics/report_session_failure.json`
before raising the original error. The failed report stays failed. The workflow
preserves only this exact file as a seven-day GitHub Actions artifact.

The projection includes per-cohort counts of downloaded daily-bar sessions,
features before/after the existing carry-forward rule, and previous formal
sessions. TWSE/TPEx snapshot coverage is counted separately. Only validated ISO
dates, aggregate counts, fixed labels, and report time/period are included.
There are no prices, OHLCV, symbols, provider URLs, credentials, user holdings,
subscription payloads, or free-form error strings.

The file is outside `reports`, excluded from Git and Docker, and is not a model
input, publication, settlement, or delivery receipt. A write failure cannot
bypass the guard; atomic replacement preserves an earlier diagnostic if the
write fails. No permissions, source calls, retry policy, schedules, scoring,
ranking, authorization, or delivery behavior are changed.

## Incident scope

Scheduled late-evening runs 469 and 470 on 2026-10-12 UTC were blocked when
122 of 159 Taiwan-equity rows regressed from 2026-10-12 to 2026-10-08. This
change improves evidence for a subsequent original scheduled attempt. It does
not repair an upstream missing trading day and is not evidence of recovery.
Do not mark recovery complete based on passing code CI or a morning report
that correctly carries a previously validated Taiwan close.

## Non-price alignment tails

The diagnostic also compares the raw downloaded tail-index dates with the last
non-null close dates and counts unpriced tails. These remain aggregate dates
and counts, without symbols, prices, corporate-action values or raw rows.

The Taiwan official overlay compares against the last non-null close, matching
feature construction, rather than a later action-only/empty-price index row.
It trims only that trailing non-price region of its local copy after the
snapshot passes the existing non-regression comparison. A genuinely newer
priced bar is never replaced by an older official snapshot. Missing priced
history cannot be manufactured from a single official snapshot.

This fixes an independently reproduced selection edge case. Run 479 did not
capture raw tail-index dates, so that run alone does not prove it encountered
this edge case. An official 2026-10-12 bar still cannot pass the report guard
against an accepted 2026-10-13 state. Upstream completeness and recovery must
be verified independently on an original scheduled run; no rerun is implied.
