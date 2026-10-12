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
