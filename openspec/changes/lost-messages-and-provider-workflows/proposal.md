## Why

An interrupted app-agent turn loses the owner's message because conversation writes happen after dispatch. Codex owner provider cells also fail during CLI startup.

## What Changes

- Commit admitted owner messages before dispatch; retain failed-turn effect evidence for subsequent turns without replay.
- Give provider cells private owner-owned homes outside temporary directories and verify shipped Codex discovery and execution.

## Capabilities

### New Capabilities

### Modified Capabilities

- `conversation-failure-history`: durable ingress and failed-turn continuity.
- `provider-routing`: provider cell homes and Codex startup.

## Impact

Conversation store and ingress, orphan recovery, provider cell setup and production-image proofs. One intent: preserve an owner's agent work across platform failures. Owner: Codex; branch: `fix/lost-messages-and-codex-cells`. Production inspection is read-only; PR #4602 and owner artifacts are outside this lane.

## Deferred

OpenRouter/workflow HTTP usage reservation is removed from this PR and restored to main, pending the founder-requested architecture review of agent capabilities. No new workflow accounting path is introduced here.
