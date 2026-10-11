## Why

An interrupted app-agent turn loses the owner's message because conversation writes happen after dispatch.

## What Changes

Commit admitted owner messages before dispatch, store and announce each send exactly once, acknowledge retries without redispatch, and link replies and failed-turn outcomes to the original row. Retain failed-turn effect evidence for subsequent turns without replay.

## Capabilities

### Modified Capabilities

- `conversation-failure-history`: durable ingress, retry deduplication and failed-turn continuity.

## Impact

Conversation store, ingress and tool evidence, with message and injected-fault tests. Owner: Codex; branch: `fix/lost-messages-and-codex-cells`.

Provider-cell changes and their tests/specs are restored to main. Founder direction (2026-10-11; architecture PRs #4616/#4617) requires one model-call path for every caller and named external-agent packages for Claude/Codex CLIs, replacing per-path provider cells.
