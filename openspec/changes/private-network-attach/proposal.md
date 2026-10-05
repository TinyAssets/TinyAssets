## Why

Private and self-hosted APIs/MCP servers need a generic owner-network route without exposing platform infrastructure or requiring a device app.

## What Changes

- Add an owner-bound overlay network connection using secret-entry custody and outbound attachment.
- Permit explicitly granted private destinations with first-contact host approval and immediate revocation.

## Capabilities

### New Capabilities

- `private-network-attach`: Private and self-hosted APIs/MCP servers need a generic owner-network route without exposing platform infrastructure or requiring a device app.

### Modified Capabilities

None; consume the existing owning contracts below.

## Impact

Planning owner: Codex. Branch: `spec/muse-pi-gap-proposals`. Proposal-only follow-up requested 2026-10-04; no product code, deployment or PR. Future implementation: one separately claimed worktree/branch/PR for this intent.

connect-anything-ladder owns MCP/card/custody; inline-connect-and-approve owns protected decisions; existing egress broker owns destination enforcement. This changes no public top-level MCP handle.
