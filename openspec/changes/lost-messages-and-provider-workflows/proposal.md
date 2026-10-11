## Why

An interrupted app-agent turn loses the owner's message because conversation writes happen after dispatch. Owner provider cells and workflow HTTP inference also fail on platform plumbing before connected sources can be used.

## What Changes

- Commit admitted owner messages before dispatch; retain failed-turn effect evidence for subsequent turns without replay.
- Give provider cells private owner-owned homes outside temporary directories and verify shipped Codex discovery and execution.
- Account for owner-connected workflow inference through the normal reservation path without requiring a separately configured budget.

## Capabilities

### New Capabilities

### Modified Capabilities

- `conversation-failure-history`: durable ingress and failed-turn continuity.
- `provider-routing`: provider cell homes and accounted workflow inference.

## Impact

Conversation store and ingress, orphan recovery, provider cell setup, workflow inference, production-image proofs. One intent: preserve an owner's agent work across platform failures. Owner: Codex; branch: `fix/lost-messages-and-codex-cells`. Production inspection is read-only; PR #4602 and owner artifacts are outside this lane.
