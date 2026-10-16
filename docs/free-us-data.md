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
   `US-FREE-DELAYED-V1` capability without asserting free entitlement.
2. In existing `stock-briefing.yml`, dispatch `source_validation_only=true` and
   `us_free_data=true`. The existing OIDC-authenticated relay accepts this opt-in
   read-only request, regardless of the default paid mode. The diagnostic prints
   counts and missing symbols only, using a frozen current-report 188-symbol pool.
3. A successful paid-account probe validates this historical route and symbol
   coverage. It does NOT verify the account's entitlement after downgrade.
4. Before activation, inspect live references, missing symbols (including OTC
   HNHPF), reports, and the owner UI. The separate Vercel source must not be replaced
   by this repository's static preview. Its hard-coded SIP labels require separate
   review before claiming that owner application is ready for free mode.
5. Only after validation/acceptance, explicitly set `ALPACA_DATA_MODE=free_delayed`
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
