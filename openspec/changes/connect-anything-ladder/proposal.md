## Why

Audit L6/L7 found that attached MCP servers are deferred and login-only sites have no credential-safe browser route. Owners must be able to connect any platform through general shapes in chat, without waiting for platform-specific code or a platform LLM.

## What Changes

- Add MCP attachments as owner connections: remote HTTP through existing connection custody, local stdio in the owner's jail, discovered tools callable as `mcp:<attachment>:<tool>` through ta.
- Extend D5's browser broker with inline owner login, daemon-side credential/session custody and opaque handles to the agent.
- Extend the existing inline connect request/continuation with both shapes; retain the originating chat line, exact owner binding, cancellation and receipts.
- Make the connection ladder an editable starter skill using generic OAuth/HTTP/MCP/browser capabilities; no per-platform adapters or required provider directory entries.

## Capabilities

### New Capabilities

- `connect-anything-ladder`: owner-bound MCP attachment and browser-login connection shapes using existing inline requests, connections and the D5 broker.

### Modified Capabilities

None. Existing HTTP/OAuth, request-continuations and connection removal contracts remain prerequisites; this adds new shapes without rewriting their requirements.

## Impact

Planning owner: Codex. Branch: `spec/muse-pi-gap-proposals`; baseline `origin/main` at `9e96ff9595`. Docs only, no PR or product code. Future implementation is a separate worktree/PR coordinated with D5/D6 and the inline UI lane.

Expected integration: `storage/outbound_connections.py`, `ta_capabilities.py`, pending requests, daemon credential custody, D5 browser contexts and the editable connect skill. No new top-level public MCP tool; no changes to `tinyassets/onboarding/app.html` in this lane.

`inline-connect-and-approve` owns binding/approval/continuation and `generic-oauth-connections` owns OAuth; this extends their contracts instead of rebuilding them. D5 owns the browser itself and D6 owns ta. Audit L3 owns card consolidation, L4 owns the editable skill content and D10 its distribution. Audit L5's platform-specific registration plan is not a prerequisite and is superseded here by the founder's general-shapes-only direction.

`broker-streaming-contract` is a prerequisite for HTTP MCP streaming, incremental credential scanning, cancellation and operation reconciliation; the current buffered broker is insufficient.

## Research-driven scope and split (2026-10-04)

Use [Muse parity matrix and build order](../../../docs/design-notes/2026-10-04-muse-connection-methods.md), preserving its evidence caveats. This change also owns one auth-shape connect card with labelled multi-account connections, standard MCP OAuth discovery/registration, private secret entry with egress injection, and tested/revocable saved connector extensions. Browser live view/takeover continues through D5. Separate bounded changes own `private-network-attach`, `companion-local-mcp` and `exact-total-spend-rail`; no implicit implementation claim for any report row.
