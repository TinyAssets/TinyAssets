## Context

Sources: [Muse connection methods](../../../docs/design-notes/2026-10-04-muse-connection-methods.md) and [orchestration analysis](../../../docs/design-notes/2026-10-04-agent-orchestration-comparative-analysis.md). Their evidence labels/caveats remain authoritative for attribution; this is a proposed TinyAssets contract, not a verified claim of existing functionality.

## Goals / Non-Goals

Resident gateways, heartbeats and long-running bots cannot be rebuilt using only short-lived jailed calls. Extend workspace-node process/lease execution, control-plane-scheduler and automation-agent-lease rather than introducing a second scheduler. command-center-harness-control owns settings/hooks and bridge events; channel-agnostic-inbound/outbound own event/send contracts. No platform LLM or provider-specific compute/integration path.

## Decisions

Reconcile historical #4319 / S7 #4292 implementations before coding, adopting compatible process supervision into the current workspace-node path. Store a versioned desired process record under authenticated owner/center: executable argv, jailed cwd, environment references without secrets, revision, stopped/running desired state, restart policy/backoff, resource budget and health check. Never launch on a daemon host or the founder PC; allocate vendor-neutral cloud execution. No shell command constructed from untrusted interpolation and no platform LLM.

One current supervisor lease/generation owns each process. Restart/deploy fences the old executor before issuing a replacement, preserving durable application state while never describing process restart as exactly-once external effects. Process status reports desired/observed state, last exit, heartbeat and retry time; unavailable capacity or exhausted budget is visibly held/failed. Stop, revocation and owner deletion fence the lease and prevent late restart. Logs are owner-scoped and credential-sanitized; model calls require the owning account's bindings.

Heartbeats reuse scheduler occurrences with durable dedupe, timezone and budget semantics. A 30-minute resident heartbeat may run a script without any model call; an agent heartbeat can inspect state and emit a message only when warranted. Gateway ingress/outbound effects use existing receipt/idempotency and approval paths. Persistent does not mean unlimited free resources: owner-set budgets and transparent resource health remain operative. No custom gateway can read another process owner's filesystem or credential slots.

## Migration Plan

Extend existing versioned records and preserve prior data/history. Negotiate unsupported versions visibly before dispatch. Roll out after prerequisites pass; on rollback disable new dispatch, retain records and revocation/recovery controls, and never reactivate stale authority or erase user data.

## Risks / Trade-offs

External availability and partial failures can leave work held: expose current state and receipts; do not invent success. New handles can become confused deputies: resolve authenticated bindings at every dispatch and fence stale generations.

## Acceptance criteria

Supports T1/T3/T10 in command-center-harness-control/design.md. Publish and copy a heartbeat/gateway template into a second account; prove 24/7 with no owner host online, restart recovery, quiet scripted checks and Stop fencing through rendered live proof.

Implementation verification includes affected tests/heavy files, Ruff, strict OpenSpec validation, floor review, deployed-SHA assertion, real-user rendered proof and spec sync. Sandbox/process/network enforcement must pass the Linux oracle; a skip is not acceptance.
