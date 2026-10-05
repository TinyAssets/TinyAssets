## Why

Resident gateways, heartbeats and long-running bots cannot be rebuilt using only short-lived jailed calls.

## What Changes

- Add owner-scoped persistent processes with explicit restart policy, health and durable desired state.
- Run resident channel gateways and heartbeats on vendor-neutral cloud compute with zero owner hosts online.

## Capabilities

### New Capabilities

- `resident-agent-processes`: Resident gateways, heartbeats and long-running bots cannot be rebuilt using only short-lived jailed calls.

### Modified Capabilities

None; consume the existing owning contracts below.

## Impact

Planning owner: Codex. Branch: `spec/muse-pi-gap-proposals`. Proposal-only follow-up requested 2026-10-04; no product code, deployment or PR. Future implementation: one separately claimed worktree/branch/PR for this intent.

Extend workspace-node process/lease execution, control-plane-scheduler and automation-agent-lease rather than introducing a second scheduler. command-center-harness-control owns settings/hooks and bridge events; channel-agnostic-inbound/outbound own event/send contracts.
