## Why

Save, test and revoke reusable connectors authored by the owner's agent.

## What Changes

- Save, test and revoke reusable connectors authored by the owner's agent.
- Consume existing card, request authority and extension/connection lifecycle.

## Capabilities

### New Capabilities

- `saved-agent-connectors`: Save, test and revoke reusable connectors authored by the owner's agent.

### Modified Capabilities

None. The connect card and onboarding-web-app delta remain solely in inline-connect-and-approve.

## Impact

Planning owner: Codex; branch `spec/muse-pi-gap-proposals`. Future implementation: one dedicated worktree/PR for this intent.

Dependency order: `connect-anything-ladder` secret slots/egress and connection lifecycle first; existing extension activation and `inline-connect-and-approve` card next. Independent of browser custody; no D5 dependency.
