## Why

Rebuilding multi-agent command centers needs reusable spawn/message/task-board primitives and channel routing packaged as data rather than hand-built service code.

## What Changes

- Expose ta spawn and ta message with owner-bound delegation and a revisioned shared task board.
- Publish channel/team templates composed from connection, inbound receiver, outbound operation and topic/mention routing data.

## Capabilities

### New Capabilities

- `agent-team-channel-templates`: Rebuilding multi-agent command centers needs reusable spawn/message/task-board primitives and channel routing packaged as data rather than hand-built service code.

### Modified Capabilities

None; consume the existing owning contracts below.

## Impact

Planning owner: Codex. Branch: `spec/muse-pi-gap-proposals`. Proposal-only follow-up requested 2026-10-04; no product code, deployment or PR. Future implementation: one separately claimed worktree/branch/PR for this intent.

Extend in-platform-agent-systems, command-center-agent-templates, channel-agnostic-inbound/outbound and full-channel-access; existing scheduler and budgets remain owners. resident-agent-processes supplies gateways, harness-control supplies hooks/events, package and recipient-update lanes supply publishing/copy/update.
