## Why

Desktop and phone capabilities need one user-owned local MCP device shape rather than platform-specific integrations in the cloud daemon.

## What Changes

- Pair a user-installed desktop companion exposing local MCP tools over an authenticated outbound tunnel.
- Extend the same shape to Android/iOS with OS permission-aware tool availability.

## Capabilities

### New Capabilities

- `companion-local-mcp`: Desktop and phone capabilities need one user-owned local MCP device shape rather than platform-specific integrations in the cloud daemon.

### Modified Capabilities

None; consume the existing owning contracts below.

## Impact

Planning owner: Codex. Branch: `spec/muse-pi-gap-proposals`. Proposal-only follow-up requested 2026-10-04; no product code, deployment or PR. Future implementation: one separately claimed worktree/branch/PR for this intent.

connect-anything-ladder supplies attach/discovery; private-network-attach provides optional private reachability; workspace-node owns existing node identity/enrollment; inline-connect-and-approve supplies decisions. No cloud uptime dependency on a companion.
