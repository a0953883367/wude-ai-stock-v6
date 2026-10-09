# Existing Alpaca calendar relay repair

Railway's existing read-only health endpoint returned HTTP 200 and a verified Alpaca US calendar on 2026-10-09, fetched at 10:59:43 UTC, covering 2025 and 2026. SIP and OPRA were configured. GitHub's native Alpaca secrets being absent does not imply that this existing connection is absent.

The existing GitHub Actions OIDC market-data relay supported SIP and OPRA but omitted calendars. Extend that same authenticated route with a bounded, cached, verified calendar response. Keys stay in Railway. No additional market-data fetch is performed by the relay. The client reconstructs and validates dates and open/close evidence; unavailable or unverified evidence remains blocked. Existing repository, main-branch, workflow and event identity restrictions remain enforced.

The existing Agent checks the deployed capability before reserving its bounded silent refresh. Previous failure and dispatch history is retained. Calendar bootstrap does not authorize an unfinished-session forecast, historical reconstruction, duplicate briefing, order, formal V6 change, ranking/weight change or shadow promotion.

Local validation: 933 Python tests, 23 Node checks, CI compilation and diff whitespace checks passed. Tests cover OIDC rejection before payload access, bounded years, whitelisted cached output, absent native secrets, early-close transport, unverified evidence rejection and Agent bootstrap with unchanged history.

Deployment and real authenticated runtime receipts must be checked after merge. A passing workflow alone is not evidence of a new immutable forecast. An intraday 2026-10-09 source must remain blocked until its official session closes, and any forecast must precede the next official opening.
