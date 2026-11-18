# gateway-refusal-code as built (2026-11-18)

The gateway, connect door and owner refresh each decoded a local or RPC refusal and
tested its code against the gateway vocabulary. The vocabulary and the RPC decoder
already live together in `contract/src/refusal.ts`. Its `gatewayRefusal` now owns
that classification. It returns a gateway code or null, so each boundary can answer
for the operation it owns.

- Gateway uses `internal` for unexpected faults and constructs a clean refusal from
  the fixed code. All four error handlers previously supplied that same fallback.
- The connect door keeps N5's response selection. A failed verification revokes the
  pending connection. A gateway 502, deadline or invalid request passes through the
  key verification route. Other faults go to its existing error handler.
- Owner refresh records the fixed gateway code and releases the lease. Only
  `oauth_invalid_grant` and `not_refreshable` mark the connection permanently failed.
  The caller receives the existing `credential_refresh_failed` refusal.

The refusal catalogue's transient comment still described a manifest root that
changed during rewrap. Files reports this through `{ key: null }`. Owner counts
the root in `left`, and the rotation sweep can ask again. The Files and rotation
implementations already agree.

Against `f934871ad`, two local Workers exercised all 143 catalogue codes across RPC
and the five door renderers. All 34 gateway codes went through key verification,
OAuth exchange and verification, and both doorbell operations. Four unexpected
errors also went through the gateway and key connect route. The outputs matched
byte for byte. Forcing the shared decoder to return null changed an invalid
request from 400 to 500 and failed that comparison. A separate
SQLite owner fixture applied the 18 real migrations and exercised 36 refresh
failures. All 36 leases cleared and the same two codes changed connection status.
