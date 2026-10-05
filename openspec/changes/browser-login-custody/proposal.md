## Why

Connect login-only sites through owner-bound credential custody.

## What Changes

- Connect login-only sites through owner-bound credential custody.
- Consume existing card, request authority and extension/connection lifecycle.

## Capabilities

### New Capabilities

- `browser-login-custody`: Connect login-only sites through owner-bound credential custody.

### Modified Capabilities

None. The connect card and onboarding-web-app delta remain solely in inline-connect-and-approve.

## Impact

Planning owner: Codex; branch `spec/muse-pi-gap-proposals`. Future implementation: one dedicated worktree/PR for this intent.

Dependency order: `connect-anything-ladder` connection metadata/lifecycle and secret entry, then completed universe-agent-harness D5 browser/live-view substrate; consume the card owned by `inline-connect-and-approve`. D5 is a release gate: no browser fallback until its real broker passes custody acceptance.
