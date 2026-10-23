## Context

N8 follows Direction 8 and `owner-daemon-in-box/design.md` T1, T4, T6 and T8. It folds the desktop portion of `companion-local-mcp` into one delivery change.

## Goals / Non-Goals

**Goal:** Give an owner an optional connector from their hosted box to their computer while every hosted surface remains usable without it.

**Non-goals:** A platform host path, a required desktop tray, shared device-data processing, or provider-specific integration.

## Decisions

1. The owner adds a computer connector package to their own box. Connection configuration, capability grants, dispatch and results are owner data in that box. Reuse existing node identity and connection incarnation. Per-capability Off / Read / Read+interact defaults Off and intersects OS grants at dispatch; the companion enforces it again. The shared layer never decides a local capability or reads device results.
2. Pair through protected owner confirmation. The companion keeps its device credential in the OS keystore; the broker holds the owner/device/incarnation binding, credential verifier and revocation state. Broker custody mints and validates one box-scoped channel credential, never a fleet credential or raw secret in the box. Unpair, logout and lost-device revocation invalidate the verifier and active channel before cleanup; re-pair mints a fresh credential and incarnation.
3. The user-installed companion in `desktop-app/` opens an outbound authenticated connection to the existing `/api` door. The broker authenticates and binds it to the admitted owner's box; the router relays opaque frames between that channel and the box connector. Box-to-companion replies use that same relay, never direct box egress to a private computer address. The broker keeps channel identity/availability only; device payload, paths and policy stay in the box and companion. No computer port or host mount is exposed to the platform.
4. Delete the legacy host in `tinyassets/desktop/tray.py` and its old tray launch path during N8 implementation. `desktop-app/` stays the thin app shell and packages the optional companion; its absence cannot affect hosted service. Desktop files, commands and screenshot/click are exposed only where the OS permits. Phone adapters may follow the same protocol without becoming an alternate desktop queue.
5. Offline, unpaired or revoked computers return an explicit unavailable result for local actions. A dropped in-flight action reports unknown outcome without replay. Hosted chat, MCP, API, inbound hooks, schedules and box lifecycle continue independently; no shared waker or admission rule depends on computer presence.

## Risks / Trade-offs

- Local OS permissions and device availability vary; advertise only current supported capabilities and report offline state.
- Revocation races active local work; the connector fences new dispatch and reports an unknown outcome rather than replaying an uncertain action.
