## Why
A failed activity left a zero-round ready turn blocking the owner chat indefinitely. Process-tree ownership does not prove a task is running.

## What Changes
Require a durable per-turn runner claim, settle runner exits and orphans, allow scoped Stop recovery, and report activity failures honestly.

## Capabilities
### New Capabilities
- `agent-turn-runner-liveness`: runner ownership and orphan recovery.
### Modified Capabilities
None.

## Impact
Agent turn journal/coordinator, boot recovery, Stop, activity admission and failure delivery. Owner: Codex. Branch: fix/orphan-ready-turn-blocks-chat. One draft PR to main.
