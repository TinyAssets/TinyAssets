## Context

The September18 scheduled probe35295447166 passed handshake, tools and wiki,
but its activity check selected the stale shared worker heartbeat and its revert
check demanded unavailable private evidence. Actual named per-universe beats
still serve the daemon watchdog and epoch2 queue descriptors; they are not all
retired. This bounded fix addresses platform coordinator liveness only.

## Goals / Non-Goals

Observe actual current execution without leaking tenant data or changing
authority. Preserve the existing five public liveness fields and the difference
between a live idle coordinator and successful user work. No worker restart,
storage schema, aggregate revert evidence or new public action.

## Decisions

Register actual started consumer objects in a process-local weak registry,
scoped to their canonical data root. Track successful poll completion under a
small lock. A live thread with progress within max(300s,poll interval+120s)
is alive; a blocked/error-only poll eventually becomes stale. Aggregate the
worst expected current coordinator, never the freshest file. Remove a stopped
instance only after its coordinator thread exits; a timed-out stop stays visible.

Keep api.universe's scoped worker entries and all heartbeat writers unchanged.
The platform projection is deliberately a distinct observation of the runtime
the HTTP server actually started. Engine MCP child processes inherit the enable
flag but cannot inspect parent memory; their existing graph-binding environment
marker selects explicit unknown external_coordinator evidence.

The status response still uses an explicit allowlist. Observation failure is
alive:null and the canary fails loudly. Disabled execution is explicitly absent,
not invented healthy execution. No new persisted inventory or compatibility shim.

## Risks / Trade-offs

Coordinator progress is not proof of every universe's successful work. Existing
activity/has-work evidence remains separate. The per-universe summary's historical
file preference and private revert evidence gap remain open. The shell watchdog
is unchanged. Child status cannot independently prove parent health.

## Migration Plan

No migration. Independent exact-head review, required CI, authenticated deployed
revision/canary and rendered app acceptance gate landing. Rollback is a normal
revert/deploy. Lead owns release and browser proof; this builder creates a draft.
