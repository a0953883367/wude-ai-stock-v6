# Private US research view

`private-research.html` is a public, data-free UI shell. Research values are
fetched only after an explicit owner action and are never embedded in HTML,
GitHub reports or notification messages. The existing decision hub links here.

## Access boundary

POST `/api/private/us-research` accepts exactly one known current US symbol.
It requires either an explicit matching existing owner token or an already-
issued, valid paired-device read-only token. Existing paired-device access to
historical research has been explicitly approved. No token is issued, stored,
rotated or paired by this feature. It rejects shared-site tokens, public-read
mode and unproven trusted-proxy-header fallback. The allowed-origin check is
additional to authentication, not a replacement for it. No trading permission
or formal decision eligibility is added.

The UI only reads the existing browser token. If none is available, it explains
that an already-authorized device is needed; it never invokes pairing or sends
a Telegram verification code. Actual end-to-end private viewing requires an
already-paired owner browser; synthetic tests cannot establish that session.

## Data and resource limits

- One explicit request, no polling or automatic request on page load.
- Global single-flight and 30-second request-start cooldown; no result cache.
- At most two provider calls: four-session AAPL entitlement check and the chosen
  symbol's closed-session SIP history. Maximum 400 calendar days and a shared
  30-second processing deadline, subject to bounded socket inactivity timeout.
- Existing fixed provider host/path, split adjustment and calendar checks.
- Same immutable projection and all existing discontinuity/identity/history
  holds. No fallback source, inferred adjustment or eligibility override.
- Response includes only the requested private features/illustrative geometry
  and provenance, never a raw historical bar array.
- `Cache-Control: no-store`; DOM-only rendering, no local/session storage,
  downloads, analytics, console payload logging, service worker or notifications.
  Page hiding aborts the browser request and clears displayed values; an already
  running bounded backend computation may finish and then discard its values.

Geometry is unvalidated private research, not an investment instruction.
Formal V6, rankings and order paths are unchanged. Durable prospective storage
and public distribution rights remain unresolved and are not enabled here.

## Verification

Tests cover public/shared/proxy auth rejection, existing owner/device acceptance,
expired/tampered tokens, no-store error responses, private exception redaction,
unknown symbols, single-flight, cooldown, request cap, unchanged holds and lock
recovery. Browser tests cover explicit-only fetch, pending lockout, text rendering,
visibility clearing/abort and stale response suppression. Production verification
must never create a token or bypass auth merely to demonstrate a private result.
