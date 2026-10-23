## Why

N1 and frontier baseline #4624 require reaching the agent through connected channels with devices off. The existing webhook receiver bypasses the one `session.start` path and does not define signature verification for connector packages.

## What Changes

- Converge the existing `/hooks/inbound/{binding}` door: map the opaque route ID to one owner, verify declared proof without parsing payloads, wake and forward; parse and start the session inside that box.
- Package Slack, Teams, WhatsApp, connected-account email and generic webhook behavior; keep credentials in broker custody and replies on the egress proxy.
- **BREAKING:** replace direct branch/source-event dispatch with durable box-local acceptance followed by `session.start`; no legacy dispatcher or alternate ingress survives cutover. Existing generic senders must add event ID/timestamp headers. Secret-dependent verification explicitly requires the broker even for awake boxes.
- Extend C2's folded wake-trigger contract, whose actual active directory is `automation-event-triggers`; do not create another scheduler or event bus.

## Capabilities

### New Capabilities
- `inbound-door`: verified external deliveries enter the existing session and wake machinery with box-local processing and idempotency.

### Modified Capabilities
- None in the as-built main specs. This change refines the pending `automation-event-triggers/specs/inbound-webhook-triggers` delivery contract; its management operations remain, while N1 replaces its direct branch enqueue semantics at implementation/sync.

## Impact

Spec-only PR, owner: N1 lane, branch `spec/n1-inbound-door`. Implementation depends on C7/C8 and the binding T1-T10 table in `owner-daemon-in-box/design.md`. Shared changes are limited to router authentication, binding metadata and broker verification; the design names the exact additions and deletions. No production access, deployment or main-spec sync in this lane.
