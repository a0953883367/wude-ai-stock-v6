# Opt-in free-compatible US historical data

Default production behavior remains paid SIP/OPRA. No subscription or billing
change is performed by this patch. No credential or authorization change is
needed. `ALPACA_DATA_MODE=free_delayed` explicitly opts a process into the new
historical path; unset restores the existing paid path.

## What remains available

- Existing Yahoo-based daily analysis/reporting, SEC and FINRA remain unchanged.
- Private quotes return consolidated SIP minute bars ending at least 16 minutes
  before observation, plus the source and actual bar timestamp.
- Symbols without recent minute bars may return a completed daily bar, explicitly
  marked `1Day`. Missing/unsupported symbols remain missing, never fabricated.
- Private minute bars are not written into public report rows. Reports show an
  isolated delayed reference price without replacing the formal price or weights.
- Historical SIP daily research remains separate and uses completed sessions.

## Capabilities that free mode cannot preserve

There is no full-market real-time NBBO, OPRA layer, or 188-symbol real-time large-buy
stream in free mode. Free IEX is not substituted for consolidated market volume.
OPRA requests and the US live stream are disabled only in explicit free mode.
Taiwan service and all authorization checks are unchanged.

## Validation and activation

1. Deploy the backward-compatible code with mode unset. `/health` reports the
   `US-FREE-DELAYED-V4` capability without asserting free entitlement.
2. In existing `stock-briefing.yml`, dispatch `source_validation_only=true` and
   `us_free_data=true`. The existing OIDC-authenticated relay accepts this opt-in
   read-only request, regardless of the default paid mode. The diagnostic prints
   counts and missing symbols only, using a frozen current-report 188-symbol pool.
   It separately checks completed SIP daily bars and the explicitly labelled
   attested Yahoo daily report cache. The cache does not claim SIP coverage and
   rejects incomplete, inconsistent, future, duplicate, or over-seven-day rows.
3. A successful paid-account probe validates this historical route and symbol
   coverage. It does NOT verify the account's entitlement after downgrade.
4. Before activation, inspect live references, missing symbols (including OTC
   HNHPF), reports, and the owner UI. The separate Vercel source must not be replaced
   by this repository's static preview. Its hard-coded SIP labels require separate
   review before claiming that owner application is ready for free mode.
5. Dispatch `us_free_report_validation=true` alone to exercise the full report
   pipeline in a temporary runner with free mode. This separate job has read-only
   repository permission and no delivery, publishing, cache-save, or commit step.
   It checks all 188 US identities, valid formal prices, delayed references, zero
   false-live/OPRA claims, and absence of private raw bars in report output.
6. Only after validation/acceptance, explicitly set `ALPACA_DATA_MODE=free_delayed`
   in the existing Railway service and report environment. Do not remove existing
   credentials; historical Basic requests still require authentication. Then the
   owner can cancel the paid subscription themselves and rerun the same probe to
   verify actual Basic entitlement. Rollback is unsetting the mode.

The optional `us_free_daily_fallback.py` adapter is a separate private completed-
session research utility, not an automatic replacement for formal model inputs.

## Provider evidence

- https://docs.alpaca.markets/us/docs/market-data-faq: SIP latest/snapshot requires
  subscription; historical `end` must be at least 15 minutes old without one.
- https://docs.alpaca.markets/us/docs/about-market-data-api: Basic historical
  200 calls/minute, IEX-only real-time and 30-symbol stock streaming cap.

Reference report blob `c8a09cab4a8a0d9ccb3c3a11b73b56af883672ca` dated
2026-10-16 21:56:45 has 380 rows / 188 US rows. Already-paid SIP availability was
187/188 (HNHPF unavailable), while HNHPF had a Yahoo daily source. This observation
is not a free-path runtime test or a guarantee of future Yahoo availability.

On 2026-10-16, workflow run 38927404192 verified the V1 historical route on the
still-paid account: 187/188 symbols returned delayed minute bars, with HNHPF
missing. Zero unexpected live fields and zero market-value exports were reported.
V2 adds the separately labelled existing Yahoo daily cache for that gap; it must
be verified by a new runtime probe before claiming 188/188 functional coverage.

V4 retains validated minute rows if the missing-symbol daily lookup fails and
tries completed daily bars after a failed minute retrieval under the same75s
budget. Diagnostic failures remain visible as fixed-category stage receipts.
The Yahoo fallback checks bounded current/archived report snapshots (27files,
8MiB/file,64MiB total), selecting the newest non-conflicting valid completed
session within7NY dates. Snapshot publication must itself be after that
session close plus16minutes, preventing archived partial intraday bars from
becoming completed merely by aging. No provider calls or cache writes are added.
