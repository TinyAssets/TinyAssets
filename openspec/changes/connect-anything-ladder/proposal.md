## Why

Connect arbitrary MCP services without per-platform code, using existing owner connection authority.

## What Changes

- Add HTTP and jailed stdio MCP attachments, protected secret entry and standard MCP OAuth discovery/registration.
- Default to broker-only credentials; permit explicit warned owner opt-in for a named own key in an exact stdio server revision.
- Split browser custody into `browser-login-custody` (gated on D5) and tested extensions into `saved-agent-connectors`.

## Capabilities

### New Capabilities

- `connect-anything-ladder`: MCP attachment, secret entry and MCP OAuth through existing connections.

### Modified Capabilities

None. `inline-connect-and-approve` solely owns the connect card and its onboarding-web-app delta; this change consumes it.

## Impact

Planning owner: Codex; branch `spec/muse-pi-gap-proposals`. Future implementation is one worktree/PR for MCP attach, separate from browser and saved-connector delivery.

Prerequisites: generic-oauth-connections, broker-streaming-contract and inline-connect-and-approve request/card authority. D6 owns ta; L4/D10 own starter skill distribution. Then saved-agent-connectors consumes slots/egress; browser-login-custody consumes lifecycle only after D5 is complete. Neither follow-up blocks MCP delivery.

The registered OAuth provider directory (`generic-oauth-connections`), including the platform Google client, STAYS as optional data for easy setup. Only per-platform code is out. Unknown services require neither directory registration nor a platform patch.
