## Context

The existing unpowered converse refusal is deterministic. Hosted acquisition and free-model binding already exist, but the page redirects and keeps the verifier in tab storage. Inline approvals provide the precedent for server-held encrypted browser authorization state.

## Goals / Non-Goals

Keep the message in the bubble, connect the owner's own free model, and continue once. No platform model, paid-model consent, or special reviewer route.

## Decisions

Reuse the existing send and account/home-scoped recovery record. Mark a setup refusal as waiting for a connection; do not treat an ambiguous send as safe to replay. Use a browser lock around automatic continuation across tabs. The persisted record changes to ordinary in-flight state before sending, so a lost response uses existing explicit recovery.

Reuse hosted acquisition and confirmation. A short-lived SQLite side table stores encrypted PKCE verifier and callback code, keyed by the existing random flow handle and owner/home. A top-level popup launcher requires the matching protected owner session before binding a HttpOnly cookie; the callback rechecks that owner and only saves the code. The authenticated originating app polls to redeem it. A system browser without a matching owner session signs in through the existing owner door, which returns only to a flow owned by that verified identity. Provider secrets never enter app responses. The primary provider comes from installed acquisition data.

## Risks / Trade-offs

Browser closes during OAuth -> inline retry, retained message. Exchange interrupted -> report recovery instead of replaying a consumed code. Native browser has separate cookies -> establish owner identity and callback binding in that browser at launch. Browser storage unavailable -> report inability to retain instead of silently losing the message. Existing recovery, rather than a new daemon task, owns interrupted sends. Failed setup attempts remain in durable conversation history alongside the eventual reply, consistent with ordinary retries; the live bubble does not re-echo the original.

## Migration Plan

Additive ephemeral table, pruned at ten minutes. No existing credential or pending-request migration. Push only; deployment and real-account proof remain outside this requested delivery.

## Open Questions

None.
