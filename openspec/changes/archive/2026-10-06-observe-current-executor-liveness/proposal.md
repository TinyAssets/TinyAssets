## Why

The authenticated activity probe treats a historical shared worker heartbeat as
the current executor even while the daemon-owned coordinator is running.
Current lifecycle evidence must distinguish that artifact from a dead or stalled
coordinator without hiding missing execution or exposing tenant details.

## What Changes

- Observe actual started coordinators in the current daemon process and their
  successful poll progress, preserving the five private-free liveness fields.
- Refuse healthy classification for a missing, stopped, stalled or unknown
  expected coordinator; retire a stopped instance only after its thread exits.
- Preserve per-universe heartbeat writers/readers and existing host watchdogs.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `uptime-and-alarms`: current-executor lifecycle observation for platform status.

## Impact

AssignedQueueConsumer lifecycle, platform get_status projection and activity
canary diagnostics. No persistence, grant, MCP handle, deployment workflow,
memory-policy or user-workflow change. The separately unresolved revert probe
remains red rather than exposing private activity or fabricating evidence.

Owner: Codex; branch: codex/current-executor-liveness. This is the narrow
monitoring slice cleared by the lead's September18 Fable architecture review.
The user explicitly authorized parallel MVP builders; installer3868 is parked.
