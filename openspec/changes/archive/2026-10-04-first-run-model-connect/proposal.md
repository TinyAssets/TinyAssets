## Why

A new user's bubble message must lead directly to connecting their own model. The platform has no LLM and no reviewer credential.

## What Changes

- Render a provider-data connection card on a deterministic setup refusal and retain the original send.
- Use a popup/system browser with server-held PKCE and browser-bound callback; automatically continue the original message after verified connection.
- Preserve cancellation and failure recovery and fence all state by owner and home.

## Capabilities

### New Capabilities
- `first-run-model-connect`: inline model connection and retained-message continuation.

### Modified Capabilities

None.

## Impact

Onboarding SPA, hosted model connection ingress, setup refusal payload, Chromium and authenticated ingress tests. Branch feat/first-run-model-connect, stacked on #4449. Owner: Codex. No PR or deployment requested.
